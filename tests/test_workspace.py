"""Global workspace (ADR-0053): the capacity-limited shared coordination store.

Covers the five global-workspace properties as behavior:

* CAPACITY — the cap is enforced in code at ``promote`` time (an insert into a
  full store forces an eviction), never by convention.
* COMPETITION — the admission floor + score gate decide entry; most traffic never
  enters, and a full store evicts its weakest to admit a stronger newcomer.
* EVICTION ORDER — lowest-score-first, pinned entries exempt; evictions are
  archived, never silently deleted.
* BROADCAST — every mutation publishes a WORKSPACE_UPDATED event; the reader
  composes over the resident set so a promoted item redirects the answer.
* TOP-DOWN — attend/pin/release/clear/suppress let the operator set the contents.
"""
from __future__ import annotations

from hydromemory.bus.bus import EventBus
from hydromemory.bus.emit import Emitter
from hydromemory.bus.events import EventType
from hydromemory.config import HydroConfig
from hydromemory.engine import build_engine
from hydromemory.governance import AgentIdentity
from hydromemory.schema import Droplet, Permissions, State, Visibility
from hydromemory.workspace import (
    ADMISSION_FLOOR,
    OUTCOME_ADMISSION_FLOOR,
    WORKSPACE_CAPACITY,
    Workspace,
    WorkspaceEntry,
    admit_distilled_outcome,
    outcome_promotion_score,
)


def _d(did: str, content: str = "x", *, purity: float = 0.9) -> Droplet:
    return Droplet(id=did, content=content, state=State(purity=purity))


def _ws(**kwargs) -> tuple[Workspace, dict[str, Droplet]]:
    """A workspace over an in-memory droplet map (no persistence, no bus)."""
    store: dict[str, Droplet] = {}
    ws = Workspace(get_droplet=store.get, **kwargs)
    return ws, store


# --- CAPACITY: the cap is enforced in code ----------------------------------
def test_capacity_cap_enforced_at_write_time():
    ws, store = _ws(capacity=3)
    for i in range(6):
        d = _d(f"d{i}")
        store[d.id] = d
        # ascending scores, all above the floor.
        ws.promote(d, 0.5 + i * 0.05)
    # Never exceeds the cap regardless of how many promotions were attempted.
    assert len(ws) == 3
    assert len(ws.entries) <= ws.capacity


def test_default_capacity_is_small():
    # The store is a *small* standing set, not a second recall window.
    assert WORKSPACE_CAPACITY <= 12
    ws, store = _ws()
    for i in range(WORKSPACE_CAPACITY + 5):
        d = _d(f"d{i}")
        store[d.id] = d
        ws.promote(d, 0.9)
    assert len(ws) == WORKSPACE_CAPACITY


# --- COMPETITION: the admission rule ----------------------------------------
def test_below_admission_floor_never_enters():
    ws, store = _ws()
    d = _d("weak")
    store[d.id] = d
    assert ws.promote(d, ADMISSION_FLOOR - 0.01) is False
    assert "weak" not in ws


def test_at_or_above_floor_enters():
    ws, store = _ws()
    d = _d("ok")
    store[d.id] = d
    assert ws.promote(d, ADMISSION_FLOOR + 0.01) is True
    assert "ok" in ws


def test_full_store_evicts_weakest_for_a_stronger_newcomer():
    ws, store = _ws(capacity=2)
    for did, score in (("a", 0.5), ("b", 0.6)):
        store[did] = _d(did)
        ws.promote(store[did], score)
    # A stronger newcomer displaces the weakest resident ("a", 0.5).
    store["c"] = _d("c")
    assert ws.promote(store["c"], 0.9) is True
    assert set(ws.ids()) == {"b", "c"}
    assert "a" not in ws


def test_repromote_updates_score_without_duplicating():
    ws, store = _ws()
    d = _d("dup")
    store[d.id] = d
    ws.promote(d, 0.4)
    ws.promote(d, 0.8)
    assert ws.ids().count("dup") == 1
    assert ws.entries[0].score == 0.8  # keeps the strongest score seen


# --- EVICTION ORDER + archive -----------------------------------------------
def test_eviction_is_lowest_score_first():
    ws, store = _ws(capacity=3)
    for did, score in (("lo", 0.4), ("mid", 0.6), ("hi", 0.8)):
        store[did] = _d(did)
        ws.promote(store[did], score)
    store["new"] = _d("new")
    ws.promote(store["new"], 0.7)  # evicts the lowest ("lo")
    assert "lo" not in ws
    assert set(ws.ids()) == {"mid", "hi", "new"}


