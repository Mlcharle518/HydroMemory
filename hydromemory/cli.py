"""HydroMemory CLI — absorb experiences, recall memory, run HQL, and manage the vault.

Usage::

    hydromem --db mem.db absorb --content "..." --source conversation
    hydromem --db mem.db recall "what do you know about my work style"
    hydromem --db mem.db hql 'GET memories WHERE reservoir="groundwater" AND purity>0.8'

    # Vault key management (keys are read from the environment, never argv):
    HYDRO_VAULT_KEY=<new> HYDRO_VAULT_PREV_KEYS=<old> hydromem --db mem.db vault-rotate
    HYDRO_VAULT_KEY=<key> hydromem --db mem.db vault-encrypt
"""
from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from typing import Any

from hydromemory.config import HydroConfig
from hydromemory.engine import build_engine
from hydromemory.governance import AgentIdentity, TrustLevel
from hydromemory.recall import RecallResult
from hydromemory.schema import Droplet, _fmt_dt


def _config_from_args(args: argparse.Namespace) -> HydroConfig:
    cfg = HydroConfig.from_env()
    if args.db:
        cfg.db_path = args.db
    if args.backend:
        cfg.intelligence_backend = args.backend
        cfg.embedding_backend = args.backend
    return cfg


def _agent_from_args(args: argparse.Namespace) -> AgentIdentity:
    trust = TrustLevel(getattr(args, "trust", "approved"))
    return AgentIdentity(name=getattr(args, "agent", None) or "assistant", trust_level=trust)


def _print_decision(decision: dict[str, Any]) -> None:
    print(f"stored:    {decision.get('stored')}")
    print(f"id:        {decision.get('droplet_id')}")
    print(f"phase:     {decision.get('phase')}")
    print(f"reservoir: {decision.get('reservoir')}")
    if decision.get("triggers"):
        print(f"triggers:  {', '.join(decision['triggers'])}")
    dec = decision.get("decision")
    if isinstance(dec, dict) and not dec.get("allowed", True):
        print(f"governance: DENIED — {dec.get('denial_reason')}")


def _render_one(item: Any) -> None:
    if isinstance(item, RecallResult):
        print(f"[{item.mode.value}] score={item.score:.3f} show_to_user={item.show_to_user}")
        if item.surface_text:
            print(f"  surface:  {item.surface_text}")
        print(f"  guidance: {item.internal_guidance}")
    elif isinstance(item, Droplet):
        print(f"{item.id}  phase={item.phase.value}  reservoir={item.reservoir.value}  type={item.memory_type}")
        print(f"  {item.content}")
    elif isinstance(item, dict):
        print(json.dumps(item, indent=2, default=str))
    else:
        print(repr(item))


