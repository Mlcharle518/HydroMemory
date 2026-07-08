"""Wall-clock tick scheduler — periodically calls ``Engine.tick()``.

Optional, opt-in companion to the engine. The scheduler owns a single daemon
thread that wakes every ``config.cycle_tick_seconds`` and advances idle-time
decay across every stored droplet.

The scheduler builds and owns its **own** Engine in its worker thread (rather
than borrowing one from the caller) because the reference SQLite repo uses
``check_same_thread=True`` connections — the safe pattern is one engine per
thread, sharing the underlying database file. Tests and the CLI can keep
their own engine for inspection/absorb on the main thread.

``Engine.tick`` itself is deterministic given ``now`` and remains independently
unit-testable; this module only owns the cadence and the lifecycle.
"""
from __future__ import annotations

import threading
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from hydromemory.config import HydroConfig


class TickScheduler:
    """A background thread that builds an Engine, calls ``tick()`` on a fixed
    interval, and tears the engine down on stop. Re-entrant: ``start()`` is a
    no-op if already running; ``stop()`` is safe to call repeatedly."""

    def __init__(
        self,
        config: HydroConfig,
        *,
        interval_seconds: float | None = None,
        on_tick: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.config = config
        self.interval_seconds = (
            interval_seconds if interval_seconds is not None else config.cycle_tick_seconds
        )
        self._on_tick = on_tick
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_stats: dict[str, Any] | None = None
        self._last_error: BaseException | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="hydromem-tick", daemon=True)
        self._thread.start()

    def stop(self, *, timeout: float | None = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None

    @property
    def last_stats(self) -> dict[str, Any] | None:
        return self._last_stats

    @property
    def last_error(self) -> BaseException | None:
        return self._last_error

    def _run(self) -> None:
        from hydromemory.engine import build_engine

        engine = build_engine(self.config)
        try:
            # Run immediately so the first tick doesn't wait a whole interval; then
            # use the Event to coalesce sleep + stop into one wait.
            while True:
                try:
                    stats = engine.tick()
                    self._last_stats = stats
                    self._last_error = None
                    if self._on_tick is not None:
                        self._on_tick(stats)
                except Exception as exc:  # noqa: BLE001 -- one bad tick should not kill the loop
                    self._last_error = exc
                if self._stop.wait(self.interval_seconds):
                    return
        finally:
            engine.close()

    def __enter__(self) -> TickScheduler:
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()
