"""Forgetting model (PRD §11): seven ways a memory fades, sinks, or ends.

Each verb is a pure droplet transform — it mutates and returns the same
:class:`Droplet` (so callers can chain or persist the result) — except
:func:`delete`, which returns ``None`` to signal the store should drop the row.

The §11 table and the state/phase/reservoir deltas each verb applies:

============ ================================= ==========================================
Verb         §11 meaning                       Deltas
============ ================================= ==========================================
evaporate    Details fade, pattern remains.    phase liquid->vapor; fluidity/depth down;
                                                temperature down; gist kept in meta.
drain        Memory loses active influence.    pressure/fluidity -> ~0; not recalled
                                                (meta active=False).
sediment     Memory sinks into archive.        reservoir -> groundwater; retention=archived;
                                                depth up, fluidity down.
dissolve     Memory merges into broader        meta merged_into=<id>, dissolved=True;
             pattern.                           integrity down (identity surrendered).
delete       Removed by user command.          returns None (hard removal).
seal         Stored but inaccessible.          reservoir -> glacier; phase ice; meta
                                                sealed=True/accessible=False; fluidity 0.
compost      Becomes a lesson; detail          content -> lesson; meta
             discarded.                         original_detail_discarded=True; depth up.
============ ================================= ==========================================
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from hydromemory.reservoirs import Reservoir
from hydromemory.schema import Droplet, Phase, Retention
from hydromemory.schema import clamp_unit as _clamp

if TYPE_CHECKING:
    from hydromemory.storage.repository import DropletRepository


def evaporate(droplet: Droplet) -> Droplet:
    """Details fade but the pattern remains (§11 Evaporation Forgetting).

    Moves a liquid droplet to ``vapor``, drops fluidity/depth and cools it, and
    preserves the pre-evaporation content as ``meta['gist']`` so the abstracted
    pattern is recoverable. Non-liquid droplets still lose detail but keep their
    phase (already-vapor/cloud memory is not pushed backwards).
    """
    droplet.meta.setdefault("gist", droplet.content)
    if droplet.phase is Phase.LIQUID:
        droplet.phase = Phase.VAPOR
    droplet.state.fluidity = _clamp(droplet.state.fluidity * 0.4)
    droplet.state.depth = _clamp(droplet.state.depth * 0.6)
    droplet.state.temperature = _clamp(droplet.state.temperature * 0.5)
    droplet.meta["evaporated"] = True
    return droplet


def drain(droplet: Droplet) -> Droplet:
    """Memory loses active influence (§11 Drainage Forgetting).

    Drives pressure and fluidity to ~0 so the recall engine no longer surfaces
    it actively, and flags ``meta['active']=False``. Content and reservoir are
    left intact — the memory still exists, it just stops pulling.
    """
    droplet.state.pressure = 0.0
    droplet.state.fluidity = 0.0
    droplet.state.temperature = _clamp(droplet.state.temperature * 0.3)
    droplet.meta["active"] = False
    droplet.meta["drained"] = True
    return droplet


def sediment(droplet: Droplet) -> Droplet:
    """Memory sinks into the archive (§11 Sedimentation).

    Relocates the droplet to the ``groundwater`` reservoir, marks retention
    ``archived``, increases depth and reduces fluidity (it settles, becomes
    slow to recall). Phase becomes ``groundwater`` to match the persisted layer.
    """
    droplet.reservoir = Reservoir.GROUNDWATER
    droplet.phase = Phase.GROUNDWATER
    droplet.permissions.retention = Retention.ARCHIVED
    droplet.state.depth = _clamp(max(droplet.state.depth, 0.5) + 0.3)
    droplet.state.fluidity = _clamp(droplet.state.fluidity * 0.3)
    droplet.meta["sedimented"] = True
    return droplet


def dissolve(droplet: Droplet, into_id: str) -> Droplet:
    """Memory merges into a broader pattern/cluster (§11 Dissolution).

    Records the parent cluster id in ``meta['merged_into']`` and adds it to
    ``links.derived_from``, marks ``meta['dissolved']=True``, and lowers
    integrity (the droplet has surrendered its standalone identity to the
    cluster). Content is retained for provenance.
    """
    droplet.meta["merged_into"] = into_id
    droplet.meta["dissolved"] = True
    if into_id not in droplet.links.derived_from:
        droplet.links.derived_from.append(into_id)
    droplet.state.integrity = _clamp(droplet.state.integrity * 0.5)
    droplet.state.fluidity = _clamp(droplet.state.fluidity * 0.5)
    return droplet


def seal(droplet: Droplet) -> Droplet:
    """Memory remains stored but inaccessible (§11 Sealing; §12 Example D).

    Freezes the droplet into the ``glacier`` reservoir as ``ice``, marks
    ``meta['sealed']=True`` and ``meta['accessible']=False``, and zeroes
    fluidity. High integrity is preserved (sealing is for safekeeping, not
    decay); thawing it later requires the §10 consent/thaw protocol.
    """
    droplet.reservoir = Reservoir.GLACIER
    droplet.phase = Phase.ICE
    droplet.state.fluidity = 0.0
    droplet.state.temperature = 0.0
    droplet.meta["sealed"] = True
    droplet.meta["accessible"] = False
    return droplet


def compost(droplet: Droplet, lesson: str) -> Droplet:
    """Memory becomes a lesson; original detail is discarded (§11 Composting).

    Replaces the droplet content with the derived ``lesson`` principle, preserves
    the original detail under ``meta['composted_from']`` for audit, flags
    ``meta['original_detail_discarded']=True``, and deepens the droplet (a
    principle is a settled, structural memory). Purity rises — a clean lesson is
    more reliable than the messy episode it came from.
    """
    droplet.meta["composted_from"] = droplet.content
    droplet.meta["original_detail_discarded"] = True
    droplet.content = lesson
    droplet.state.depth = _clamp(max(droplet.state.depth, 0.6) + 0.2)
    droplet.state.purity = _clamp(max(droplet.state.purity, 0.8))
    droplet.meta["composted"] = True
    return droplet


def delete(droplet: Droplet) -> None:
    """Memory is removed by user command (§11 Deletion).

    Hard removal: returns ``None``. The store is responsible for dropping the
    row keyed by ``droplet.id``; this verb intentionally produces no droplet so
    the call site cannot accidentally re-persist a deleted memory.
    """
    return None


# --- Passive time decay + aged selection (§11 fading; ADR-0032) -------------
@dataclass(frozen=True)
class DecayConfig:
    """Documented defaults for :func:`decay` (opt-in; the default path is off)."""

    salience_factor: float = 0.85  # per idle-cycle multiplier on salience dims
    sediment_floor: float = 0.05  # salience <= this -> suggest 'sediment'
    drain_floor: float = 0.02  # salience <= this -> suggest 'drain'


DEFAULT_DECAY = DecayConfig()

# Default "aged" cut-off for re-verification selection (wall-clock elapsed).
DEFAULT_AGED_MAX_AGE = timedelta(days=7)


def decay(droplet: Droplet, *, idle_cycles: int = 1, config: DecayConfig = DEFAULT_DECAY) -> Droplet:
    """Passively fade a memory's *salience* with idle time (§11 fading; ADR-0032).

    Multiplies the salience dimensions — ``pressure``, ``fluidity``,
    ``temperature`` — by ``salience_factor ** idle_cycles`` so an un-recalled
    memory goes quiet over time.

    CRITICAL INVARIANT: decay NEVER touches ``purity``, ``integrity`` or
    ``confidence``. Those encode epistemic *truth*; salience encodes
    recency/activeness. Fading salience-only is exactly what preserves the
    stale-vs-rare distinction — a rare-but-true memory goes quiet yet stays
    recallable on a strong pull (``purity`` intact, not contaminated), while a
    stale-*false* memory travels the separate contamination path. At/below a
    floor a *demote suggestion* (``drain`` / ``sediment``) is recorded under
    ``meta['decay_suggestion']`` — never applied here, and never ``delete``
    (forgetting fades influence; it does not destroy data without a user command).
    """
    if idle_cycles <= 0:
        return droplet
    factor = config.salience_factor**idle_cycles
    droplet.state.pressure = _clamp(droplet.state.pressure * factor)
    droplet.state.fluidity = _clamp(droplet.state.fluidity * factor)
    droplet.state.temperature = _clamp(droplet.state.temperature * factor)
    droplet.meta["decayed"] = True
    salience = max(droplet.state.pressure, droplet.state.fluidity)
    if salience <= config.drain_floor:
        droplet.meta["decay_suggestion"] = "drain"
    elif salience <= config.sediment_floor:
        droplet.meta["decay_suggestion"] = "sediment"
    return droplet


def select_aged(
    repo: DropletRepository,
    *,
    now: datetime | None = None,
    max_age: timedelta = DEFAULT_AGED_MAX_AGE,
    limit: int = 50,
    include_unverified: bool = True,
) -> list[Droplet]:
    """Select droplets due for re-verification (the real ``aged_droplets``).

    Returns droplets whose ``cycle.last_verified`` is older than ``now - max_age``
    (or never verified, when ``include_unverified``) — replacing the
    ``MeshEngine.aged_droplets`` passthrough with a real store-backed selection
    that feeds the Reflection role's ``reverify``. Reference impl: scans
    ``repo.query()`` and filters in Python (no new index), capped at ``limit``.
    """
    current = now or datetime.now(UTC)
    cutoff = current - max_age
    aged: list[Droplet] = []
    for droplet in repo.query():
        last_verified = droplet.cycle.last_verified
        if last_verified is None:
            if include_unverified:
                aged.append(droplet)
        elif last_verified < cutoff:
            aged.append(droplet)
        if len(aged) >= limit:
            break
    return aged
