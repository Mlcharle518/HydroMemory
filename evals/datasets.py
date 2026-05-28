"""Load + validate hand-built synthetic eval datasets (evals/README.md §5)."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

_DATA = Path(__file__).parent / "data"


@dataclass(frozen=True)
class CorpusDroplet:
    id: str
    content: str
    links: dict[str, list[str]] = field(default_factory=dict)
    state: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class Question:
    id: str
    seed: str
    gold_support: list[str]
    answer: str | None = None
    answer_keys: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class MultiHopDataset:
    corpus: list[CorpusDroplet]
    questions: list[Question]


def load_multihop(path: str | Path | None = None) -> MultiHopDataset:
    source = Path(path) if path else _DATA / "multihop.json"
    raw = json.loads(source.read_text(encoding="utf-8"))
    corpus = [
        CorpusDroplet(
            id=c["id"], content=c["content"], links=c.get("links", {}), state=c.get("state", {})
        )
        for c in raw["corpus"]
    ]
    questions = [
        Question(
            id=q["id"],
            seed=q["seed"],
            gold_support=list(q["gold_support"]),
            answer=q.get("answer"),
            answer_keys=list(q.get("answer_keys", [])),
        )
        for q in raw["questions"]
    ]
    return MultiHopDataset(corpus=corpus, questions=questions)


def load_pollution(path: str | Path | None = None) -> list[dict]:
    """Load the pollution timeline (a list of op dicts; see evals/README.md §5.2)."""
    source = Path(path) if path else _DATA / "pollution.json"
    raw = json.loads(source.read_text(encoding="utf-8"))
    return list(raw["timeline"])


def load_consolidation(path: str | Path | None = None) -> list[dict]:
    """Load consolidation themes (each: id, query, principle text, episodes)."""
    source = Path(path) if path else _DATA / "consolidation.json"
    raw = json.loads(source.read_text(encoding="utf-8"))
    return list(raw["themes"])


def load_intent(path: str | Path | None = None) -> list[dict]:
    """Load intent-distillation themes (each: id, query, intent statement, episodes)."""
    source = Path(path) if path else _DATA / "intent.json"
    raw = json.loads(source.read_text(encoding="utf-8"))
    return list(raw["themes"])


def load_packing(path: str | Path | None = None) -> dict:
    """Load the packing dataset: a shared `fillers` pool + `cases` (q/needle/answer)."""
    source = Path(path) if path else _DATA / "packing.json"
    raw = json.loads(source.read_text(encoding="utf-8"))
    return {"fillers": list(raw["fillers"]), "cases": list(raw["cases"])}


def load_longmemeval(path: str | Path | None = None) -> list[dict]:
    """Load LongMemEval instances (question / answer / question_type / haystack_sessions).

    Accepts the real benchmark's JSON (a list of instances) or our sample (a dict with
    an ``instances`` key). Default: the bundled synthetic sample.
    """
    source = Path(path) if path else _DATA / "longmemeval_sample.json"
    raw = json.loads(source.read_text(encoding="utf-8"))
    return list(raw) if isinstance(raw, list) else list(raw.get("instances", []))