def _render(obj: Any) -> None:
    if isinstance(obj, list):
        if not obj:
            print("(no results)")
            return
        for i, item in enumerate(obj):
            if i:
                print("-" * 60)
            _render_one(item)
    else:
        _render_one(obj)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hydromem", description="HydroMemory Protocol CLI.")
    parser.add_argument("--db", help="SQLite store path (default: $HYDRO_DB_PATH or hydromemory.db)")
    parser.add_argument("--backend", choices=["stub", "claude"], help="intelligence backend (default: stub)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_absorb = sub.add_parser("absorb", help="capture an experience into memory")
    p_absorb.add_argument("--content", required=True)
    p_absorb.add_argument("--source", default="conversation")
    p_absorb.add_argument("--context", help="JSON object of context (e.g. topic/session_type)")

    for name, help_text in (("recall", "recall memory for a query"), ("hql", "run a Hydro Query Language statement")):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("query")
        p.add_argument("--agent", default="assistant")
        p.add_argument("--trust", choices=["session", "approved", "high_trust"], default="approved")

    p_run = sub.add_parser("run-example", help="run a PRD §12 source example (A-F)")
    p_run.add_argument("name", help="example letter A-F")

    # Vault key management. Keys are read from the environment (HYDRO_VAULT_KEY /
    # HYDRO_VAULT_PREV_KEYS), never passed as args, so secrets stay out of argv.
    sub.add_parser(
        "vault-rotate",
        help="re-encrypt the vault to HYDRO_VAULT_KEY (rotating from HYDRO_VAULT_PREV_KEYS)",
    )
    sub.add_parser(
        "vault-encrypt",
        help="encrypt a previously-keyless vault under HYDRO_VAULT_KEY",
    )

    # Review console (Master Spec §22 MVP-6): inspect/approve/modify/drain/delete durable
    # cognitive updates — the HydroIntegrate reintegration queue + HydroIdentity anchors.
    p_review = sub.add_parser("review", help="inspect and govern durable cognitive updates")
    rsub = p_review.add_subparsers(dest="review_action", required=True)
    rsub.add_parser("pending", help="list reintegrations awaiting user review")
    r_approve = rsub.add_parser("approve", help="approve + commit a reviewed reintegration")
    r_approve.add_argument("reintegration_id")
    r_reject = rsub.add_parser("reject", help="reject a reviewed reintegration")
    r_reject.add_argument("reintegration_id")
    r_log = rsub.add_parser("log", help="list the reintegration audit log")
    r_log.add_argument("--status", help="filter by reintegration status")
    r_log.add_argument("--target", help="filter by target layer")
    r_drain = rsub.add_parser("drain", help="de-weight an applied reintegration")
    r_drain.add_argument("reintegration_id")
    r_rollback = rsub.add_parser("rollback", help="reverse an applied reintegration")
    r_rollback.add_argument("reintegration_id")
    r_rollback.add_argument("--reason", help="why the update is being reversed")
    r_anchors = rsub.add_parser("anchors", help="list identity anchors")
    r_anchors.add_argument("--status", help="filter by anchor status")
    r_anchors.add_argument("--type", dest="anchor_type", help="filter by anchor type")
    r_affirm = rsub.add_parser("affirm", help="confirm a PROVISIONAL anchor -> ACTIVE")
    r_affirm.add_argument("anchor_id")
    r_retire = rsub.add_parser("retire", help="retire an identity anchor")
    r_retire.add_argument("anchor_id")

    return parser


def _run_vault_op(args: argparse.Namespace) -> int:
    """Run a vault key-management command (keys come from the environment)."""
    from hydromemory.vault import encrypt_vault, rotate_vault_keys

    cfg = _config_from_args(args)
    try:
        if args.command == "vault-rotate":
            count = rotate_vault_keys(cfg)
            print(f"rotated {count} droplet(s) to the primary key")
        else:  # vault-encrypt
            count = encrypt_vault(cfg)
            print(f"encrypted {count} previously-plaintext droplet(s)")
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def _review_engine(args: argparse.Namespace, *, identity: bool = False) -> Any:
    """Build an engine for the review console with HydroIntegrate (and optionally HydroIdentity)
    enabled on the CLI's config, so durable updates commit to their target layer rather than
    being recorded only."""
    cfg = _config_from_args(args)
    cfg.integrate_enabled = True
    if identity:
        cfg.identity_enabled = True
    return build_engine(cfg)


def _run_review(args: argparse.Namespace) -> int:
    """Run a review-console action (Master Spec §22 MVP-6)."""
    from hydromemory.hydrointegrate.schema import ReintegrationStatus

    action = args.review_action

    # -- HydroIntegrate reintegration queue --------------------------------------------
    if action == "pending":
        engine = _review_engine(args)
        try:
            pending = engine.integrate.reintegration_repo.query(
                status=ReintegrationStatus.REVIEW_REQUIRED
            )
            if not pending:
                print("(no reintegrations awaiting review)")
            for i, obj in enumerate(pending):
                if i:
                    print("-" * 60)
                print(f"{obj.id}  type={obj.update_type}  target={obj.target_layer}")
                print(f"  statement:   {obj.candidate_statement}")
                print(f"  sensitivity: {obj.sensitivity:.2f}")
        finally:
            engine.close()
        return 0

    if action in ("approve", "reject"):
        # Approving commits an identity_update to HydroIdentity, so enable that layer too.
        engine = _review_engine(args, identity=True)
        try:
            obj = engine.integrate.reintegration_repo.get(args.reintegration_id)
            if obj is None:
                print(f"reintegration not found: {args.reintegration_id}")
                return 1
            if action == "reject":
                obj = engine.integrate.review_update(obj, approved=False)
                print(f"{obj.id}  status={obj.status}")
            else:
                engine.integrate.review_update(obj, approved=True)
                engine.integrate.apply_update(obj)
                engine.integrate.audit_update(obj)
                engine.integrate.notify_agents(obj)
                print(f"{obj.id}  status={obj.status}")
                print(f"  applied_ref: {obj.meta.get('applied_ref')}")
        finally:
            engine.close()
        return 0

    if action == "log":
        engine = _review_engine(args)
        try:
            log = engine.integrate.reintegration_repo.query(
                status=args.status, target_layer=args.target
            )
            if not log:
                print("(no reintegrations)")
            for i, obj in enumerate(log):
                if i:
                    print("-" * 60)
                print(
                    f"{obj.id}  status={obj.status}  type={obj.update_type}  "
                    f"target={obj.target_layer}  created={_fmt_dt(obj.created_at)}"
                )
        finally:
            engine.close()
        return 0

    if action in ("drain", "rollback"):
        engine = _review_engine(args, identity=True)
        try:
            obj = engine.integrate.reintegration_repo.get(args.reintegration_id)
            if obj is None:
                print(f"reintegration not found: {args.reintegration_id}")
                return 1
            if action == "drain":
                obj = engine.integrate.drain(obj)
            else:
                obj = engine.integrate.rollback(obj, reason=getattr(args, "reason", None))
            print(f"{obj.id}  status={obj.status}")
        finally:
            engine.close()
        return 0

    # -- HydroIdentity anchors ---------------------------------------------------------
    if action == "anchors":
        engine = _review_engine(args, identity=True)
        try:
            anchors = engine.identity.query(status=args.status, anchor_type=args.anchor_type)
            if not anchors:
                print("(no anchors)")
            for i, anchor in enumerate(anchors):
                if i:
                    print("-" * 60)
                print(f"{anchor.id}  type={anchor.anchor_type}  status={anchor.status}")
                print(f"  statement: {anchor.statement}")
        finally:
            engine.close()
        return 0

    if action in ("affirm", "retire"):
        engine = _review_engine(args, identity=True)
        try:
            anchor = engine.identity.identity_repo.get(args.anchor_id)
            if anchor is None:
                print(f"anchor not found: {args.anchor_id}")
                return 1
            if action == "affirm":
                anchor = engine.identity.affirm(anchor)
            else:
                anchor = engine.identity.retire(anchor)
            print(f"{anchor.id}  status={anchor.status}")
        finally:
            engine.close()
        return 0

    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "run-example":
        from hydromemory.examples import run_example

        _render_one(run_example(args.name))
        return 0
    if args.command in ("vault-rotate", "vault-encrypt"):
        return _run_vault_op(args)
    if args.command == "review":
        return _run_review(args)
    cfg = _config_from_args(args)
    # When a vault key/toggle is configured, absorb/recall/hql go through the
    # encrypted, audited vault store (owner cross-app); otherwise the plain engine
    # (default — v1 CLI behavior unchanged).
    if cfg.vault_key or cfg.vault_enabled:
        from hydromemory.vault import build_vault_engine

        engine = build_vault_engine(cfg)
    else:
        engine = build_engine(cfg)
    try:
        if args.command == "absorb":
            ctx = json.loads(args.context) if args.context else {}
            _print_decision(engine.absorb(args.content, source=args.source, context=ctx))
        elif args.command == "recall":
            _render(engine.recall(args.query, agent=_agent_from_args(args)))
        elif args.command == "hql":
            _render(engine.hql(args.query, agent=_agent_from_args(args)))
    finally:
        engine.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
