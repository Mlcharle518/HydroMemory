"""Global workspace — a capacity-limited shared coordination store (ADR-0053).

The reservoirs, the vault, and the SQLite droplet table are *long-term stores*: a
data lake, unbounded, that every layer queries independently. What the engine has
never had is a **small, shared, standing "active awareness"** — the handful of
droplets that are salient *right now* and that every module should reason over
*together*. The docs reach for it repeatedly ("recall into active awareness", the
volatile WORKING_STREAM, the ephemeral working-set packer) but never reify it.

This module reifies it. A :class:`Workspace` holds an ordered, **capped** list of
:class:`WorkspaceEntry` — the global-workspace primitive from the 2026-07 paper:

1. **Capacity-limited.** :data:`WORKSPACE_CAPACITY` entries, enforced *in code* at
   :meth:`Workspace.promote` time: promoting into a full store forces an eviction.
2. **Competition for entry.** :meth:`Workspace.promote` admits a droplet only when
   its recall score clears the store's :data:`ADMISSION_FLOOR`; when the store is
   full the lowest-scoring *unpinned* entry is evicted to make room (a pinned
   entry is exempt, so a full pinned store rejects the newcomer). The score is the
   §5.6 ``hydro_recall_score`` the pipeline already computes — most traffic never
   competes, and of what does, only the top handful stays resident.
3. **Broadcast + archive.** Every mutation publishes a
   :attr:`~hydromemory.bus.events.EventType.WORKSPACE_UPDATED` event on the bus so
   every subscriber sees the same small set. An evicted entry is *archived*
   (handed to the injected ``archive`` sink — the unbounded droplet store), never
   silently dropped.
4. **Reportability.** :meth:`Workspace.report` renders the resident set via
   :func:`~hydromemory.recall.format_recall`; the CLI (``hydromem workspace``) and
   the server (``GET /workspace``) verbalize it.
5. **Top-down control.** :meth:`Workspace.pin` / :meth:`Workspace.release` /
   :meth:`Workspace.suppress` / :meth:`Workspace.clear` let an operator (or the
   ``attend``/``pin``/``release`` verbs) deliberately set the contents.

The store is deliberately tiny (a few rows), so it is persisted separately from
the droplets table via a pluggable :class:`WorkspaceStore` (the SQLite repo
supplies one; tests get the in-memory default). Persistence is opt-in: with no
store the workspace is a pure in-process object (v1 behavior, byte-identical).
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

from hydromemory.bus.emit import NULL_EMITTER, Emitter
from hydromemory.bus.events import EventType
from hydromemory.recall import RecallResult, format_recall, select_recall_mode
from hydromemory.schema import Droplet

# The capacity cap: a *small* standing set (the paper's "capacity-limited store").
# Enforced in code at promote() time, never by convention. Kept well under the
# ~12-item working-set budget so the workspace is a sharp attentional focus, not a
# second recall window.
WORKSPACE_CAPACITY: int = 7

# The admission floor: a droplet must clear this recall score to *compete* for
# entry at all. Most traffic scores below it and never enters (competition). The
# floor sits just above the lowest §5.6 recall_threshold so anything recall itself
# would surface is eligible, but idle/low-salience droplets are not.
ADMISSION_FLOOR: float = 0.30

# Multiplier applied to an archived entry's last-held score when it is rehydrated
# (ADR-0053, mirroring the reference REHYDRATE_BOOST). A droplet that was evicted
# and is now re-activated (recalled again) held a seat once and is relevant again,
# so it re-competes at a small premium — but must still win a slot like any other
# admission (never forced in). 1.0 = re-compete at exactly its last-held salience.
REHYDRATE_BOOST: float = 1.0

# Tool-output gating (ADR-0053). HydroMemory-OSS has no ACT layer, so its strongest
# "executed outcome" signal is DISTILL — the consolidation verb that produces a new
# durable principle droplet from a cluster (the analog of a consequential tool
# result). Only a *consequential* distilled outcome should compete for the shared
# context, and only its compact reference (the principle's droplet id) enters —
# never the joined source payload. An outcome must clear this floor to compete;
# below it the principle stays in its own layer store and never floods the
# workspace (the "routine / low-impact" bypass). Sits above ADMISSION_FLOOR so an
# outcome faces a stricter bar than an ordinary recall survivor.
OUTCOME_ADMISSION_FLOOR: float = 0.45


@dataclass
class WorkspaceEntry:
    """One resident of the workspace (a droplet id + why it is here)."""

    droplet_id: str
    score: float
    source_agent: str = "system"
    pinned: bool = False
    entered_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict[str, Any]:
        return {
            "droplet_id": self.droplet_id,
            "score": self.score,
            "source_agent": self.source_agent,
            "pinned": self.pinned,
            "entered_at": self.entered_at.astimezone(UTC).isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorkspaceEntry:
        raw = data.get("entered_at")
        entered = (
            datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            if raw
            else datetime.now(UTC)
        )
        if entered.tzinfo is None:
            entered = entered.replace(tzinfo=UTC)
        return cls(
            droplet_id=str(data["droplet_id"]),
            score=float(data.get("score", 0.0) or 0.0),
            source_agent=str(data.get("source_agent", "system")),
            pinned=bool(data.get("pinned", False)),
            entered_at=entered,
        )


@runtime_checkable
class WorkspaceStore(Protocol):
    """Persistence seam for the (tiny) workspace table.

    A store round-trips the ordered entry list, and — for rehydration (ADR-0053,
    mirroring the reference ``workspace_archive`` ledger) — appends every evicted
    entry to an unbounded archive it can look the newest one up in. The SQLite
    repository implements this over a ``workspace`` slot table plus a
    ``workspace_archive`` ledger; the in-memory default keeps both in Python
    attributes (used by tests and the default DI path).
    """

    def load_workspace(self) -> list[WorkspaceEntry]:
        ...

    def save_workspace(self, entries: list[WorkspaceEntry]) -> None:
        ...

    def archive_entry(self, entry: WorkspaceEntry, *, reason: str) -> None:
        ...

    def latest_archived(self, droplet_id: str) -> WorkspaceEntry | None:
        ...


class _MemoryWorkspaceStore:
    """The default in-memory :class:`WorkspaceStore` (no persistence).

    Also keeps an in-process append-only archive ledger so rehydration works
    without a SQLite backend (the test/default DI path).
    """

    def __init__(self) -> None:
        self._entries: list[WorkspaceEntry] = []
        self._archive: list[WorkspaceEntry] = []

    def load_workspace(self) -> list[WorkspaceEntry]:
        return list(self._entries)

    def save_workspace(self, entries: list[WorkspaceEntry]) -> None:
        self._entries = list(entries)

    def archive_entry(self, entry: WorkspaceEntry, *, reason: str) -> None:
        # Store a copy carrying the last-held score/pin so a later rehydration
        # re-competes it at the salience it had when it left the live store.
        self._archive.append(
            WorkspaceEntry(
                droplet_id=entry.droplet_id,
                score=entry.score,
                source_agent=entry.source_agent,
                pinned=entry.pinned,
                entered_at=entry.entered_at,
            )
        )

    def latest_archived(self, droplet_id: str) -> WorkspaceEntry | None:
        for entry in reversed(self._archive):
            if entry.droplet_id == droplet_id:
                return entry
        return None


# An archive sink: given the droplet an eviction displaced, persist it to the
# long-term store (the unbounded droplets table). Eviction never deletes.
ArchiveSink = Callable[[Droplet], None]


class Workspace:
    """A capacity-limited, competition-gated, broadcasting shared store.

    Construction wires the four collaborators, each optional so the workspace is
    testable in isolation and a no-op by default:

    * ``get_droplet`` — resolve an id to a :class:`Droplet` (for report/archive).
    * ``store``       — a :class:`WorkspaceStore` for persistence (default: memory).
    * ``emit``        — the bus :class:`Emitter` for WORKSPACE_UPDATED broadcast.
    * ``archive``     — the :data:`ArchiveSink` an evicted entry is handed to.
    """

    def __init__(
        self,
        *,
        get_droplet: Callable[[str], Droplet | None] | None = None,
        store: WorkspaceStore | None = None,
        emit: Emitter = NULL_EMITTER,
        archive: ArchiveSink | None = None,
        capacity: int = WORKSPACE_CAPACITY,
        admission_floor: float = ADMISSION_FLOOR,
    ) -> None:
        self._get_droplet = get_droplet
        self._store: WorkspaceStore = store or _MemoryWorkspaceStore()
        self.emit = emit
        self._archive = archive
        self.capacity = capacity
        self.admission_floor = admission_floor
        self._entries: list[WorkspaceEntry] = self._store.load_workspace()
        # Ids the operator has suppressed: they cannot re-enter until released.
        self._suppressed: set[str] = set()

    # -- introspection --------------------------------------------------------

    @property
    def entries(self) -> list[WorkspaceEntry]:
        """The resident entries, highest-score first (pinned sort ahead on ties)."""
        return list(self._entries)

    def ids(self) -> list[str]:
        return [e.droplet_id for e in self._entries]

    def __contains__(self, droplet_id: object) -> bool:
        return any(e.droplet_id == droplet_id for e in self._entries)

    def __len__(self) -> int:
        return len(self._entries)

    def _find(self, droplet_id: str) -> WorkspaceEntry | None:
        for e in self._entries:
            if e.droplet_id == droplet_id:
                return e
        return None

    # -- competition + capacity (the core) ------------------------------------

    def promote(
        self,
        droplet: Droplet,
        score: float,
        *,
        source_agent: str = "system",
        pinned: bool = False,
    ) -> bool:
        """Compete ``droplet`` for a workspace slot; return whether it is resident.

        The admission rule (competition): a droplet must clear
        :attr:`admission_floor` on the §5.6 recall ``score`` to enter, and must not
        be operator-suppressed. Re-promoting a resident droplet updates its score
        (keeping the best seen) rather than duplicating it.

        Capacity is enforced **here, in code**: when the store is full the lowest
        unpinned entry is evicted to the archive to make room. If every resident is
        pinned (a full pinned store) the newcomer is rejected — the operator's pins
        win over fresh salience. A ``pinned=True`` promotion always enters (the
        top-down path), evicting if it must.
        """
        did = droplet.id
        if did in self._suppressed and not pinned:
            return False

        existing = self._find(did)
        if existing is not None:
            # Already resident: keep the strongest score + strongest pin/source.
            existing.score = max(existing.score, float(score))
            if pinned:
                existing.pinned = True
            if source_agent != "system":
                existing.source_agent = source_agent
            self._reorder()
            self._commit(reason="update", droplet_id=did)
            return True

        if not pinned and float(score) < self.admission_floor:
            return False  # lost the competition: below the admission floor.

        entry = WorkspaceEntry(
            droplet_id=did, score=float(score), source_agent=source_agent, pinned=pinned
        )

        if len(self._entries) >= self.capacity:
            victim = self._pick_victim(challenger=entry)
            if victim is None:
                return False  # store full of pins and the newcomer isn't stronger.
            self._evict(victim)

        self._entries.append(entry)
        self._reorder()
        self._commit(reason="promote", droplet_id=did)
        return True

    # -- rehydration (tier-2 <- tier-3) ---------------------------------------

    def rehydrate(
        self,
        droplet_ids: list[str],
        *,
        source_agent: str = "rehydrate",
        score_factor: float = REHYDRATE_BOOST,
    ) -> list[str]:
        """Re-admit archived droplets a new context has re-activated (ADR-0053).

        The workspace is ephemeral under pressure — entries get evicted to the
        ``workspace_archive`` ledger. When a later context re-surfaces one of them
        (e.g. it is recalled again), rehydration pulls its last-held state back out
        of the archive and re-competes it for a slot at its archived score (scaled
        by ``score_factor``). This mirrors the reference ``rehydrate``:

        * An id already **live** in the store is skipped (nothing to restore).
        * An id that was **never archived** is skipped (no tier-3 record to pull).
        * Otherwise the archived entry re-enters through :meth:`promote`, so the
          cap and competition still apply — a rehydrated droplet earns its slot
          exactly like a fresh one and is **never forced in**. A full store of
          stronger residents simply refuses it.

        Requires ``get_droplet`` (to resolve the archived id to a live droplet);
        without it rehydration is a no-op. Returns the ids actually re-seated.
        """
        if self._get_droplet is None:
            return []
        live = set(self.ids())
        restored: list[str] = []
        for droplet_id in droplet_ids:
            if droplet_id in live:
                continue  # already resident — nothing to rehydrate.
            archived = self._store.latest_archived(droplet_id)
            if archived is None:
                continue  # never held a seat / never archived — skip.
            droplet = self._get_droplet(droplet_id)
            if droplet is None:
                continue  # the referenced droplet is gone from the long-term store.
            rehydrate_score = float(archived.score) * float(score_factor)
            # Competition guard: a rehydrated item is never *forced* in. On a full
            # store it must out-score the weakest unpinned resident to earn a seat
            # (an unpinned re-activation is not a backdoor past the cap). A pinned
            # archived entry is the top-down path and re-competes as such.
            if not archived.pinned and len(self._entries) >= self.capacity:
                victim = self._pick_victim(challenger=archived)
                if victim is None or rehydrate_score <= victim.score:
                    continue  # loses the competition — stays in the archive.
            entered = self.promote(
                droplet,
                rehydrate_score,
                source_agent=source_agent,
                pinned=archived.pinned,
            )
            if entered:
                restored.append(droplet_id)
        return restored

    def _pick_victim(self, *, challenger: WorkspaceEntry) -> WorkspaceEntry | None:
        """The lowest-scoring unpinned resident, or ``None`` if none may be evicted.

        Pinned entries are exempt from eviction. A pinned challenger may still not
        displace a pinned resident — only unpinned residents are candidates — so a
        fully-pinned store is immovable by promotion (use :meth:`release`/:meth:`clear`).
        """
        unpinned = [e for e in self._entries if not e.pinned]
        if not unpinned:
            return None
        return min(unpinned, key=lambda e: e.score)

    def _evict(self, entry: WorkspaceEntry, *, reason: str = "evicted") -> None:
        """Remove ``entry`` and archive it (never a silent delete).

        Two archive paths, both additive: (1) the injected :data:`ArchiveSink`
        re-persists the referenced *droplet* to the long-term store, and (2) the
        :class:`WorkspaceStore` appends the *slot* (id + last-held score/pin) to its
        ``workspace_archive`` ledger — the tier-3 record :meth:`rehydrate` reads to
        bring an evicted-but-re-activated item back (ADR-0053).
        """
        self._entries = [e for e in self._entries if e.droplet_id != entry.droplet_id]
        if self._archive is not None and self._get_droplet is not None:
            droplet = self._get_droplet(entry.droplet_id)
            if droplet is not None:
                self._archive(droplet)
        self._store.archive_entry(entry, reason=reason)
        self.emit.emit(
            EventType.WORKSPACE_UPDATED,
            droplet_id=entry.droplet_id,
            payload={"action": "evicted", "reason": reason, "score": entry.score, "size": len(self._entries)},
        )

    # -- top-down control -----------------------------------------------------

    def pin(self, droplet_id: str) -> bool:
        """Pin a resident entry so it is exempt from eviction. Returns success."""
        entry = self._find(droplet_id)
        if entry is None:
            return False
        entry.pinned = True
        self._reorder()
        self._commit(reason="pin", droplet_id=droplet_id)
        return True

    def release(self, droplet_id: str) -> bool:
        """Unpin a resident entry (it may now be evicted). Returns success."""
        entry = self._find(droplet_id)
        if entry is None:
            return False
        entry.pinned = False
        self._commit(reason="release", droplet_id=droplet_id)
        return True

    def suppress(self, droplet_id: str) -> None:
        """Evict a droplet if resident and bar it from re-entering until released."""
        self._suppressed.add(droplet_id)
        entry = self._find(droplet_id)
        if entry is not None:
            self._evict(entry, reason="suppressed")
            self._save()

    def unsuppress(self, droplet_id: str) -> None:
        """Lift an operator suppression so the droplet may compete again."""
        self._suppressed.discard(droplet_id)

    def clear(self, *, include_pinned: bool = False) -> int:
        """Clear the workspace (keeping pins unless ``include_pinned``).

        Cleared entries are archived, not deleted. Returns the number removed.
        """
        removed = [e for e in self._entries if include_pinned or not e.pinned]
        for entry in removed:
            self._evict(entry, reason="cleared")
        self._save()
        return len(removed)

    # -- reportability --------------------------------------------------------

    def report(self) -> list[RecallResult]:
        """Verbalize the resident set as rendered :class:`RecallResult` objects.

        Each resident droplet is rendered via :func:`format_recall` in its natural
        recall mode, carrying the workspace score. Entries whose droplet can no
        longer be resolved are skipped. This is the surface the CLI/HTTP expose.
        """
        out: list[RecallResult] = []
        if self._get_droplet is None:
            return out
        for entry in self._entries:
            droplet = self._get_droplet(entry.droplet_id)
            if droplet is None:
                continue
            mode = select_recall_mode(droplet, {})
            result = format_recall(droplet, mode, score=entry.score)
            result.meta["workspace"] = {
                "source_agent": entry.source_agent,
                "pinned": entry.pinned,
                "entered_at": entry.entered_at.astimezone(UTC).isoformat(),
            }
            out.append(result)
        return out

    def to_dict(self) -> dict[str, Any]:
        """A JSON-safe snapshot of the workspace (for ``GET /workspace``)."""
        return {
            "capacity": self.capacity,
            "size": len(self._entries),
            "entries": [e.to_dict() for e in self._entries],
        }

    # -- internals ------------------------------------------------------------

    def _reorder(self) -> None:
        """Keep the entry list score-desc with pinned entries ahead on ties."""
        self._entries.sort(key=lambda e: (e.pinned, e.score), reverse=True)

    def _save(self) -> None:
        self._store.save_workspace(self._entries)

    def _commit(self, *, reason: str, droplet_id: str) -> None:
        """Persist + broadcast a mutation (the broadcast half of the paper)."""
        self._save()
        self.emit.emit(
            EventType.WORKSPACE_UPDATED,
            droplet_id=droplet_id,
            payload={"action": reason, "size": len(self._entries), "ids": self.ids()},
        )


# ---------------------------------------------------------------------------
# Tool-output gating (ADR-0053) — DISTILL is HydroMemory-OSS's strongest
# "executed outcome" seam (no ACT layer). A distilled principle is a large
# consolidation flooding back; only a *consequential* one should compete for the
# shared context, and only its compact reference (the principle droplet's id, which
# is all a WorkspaceEntry ever holds) enters — never the joined source payload.
# ---------------------------------------------------------------------------


def outcome_promotion_score(droplet: Droplet) -> float:
    """Score a distilled outcome by how consequential it is (in ``[0, 1]``).

    Reuses signals the distillation already carries: a principle lands high-purity
    with elevated ``gravity`` (importance/consequence) and ``confidence``, and
    ``distilled_from`` records how many memories it consolidates (a broader
    consolidation is a bigger outcome). Blends importance x confidence with a small
    fan-in bonus. The gate in :func:`admit_distilled_outcome` decides admission
    against :data:`OUTCOME_ADMISSION_FLOOR`; a thin, low-consequence distillation
    scores below it and bypasses the workspace.
    """
    s = droplet.state
    importance = max(float(s.gravity), float(s.purity))
    confidence = float(s.confidence)
    fan_in = len(droplet.meta.get("distilled_from") or [])
    fan_bonus = 0.1 * min(fan_in, 3)  # saturates: a 3+ way merge is already "big".
    val = 0.7 * importance * (0.5 + 0.5 * confidence) + fan_bonus
    return 0.0 if val < 0.0 else (1.0 if val > 1.0 else val)


def admit_distilled_outcome(
    workspace: Workspace | None,
    droplet: Droplet,
    *,
    source_agent: str = "distill",
) -> bool:
    """Gate a distilled principle into the workspace: promote the reference, not the dump.

    Only outcomes clearing :data:`OUTCOME_ADMISSION_FLOOR` compete; the rest bypass
    the workspace entirely (routine/low-impact consolidation stays in its own
    layer store). The admitted entry holds only the principle droplet's id — a
    compact reference — so a large consolidation costs one small slot, and
    admission still enforces cap + competition through :meth:`Workspace.promote`
    (a trivial outcome loses to a live salient resident). Returns whether it was
    seated. A no-op (returns ``False``) when no workspace is wired.
    """
    if workspace is None:
        return False
    score = outcome_promotion_score(droplet)
    if score < OUTCOME_ADMISSION_FLOOR:
        return False  # not consequential enough — bypass the shared context.
    return workspace.promote(droplet, score, source_agent=source_agent)


__all__ = [
    "Workspace",
    "WorkspaceEntry",
    "WorkspaceStore",
    "ArchiveSink",
    "WORKSPACE_CAPACITY",
    "ADMISSION_FLOOR",
    "REHYDRATE_BOOST",
    "OUTCOME_ADMISSION_FLOOR",
    "outcome_promotion_score",
    "admit_distilled_outcome",
]
