"""The HydroCognitive developer SDK client (Master Spec §22 MVP-7 / §25).

:class:`HydroClient` is a thin, ergonomic surface over a fully-wired
:class:`~hydromemory.engine.Engine`. It exposes the §18 canonical protocol verbs
uniformly (resolve-and-dispatch via :func:`~hydromemory.canonical.verbs.resolve_verb`),
projects/validates any layer object against the canonical §8 JSON Schemas
(:mod:`hydromemory.canonical.jsonschema`), and bridges to the unified cognitive
bus (:mod:`hydromemory.cognitive_bus`) — so an external developer can drive the
whole 9-layer stack through one object.

The client deliberately *reuses* the existing modules and adds no new policy: a
verb call resolves to the live bound layer method (so nothing is renamed, and a
disabled layer simply does not resolve), and a clear :class:`SdkError` names the
owning layer when a verb is unavailable. It is dependency-light — it imports only
``hydromemory.canonical``, ``hydromemory.config``, ``hydromemory.engine`` and the
cognitive-bus types — and never touches a layer schema directly.
"""
from __future__ import annotations

from typing import Any

from hydromemory.canonical.projection import to_canonical
from hydromemory.canonical.verbs import VERB_REGISTRY, CanonicalVerb, resolve_verb
from hydromemory.cognitive_bus import CognitiveSubscription
from hydromemory.config import HydroConfig
from hydromemory.engine import Engine, build_engine


class SdkError(Exception):
    """Raised when the SDK cannot satisfy a request.

    The most common cause is calling a canonical verb whose owning layer is
    disabled or unbuilt on the held engine (the message names the layer from
    :data:`~hydromemory.canonical.verbs.VERB_REGISTRY`).
    """