def test_pinned_entries_are_exempt_from_eviction():
    ws, store = _ws(capacity=2)
    store["pin"] = _d("pin")
    ws.promote(store["pin"], 0.4)
    ws.pin("pin")
    store["b"] = _d("b")
    ws.promote(store["b"], 0.6)
    # Store is full (pin + b). A stronger newcomer evicts the unpinned "b", not the pin.
    store["c"] = _d("c")
    ws.promote(store["c"], 0.9)
    assert "pin" in ws  # pinned survived despite being the lowest score
    assert "b" not in ws


def test_full_pinned_store_rejects_newcomer():
    ws, store = _ws(capacity=1)
    store["pin"] = _d("pin")
    ws.promote(store["pin"], 0.4)
    ws.pin("pin")
    store["c"] = _d("c")
    assert ws.promote(store["c"], 0.99) is False  # no unpinned victim available
    assert ws.ids() == ["pin"]


def test_eviction_archives_never_deletes():
    archived: list[str] = []
    store: dict[str, Droplet] = {}
    ws = Workspace(get_droplet=store.get, archive=lambda d: archived.append(d.id), capacity=1)
    store["a"] = _d("a")
    ws.promote(store["a"], 0.5)
    store["b"] = _d("b")
    ws.promote(store["b"], 0.9)  # evicts "a" -> archived, not dropped
    assert archived == ["a"]


# --- BROADCAST --------------------------------------------------------------
def test_mutation_broadcasts_workspace_updated():
    seen: list[tuple[str, str | None]] = []
    bus = EventBus()  # no repo -> topic-only events (workspace events carry droplet_id)
    bus.subscribe(
        topics=frozenset({EventType.WORKSPACE_UPDATED.value}),
        handler=lambda e: seen.append((e.payload.get("action"), e.droplet_id)),
    )
    store: dict[str, Droplet] = {}
    ws = Workspace(get_droplet=store.get, emit=Emitter(bus, actor="test"), capacity=1)
    store["a"] = _d("a")
    ws.promote(store["a"], 0.6)          # -> "promote"
    store["b"] = _d("b")
    ws.promote(store["b"], 0.9)          # -> "evicted" (a) + "promote" (b)
    actions = [a for a, _ in seen]
    assert "promote" in actions
    assert "evicted" in actions


# --- REPORTABILITY ----------------------------------------------------------
def test_report_verbalizes_resident_set():
    ws, store = _ws()
    store["r"] = _d("r", "the runbook is in the wiki")
    ws.promote(store["r"], 0.8)
    report = ws.report()
    assert len(report) == 1
    assert report[0].droplet_id == "r"
    assert report[0].score == 0.8
    assert report[0].meta["workspace"]["pinned"] is False


# --- TOP-DOWN CONTROL -------------------------------------------------------
def test_clear_keeps_pins_unless_included():
    ws, store = _ws()
    for did in ("a", "b", "c"):
        store[did] = _d(did)
        ws.promote(store[did], 0.7)
    ws.pin("b")
    assert ws.clear() == 2          # a + c cleared, b (pinned) kept
    assert ws.ids() == ["b"]
    assert ws.clear(include_pinned=True) == 1  # now b too
    assert len(ws) == 0


def test_suppress_evicts_and_bars_reentry():
    ws, store = _ws()
    store["s"] = _d("s")
    ws.promote(store["s"], 0.8)
    ws.suppress("s")
    assert "s" not in ws
    # A suppressed droplet cannot re-enter by ordinary (salience) promotion.
    assert ws.promote(store["s"], 0.99) is False
    ws.unsuppress("s")
    assert ws.promote(store["s"], 0.99) is True


# --- PERSISTENCE (WorkspaceStore round-trip) --------------------------------
def test_workspace_persists_across_reopen(tmp_path):
    cfg = HydroConfig(db_path=str(tmp_path / "ws.db"), vector_dim=64, intelligence_backend="stub")
    eng = build_engine(cfg)
    try:
        d = eng.verbs.absorb("attend me", state=State(purity=0.9))
        assert eng.verbs.attend(d, agent=AgentIdentity(name="operator"), pin=True).result is True
        assert d.id in eng.workspace
    finally:
        eng.close()
    # Reopen: the workspace table restored the pinned resident.
    eng2 = build_engine(cfg)
    try:
        assert d.id in eng2.workspace
        assert eng2.workspace.entries[0].pinned is True
    finally:
        eng2.close()


