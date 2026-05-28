# HydroCognitive Developer SDK

The SDK (`hydromemory.sdk`) is the one-surface entry point for external developers
(Master Spec §22 MVP-7 / §25 "HydroCognitive SDK"). `HydroClient` wraps a fully-wired
[`Engine`](architecture.md) and exposes the **§18 canonical protocol verbs** uniformly,
validates objects against the [canonical §8 JSON Schemas](canonical.md), and bridges the
[unified cognitive bus](event-bus.md) (§17) — so you drive the whole 9-layer stack through
one object. It is additive and renames nothing (ADR-0048): a verb call resolves to the live
bound layer method, and a disabled layer simply does not resolve.

## The 12 canonical verbs (§18)

Each verb maps to the layer that owns it. A verb resolves only when that layer is enabled in
`HydroConfig` (the matching `*_enabled` flag) and built on the engine.

| Verb | Layer | Helper | Purpose |
|------|-------|--------|---------|
| `SENSE` | HydroSense | `client.sense(...)` | Create an observation event from the environment. |
| `ABSORB` | HydroMemory | `client.absorb(...)` | Create a memory droplet from experience. |
| `RECALL` | HydroMemory | `client.recall(...)` | Surface memory by phase, context, and permission. |
| `ANCHOR` | HydroIdentity | `client.anchor(...)` | Create/update a stable identity/value/boundary record. |
| `FORM_INTENT` | HydroIntent | `client.form_intent(...)` | Create directional intent from memory and identity. |
| `JUDGE` | HydroJudgment | `client.judge(...)` | Evaluate whether and how to proceed. |
| `PLAN` | HydroPlan | `client.plan(...)` | Generate an executable route and contingencies. |
| `ACT` | HydroAction | `client.act(...)` | Execute an authorized operation. |
| `REFLECT` | HydroReflect | `client.reflect(...)` | Assess outcome and generate lessons. |
| `INTEGRATE` | HydroIntegrate | `client.integrate(...)` | Commit governed learning updates. |
| `SUPERSEDE` | HydroIntegrate | `client.verb("SUPERSEDE", ...)` | Replace stale objects, preserving history. |
| `FORGET` | HydroMemory | `client.forget(...)` | Delete, seal, drain, or compost per policy. |

`SUPERSEDE` has no named helper; reach it via `client.verb(CanonicalVerb.SUPERSEDE, ...)`.

## Quickstart

```python
from hydromemory.config import HydroConfig
from hydromemory.canonical.verbs import CanonicalVerb
from hydromemory.sdk import HydroClient

config = HydroConfig(
    db_path="hydro.db",
    intents_enabled=True,
    judgment_enabled=True,
    # ... enable the layers you need; all default off (ADR-0025)
)

with HydroClient(config) as client:          # builds + owns the engine
    droplet = client.absorb("User prefers depth over summaries.")

    # Introspect which verbs are live on this engine.
    print(client.which_verbs())               # {"SENSE": True, "ABSORB": True, ...}

    # Uniform dispatch: resolve a canonical verb and call the bound layer method.
    intent = client.engine.intents.propose_intent("Refactor storage.")
    judgment = client.verb(CanonicalVerb.JUDGE, intent)
```

`HydroClient(config=None, engine=None)` either **builds** an engine from config (and owns its
lifecycle — `close()` shuts it down) or **wraps** a pre-built `engine` you pass in (then
`close()` is a no-op; you keep ownership). The client is a context manager. The wrapped engine
is always reachable via `client.engine`.

### How `verb` dispatches and errors

`client.verb(name, *args, **kwargs)` resolves `name` (a `CanonicalVerb` or its string) against
the engine via `resolve_verb`, then calls the **first** resolved bound method (the spec's
preferred one) with the passed arguments. The named helpers (`absorb`, `judge`, …) are thin
wrappers over `verb(...)`. If the verb does not resolve — because its layer is disabled or
unbuilt — `verb` raises `SdkError` naming the owning layer:

```python
client.form_intent(statement="...")
# SdkError: Canonical verb 'FORM_INTENT' is unavailable: its layer 'HydroIntent' is
#           disabled or unbuilt on this engine. Enable it in HydroConfig ...
```

An unknown verb string also raises `SdkError`.

## Validate and canonicalize (§8 / §25)

Any built layer object (droplet, intent, judgment, plan, action, reflection, observation,
identity anchor) projects onto the minimum-shared-metadata **§8 envelope**:

```python
client.canonical(droplet)   # -> dict: the §8 envelope (routing/gating/audit metadata)
# {"id": "...", "object_type": "memory", "owner": "user", "permissions": {...}, ...}

client.validate(droplet)    # -> list[str]: schema errors; [] means valid
```

`canonical(obj)` returns the envelope dict (via `to_canonical(obj).to_dict()`); `validate(obj)`
projects and validates against the object's own type schema, returning human-readable error
strings (an empty list is valid). Both raise `SdkError` for an object no layer projection covers.

## Cognitive events (§17)

When HydroIntegrate is enabled the engine carries a unified cognitive bus. Subscribe through
the SDK:

```python
sub = client.events(object_types={ObjectType.MEMORY}, subscriber="user")
# ... layers publish CognitiveEvents as objects move through the stack ...
for event in sub.received:        # convenience buffer of delivered events
    print(event.verb, event.object_id)

client.engine.cognitive_bus.unsubscribe(sub)
```

`events(object_types=None, subscriber=None)` returns the bus `CognitiveSubscription` (with an
added `received` buffer). `object_types` filters by `ObjectType` (`None` = all); `subscriber`
is the identity string for the bus's fail-closed envelope gate (`None` = anonymous, which
receives only `public` objects). It raises `SdkError` when the engine has no cognitive bus
(HydroIntegrate disabled).