class HydroClient:
    """One ergonomic surface over the 9-layer HydroCognitive stack (§22 MVP-7).

    Construct with a :class:`~hydromemory.config.HydroConfig` (the SDK builds the
    :class:`~hydromemory.engine.Engine`) or pass a pre-built ``engine`` to wrap an
    existing instance. The canonical verbs are reached uniformly through
    :meth:`verb` (or the named helpers that delegate to it); :meth:`canonical` /
    :meth:`validate` project and check any layer object against the §8 envelope
    schema; :meth:`events` bridges to the unified cognitive bus.
    """

    def __init__(self, config: HydroConfig | None = None, engine: Engine | None = None) -> None:
        if engine is not None:
            self._engine = engine
            self._owns_engine = False
        else:
            self._engine = build_engine(config)
            self._owns_engine = True

    # ------------------------------------------------------------------ #
    # Engine access
    # ------------------------------------------------------------------ #
    @property
    def engine(self) -> Engine:
        """The wrapped :class:`~hydromemory.engine.Engine` (escape hatch)."""
        return self._engine

    # ------------------------------------------------------------------ #
    # Canonical verb dispatch (§18)
    # ------------------------------------------------------------------ #
    def verb(self, name: CanonicalVerb | str, *args: Any, **kwargs: Any) -> Any:
        """Resolve the canonical ``name`` against the engine and call it.

        Resolves the verb to the live bound layer methods via
        :func:`~hydromemory.canonical.verbs.resolve_verb` and invokes the **first**
        one (the spec's preferred method) with ``*args`` / ``**kwargs``. Raises
        :class:`SdkError` — naming the owning layer — when the verb does not
        resolve because that layer is disabled or unbuilt on this engine.
        """
        verb = self._coerce_verb(name)
        bound = resolve_verb(verb, self._engine)
        if not bound:
            layer = VERB_REGISTRY[verb].layer
            raise SdkError(
                f"Canonical verb {verb.value!r} is unavailable: its layer {layer!r} is "
                f"disabled or unbuilt on this engine. Enable it in HydroConfig "
                f"(e.g. the matching *_enabled flag) and rebuild the client."
            )
        return bound[0](*args, **kwargs)

    @staticmethod
    def _coerce_verb(name: CanonicalVerb | str) -> CanonicalVerb:
        if isinstance(name, CanonicalVerb):
            return name
        try:
            return CanonicalVerb(str(name))
        except ValueError as exc:  # unknown verb string
            valid = ", ".join(v.value for v in CanonicalVerb)
            raise SdkError(f"Unknown canonical verb {name!r}; expected one of: {valid}") from exc

    # -- thin named helpers (each delegates to verb(...)) --------------- #
    def sense(self, *args: Any, **kwargs: Any) -> Any:
        """SENSE — create an observation event from the environment."""
        return self.verb(CanonicalVerb.SENSE, *args, **kwargs)

    def absorb(self, *args: Any, **kwargs: Any) -> Any:
        """ABSORB — create a memory droplet from experience."""
        return self.verb(CanonicalVerb.ABSORB, *args, **kwargs)

    def recall(self, *args: Any, **kwargs: Any) -> Any:
        """RECALL — surface memory by phase, context, and permission."""
        return self.verb(CanonicalVerb.RECALL, *args, **kwargs)

    def anchor(self, *args: Any, **kwargs: Any) -> Any:
        """ANCHOR — create/update a stable identity/value/boundary record."""
        return self.verb(CanonicalVerb.ANCHOR, *args, **kwargs)

    def form_intent(self, *args: Any, **kwargs: Any) -> Any:
        """FORM_INTENT — create directional intent from memory and identity."""
        return self.verb(CanonicalVerb.FORM_INTENT, *args, **kwargs)

    def judge(self, *args: Any, **kwargs: Any) -> Any:
        """JUDGE — evaluate whether and how to proceed."""
        return self.verb(CanonicalVerb.JUDGE, *args, **kwargs)

    def plan(self, *args: Any, **kwargs: Any) -> Any:
        """PLAN — generate an executable route and contingencies."""
        return self.verb(CanonicalVerb.PLAN, *args, **kwargs)

    def act(self, *args: Any, **kwargs: Any) -> Any:
        """ACT — execute an authorized operation."""
        return self.verb(CanonicalVerb.ACT, *args, **kwargs)

    def reflect(self, *args: Any, **kwargs: Any) -> Any:
        """REFLECT — assess outcome and generate lessons."""
        return self.verb(CanonicalVerb.REFLECT, *args, **kwargs)

    def integrate(self, *args: Any, **kwargs: Any) -> Any:
        """INTEGRATE — commit governed learning updates."""
        return self.verb(CanonicalVerb.INTEGRATE, *args, **kwargs)

    def forget(self, *args: Any, **kwargs: Any) -> Any:
        """FORGET — delete, seal, drain, or compost according to policy."""
        return self.verb(CanonicalVerb.FORGET, *args, **kwargs)

    # ------------------------------------------------------------------ #
    # Canonical projection + validation (§8 / §25)
    # ------------------------------------------------------------------ #
    def canonical(self, obj: Any) -> dict[str, Any]:
        """Project any layer object to the §8 canonical envelope dict.

        Delegates to :func:`~hydromemory.canonical.projection.to_canonical`; raises
        :class:`SdkError` for an object no layer projection covers (instead of the
        bare ``TypeError`` the projection raises).
        """
        return self._to_canonical(obj).to_dict()

    def validate(self, obj: Any) -> list[str]:
        """Project ``obj`` and validate it against its canonical type schema.

        Returns a list of human-readable error strings; an empty list means the
        projected envelope is valid. Validation is pinned to the object's own
        ``object_type`` (via :func:`hydromemory.canonical.jsonschema.validate`).
        """
        # Local import keeps the module import-light and mirrors the engine's lazy style.
        from hydromemory.canonical.jsonschema import validate as _validate

        canonical = self._to_canonical(obj)
        return _validate(canonical.to_dict(), object_type=canonical.object_type)

    @staticmethod
    def _to_canonical(obj: Any) -> Any:
        try:
            return to_canonical(obj)
        except TypeError as exc:
            raise SdkError(str(exc)) from exc

    # ------------------------------------------------------------------ #
    # Introspection
    # ------------------------------------------------------------------ #
    def which_verbs(self) -> dict[str, bool]:
        """Map each :class:`CanonicalVerb` value to whether it resolves on this engine.

        ``True`` means the verb's owning layer is enabled/built and at least one
        bound method is callable; ``False`` means it is unavailable (the same
        condition that makes :meth:`verb` raise).
        """
        return {v.value: bool(resolve_verb(v, self._engine)) for v in CanonicalVerb}

    # ------------------------------------------------------------------ #
    # Unified cognitive bus (§17)
    # ------------------------------------------------------------------ #
    def events(
        self,
        object_types: Any = None,
        subscriber: str | None = None,
    ) -> CognitiveSubscription:
        """Subscribe to the engine's unified cognitive bus; return the subscription.

        ``object_types`` is an optional set of
        :class:`~hydromemory.canonical.envelope.ObjectType` to filter on (``None`` =
        all types); ``subscriber`` is the identity string used by the bus's
        fail-closed envelope gate (``None`` = anonymous, public-only). Raises
        :class:`SdkError` when this engine has no cognitive bus (HydroIntegrate
        disabled). Unsubscribe with ``client.engine.cognitive_bus.unsubscribe(sub)``.
        """
        bus = self._engine.cognitive_bus
        if bus is None:
            raise SdkError(
                "No cognitive bus on this engine: enable HydroIntegrate "
                "(integrate_enabled=True) to publish/subscribe cognitive events."
            )

        def _record(event: Any) -> None:
            received.append(event)

        received: list[Any] = []
        sub = bus.subscribe(
            object_types=object_types,
            subscriber=subscriber,
            handler=_record,
        )
        # Expose a convenience buffer of delivered events on the returned subscription
        # without altering the bus contract (additive attribute only).
        sub.received = received  # type: ignore[attr-defined]
        return sub

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    def close(self) -> None:
        """Close the held engine (and all layer stores) if the SDK built it.

        A client wrapping a caller-supplied engine does not close it — the owner
        retains responsibility for its lifecycle.
        """
        if self._owns_engine:
            self._engine.close()

    def __enter__(self) -> HydroClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


__all__ = ["HydroClient", "SdkError"]