# --- INTEGRATION: a promoted item redirects a consumer (Engine.answer) ------
def test_pinned_workspace_item_redirects_the_answer(tmp_path):
    """A pinned droplet the query would NOT recall still steers ``answer``.

    This is the load-bearing broadcast test: the reader composes over the shared
    workspace set, so an operator-attended memory redirects the consumer's output
    even when raw similarity buried or excluded it.
    """
    cfg = HydroConfig(db_path=str(tmp_path / "ans.db"), vector_dim=64, intelligence_backend="stub")
    eng = build_engine(cfg)
    try:
        # The memory that matches the query.
        eng.verbs.absorb("The deploy command is shipit --prod.", state=State(purity=0.9, fluidity=0.6))
        # An UNRELATED memory the deploy query would never surface on similarity.
        off = eng.verbs.absorb("Pluto is a dwarf planet.", state=State(purity=0.9))

        # Baseline: the deploy query does not *lead* with Pluto — similarity ranks
        # the deploy fact ahead of it, so the extractive answer is the deploy fact.
        out_baseline = eng.answer("how do I deploy to prod", traverse=False)
        assert "shipit" in out_baseline.answer
        assert "Pluto" not in out_baseline.answer

        # Operator attends + pins the unrelated memory into the workspace.
        assert eng.verbs.attend(off, agent=AgentIdentity(name="operator"), pin=True).result is True

        # Now the same query's answer sees the pinned memory FIRST — it redirects.
        seen_after: dict[str, list[str]] = {}

        def capture2(query: str, items: list[str]) -> str:
            seen_after["items"] = items
            return f"{items[0]} [1]"

        out = eng.answer("how do I deploy to prod", traverse=False, composer=capture2)
        assert seen_after["items"][0] == "Pluto is a dwarf planet."  # workspace item leads
        assert "Pluto" in out.answer  # the extractive answer is redirected by the pin
    finally:
        eng.close()


# --- INTEGRATION: recall promotes survivors into the workspace --------------
def test_recall_promotes_top_survivors(tmp_path):
    cfg = HydroConfig(db_path=str(tmp_path / "rec.db"), vector_dim=64, intelligence_backend="stub")
    eng = build_engine(cfg)
    try:
        eng.verbs.absorb(
            "The deploy command is shipit --prod.",
            state=State(purity=0.95, gravity=0.6, confidence=0.9),
        )
        assert len(eng.workspace) == 0
        results = eng.recall(
            "how do I deploy to prod",
            agent=AgentIdentity(name="assistant"),
        )
        assert results  # recall surfaced the deploy memory
        # Its strongest survivor now holds a workspace seat (broadcast to consumers).
        assert eng.workspace.ids()
        assert results[0].droplet_id in eng.workspace
    finally:
        eng.close()


# --- WorkspaceEntry serialization round-trip --------------------------------
def test_entry_round_trips_through_dict():
    e = WorkspaceEntry(droplet_id="z", score=0.42, source_agent="capture", pinned=True)
    back = WorkspaceEntry.from_dict(e.to_dict())
    assert back.droplet_id == "z"
    assert back.score == 0.42
    assert back.source_agent == "capture"
    assert back.pinned is True


# --- admission respects permission (attend is governance-checked) -----------
def test_attend_denied_by_governance_does_not_enter(tmp_path):
    cfg = HydroConfig(db_path=str(tmp_path / "gov.db"), vector_dim=64, intelligence_backend="stub")
    eng = build_engine(cfg)
    try:
        # A private droplet owned by user, restricted to a different agent.
        d = eng.verbs.absorb("secret note")
        d.permissions = Permissions(
            owner="user", visibility=Visibility.PRIVATE, allowed_agents=["someone_else"]
        )
        eng.repo.upsert(d)
        stranger = AgentIdentity(name="intruder")
        resp = eng.verbs.attend(d, agent=stranger)
        assert resp.result is False
        assert d.id not in eng.workspace
    finally:
        eng.close()


# ============================================================================
# TOOL-OUTPUT GATING (ADR-0053): DISTILL is the OSS's strongest "executed
# outcome" seam. Only a consequential distilled principle competes for a
# workspace seat; a thin/routine one bypasses; and only the compact reference
# (the principle's droplet id) enters — never the joined source payload.
# ============================================================================


def _principle(did: str, *, gravity: float, purity: float, confidence: float, fan_in: int = 2) -> Droplet:
    """A stand-in for a DISTILL output droplet (a consolidated principle)."""
    d = Droplet(
        id=did,
        content="consolidated principle text",
        state=State(purity=purity, gravity=gravity, confidence=confidence),
    )
    d.meta["distilled_from"] = [f"src{i}" for i in range(fan_in)]
    return d


def test_gating_admits_a_consequential_distilled_outcome():
    ws, store = _ws()
    p = _principle("strong", gravity=0.8, purity=0.95, confidence=0.9, fan_in=3)
    store[p.id] = p
    # A high-consequence consolidation clears the outcome floor and competes in.
    assert outcome_promotion_score(p) >= OUTCOME_ADMISSION_FLOOR
    assert admit_distilled_outcome(ws, p) is True
    assert p.id in ws


def test_gating_lets_a_routine_outcome_bypass():
    ws, store = _ws()
    thin = _principle("thin", gravity=0.1, purity=0.3, confidence=0.2, fan_in=1)
    store[thin.id] = thin
    # A thin, low-consequence distillation scores below the floor and never enters.
    assert outcome_promotion_score(thin) < OUTCOME_ADMISSION_FLOOR
    assert admit_distilled_outcome(ws, thin) is False
    assert thin.id not in ws


def test_gating_stores_only_the_compact_reference_not_the_payload():
    ws, store = _ws()
    p = _principle("ref", gravity=0.8, purity=0.95, confidence=0.9, fan_in=3)
    store[p.id] = p
    assert admit_distilled_outcome(ws, p) is True
    # The entry holds only the droplet id + score/pin metadata — a reference, never
    # the consolidated content or the joined source payload.
    entry = ws.entries[0]
    assert entry.droplet_id == "ref"
    entry_fields = set(entry.to_dict().keys())
    assert entry_fields == {"droplet_id", "score", "source_agent", "pinned", "entered_at"}
    # None of the persisted fields carry the principle text.
    assert all("consolidated principle text" not in str(v) for v in entry.to_dict().values())


def test_gating_wired_into_the_distill_verb(tmp_path):
    """The gate is a REAL auto-caller at the distill chokepoint, not a dormant seam."""
    cfg = HydroConfig(db_path=str(tmp_path / "distill.db"), vector_dim=64, intelligence_backend="stub")
    eng = build_engine(cfg)
    try:
        a = eng.verbs.absorb("deploy uses shipit prod", state=State(purity=0.9, gravity=0.7, confidence=0.9))
        b = eng.verbs.absorb("deploy also runs migrations", state=State(purity=0.9, gravity=0.7, confidence=0.9))
        assert len(eng.workspace) == 0
        principle = eng.verbs.distill([a, b])
        # Distilling through the verb auto-competes the consequential outcome in.
        assert principle.id in eng.workspace
    finally:
        eng.close()


def test_gating_outcome_faces_a_stricter_bar_than_ordinary_recall():
    # The outcome floor sits above the ordinary admission floor: an executed
    # outcome must be *more* consequential than a routine recall survivor to enter.
    assert OUTCOME_ADMISSION_FLOOR > ADMISSION_FLOOR


# ============================================================================
# REHYDRATION (ADR-0053): tier-2 <- tier-3. An evicted-but-re-activated item is
# brought back from the workspace archive at its last-held salience, re-competing
# for its slot (never forced in). Skips already-live and never-archived ids.
# ============================================================================


def test_rehydrate_restores_a_previously_held_evicted_item():
    ws, store = _ws(capacity=1)
    store["a"] = _d("a")
    store["b"] = _d("b")
    ws.promote(store["a"], 0.6)           # a holds the single seat
    ws.promote(store["b"], 0.9)           # b evicts a -> a is archived (tier-3)
    assert "a" not in ws and "b" in ws
    # A later context frees the seat, then re-activates a: rehydration brings it back.
    ws.clear()                            # b leaves (also archived)
    restored = ws.rehydrate(["a"])
    assert restored == ["a"]
    assert "a" in ws
    # It re-entered at its last-held score (0.6), not reset to zero.
    assert ws.entries[0].score == 0.6


def test_rehydrate_skips_already_live_ids():
    ws, store = _ws()
    store["live"] = _d("live")
    ws.promote(store["live"], 0.7)
    # Already resident -> rehydration is a no-op for it (nothing to restore).
    assert ws.rehydrate(["live"]) == []
    assert ws.ids().count("live") == 1


def test_rehydrate_skips_never_archived_ids():
    ws, store = _ws()
    store["ghost"] = _d("ghost")
    # Never held a seat, so never archived -> nothing in tier-3 to pull back.
    assert ws.rehydrate(["ghost"]) == []
    assert "ghost" not in ws


def test_rehydrate_still_competes_for_its_slot_when_full():
    # A rehydrated item is NEVER forced in: it re-competes through promote, so a
    # full store of stronger residents refuses it.
    ws, store = _ws(capacity=2)
    for did, score in (("x", 0.5), ("y", 0.6)):
        store[did] = _d(did)
        ws.promote(store[did], score)
    # Evict x (weak) with a stronger newcomer -> x lands in the archive at 0.5.
    store["z"] = _d("z")
    ws.promote(store["z"], 0.9)
    assert "x" not in ws and set(ws.ids()) == {"y", "z"}
    # Store is now full of {y:0.6, z:0.9}, both stronger than x's archived 0.5.
    # Rehydrating x must lose the competition (its 0.5 beats no resident).
    restored = ws.rehydrate(["x"])
    assert restored == []
    assert "x" not in ws
    assert set(ws.ids()) == {"y", "z"}


def test_rehydrate_wins_a_slot_when_store_has_room():
    ws, store = _ws(capacity=3)
    for did, score in (("x", 0.5), ("y", 0.6)):
        store[did] = _d(did)
        ws.promote(store[did], score)
    store["z"] = _d("z")
    ws.promote(store["z"], 0.9)           # full at 3
    ws.suppress("y")                       # frees a seat; y archived
    ws.unsuppress("y")
    # There is now room (2 of 3). Rehydrating y re-seats it at its last-held 0.6.
    restored = ws.rehydrate(["y"])
    assert restored == ["y"]
    assert "y" in ws


def test_rehydrate_wired_into_recall_across_eviction(tmp_path):
    """Recall is the real rehydration chokepoint: an evicted item re-recalled comes back."""
    cfg = HydroConfig(db_path=str(tmp_path / "rehy.db"), vector_dim=64, intelligence_backend="stub")
    eng = build_engine(cfg)
    try:
        d = eng.verbs.absorb(
            "The deploy command is shipit --prod.",
            state=State(purity=0.95, gravity=0.6, confidence=0.9),
        )
        # A recall seats it, then an operator clears the workspace (it lands in the
        # archive). Re-recalling the same query rehydrates it from tier-3.
        eng.recall("how do I deploy to prod", agent=AgentIdentity(name="assistant"))
        assert d.id in eng.workspace
        eng.workspace.clear(include_pinned=True)
        assert d.id not in eng.workspace
        results = eng.recall("how do I deploy to prod", agent=AgentIdentity(name="assistant"))
        assert results
        # It is live again — either re-promoted as a fresh survivor or rehydrated
        # from the archive; both are the workspace surviving eviction across turns.
        assert d.id in eng.workspace
    finally:
        eng.close()


def test_rehydrate_persists_archive_across_reopen(tmp_path):
    """The archive ledger is durable: an item evicted in one session rehydrates in the next."""
    cfg = HydroConfig(db_path=str(tmp_path / "persist.db"), vector_dim=64, intelligence_backend="stub")
    eng = build_engine(cfg)
    did = None
    try:
        d = eng.verbs.absorb("durable fact", state=State(purity=0.9, gravity=0.6, confidence=0.9))
        did = d.id
        assert eng.verbs.attend(d, agent=AgentIdentity(name="operator")).result is True
        assert did in eng.workspace
        eng.workspace.clear(include_pinned=True)   # evict -> archive ledger row
        assert did not in eng.workspace
    finally:
        eng.close()
    # Reopen: the archive row survived, so rehydration restores the evicted item.
    eng2 = build_engine(cfg)
    try:
        assert did not in eng2.workspace
        restored = eng2.workspace.rehydrate([did])
        assert restored == [did]
        assert did in eng2.workspace
    finally:
        eng2.close()
