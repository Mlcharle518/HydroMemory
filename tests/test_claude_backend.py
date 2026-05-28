"""Claude-backend tests.

``test_claude_backend_no_key_raises`` runs offline (the key check fires before any
anthropic import) and pins the clear-error contract. ``test_claude_backend_smoke``
skips unless ``ANTHROPIC_API_KEY`` is set and ``anthropic`` is installed.

The remaining tests cover the *real* Claude code path entirely offline (no API key,
no ``anthropic`` import): the pure ``_parse_json_object`` helper, the adapter
dict->dataclass mapping via a duck-typed fake client, and the structured-output
``messages.parse`` -> ``messages.create`` + JSON fallback via a fake Anthropic SDK
client injected through the lazy ``_ClaudeClient._ensure``.
"""
from __future__ import annotations

import os
from typing import Any

import pytest

from hydromemory.config import HydroConfig
from hydromemory.intelligence import build_intelligence, claude_backend
from hydromemory.intelligence.base import (
    MEMORY_TYPES,
    Classification,
    ContaminationVerdict,
    coerce_memory_type,
)
from hydromemory.intelligence.claude_backend import (
    ClaudeAbstractor,
    ClaudeClassifier,
    ClaudeContaminationDetector,
    _clamp01,
    _ClaudeClient,
    _parse_json_object,
)
from hydromemory.schema import Droplet


def test_claude_backend_no_key_raises_clearly():
    # Builds fine with no key; raises a clear error only when a Claude op runs.
    cfg = HydroConfig(intelligence_backend="claude", anthropic_api_key=None)
    intel = build_intelligence(cfg)
    with pytest.raises(RuntimeError, match="Anthropic API key"):
        intel.classifier.classify("hello world")


def test_claude_backend_default_model_is_opus():
    assert HydroConfig().claude_model == "claude-opus-4-7"


def test_coerce_memory_type_maps_to_vocabulary():
    # Known labels pass through; case/whitespace normalized; unknown -> general.
    assert coerce_memory_type("communication_preference") == "communication_preference"
    assert coerce_memory_type("  IDENTITY  ") == "identity"
    assert coerce_memory_type("user_preference_analysis_approach") == "general"
    assert coerce_memory_type("") == "general"
    assert coerce_memory_type(None) == "general"


def test_classify_system_prompt_lists_the_vocabulary():
    # The prompt steers the model toward the controlled set (the schema enforces it).
    for label in MEMORY_TYPES:
        assert label in claude_backend._CLASSIFY_SYSTEM


def test_classification_schema_enforces_vocabulary():
    # The structured-output schema constrains memory_type to MEMORY_TYPES, so
    # constrained decoding can never emit a free-form label.
    pytest.importorskip("pydantic")
    import pydantic

    schema = claude_backend._classification_schema()
    ok = schema(memory_type="identity", importance=0.5, sensitivity=0.1, expected_lifespan="persistent")
    assert ok.memory_type == "identity"
    with pytest.raises(pydantic.ValidationError):
        schema(memory_type="not_a_real_type", importance=0.5, sensitivity=0.1, expected_lifespan="persistent")


# --------------------------------------------------------------------------- #
# _parse_json_object: pure defensive JSON extraction
# --------------------------------------------------------------------------- #


def test_parse_json_object_bare_object() -> None:
    assert _parse_json_object('{"a": 1, "b": "x"}') == {"a": 1, "b": "x"}


def test_parse_json_object_embedded_in_prose() -> None:
    text = 'Sure! Here you go: {"contaminated": true, "confidence": 0.9} — hope that helps.'
    assert _parse_json_object(text) == {"contaminated": True, "confidence": 0.9}


@pytest.mark.parametrize("bad", ["", "   ", "not json at all", "{not: valid}", "{", None])
def test_parse_json_object_malformed_or_empty_returns_empty(bad: Any) -> None:
    assert _parse_json_object(bad) == {}


def test_parse_json_object_non_dict_json_returns_empty() -> None:
    # A valid JSON value that isn't an object (a list, a scalar) -> {}.
    assert _parse_json_object("[1, 2, 3]") == {}
    assert _parse_json_object("42") == {}


# --------------------------------------------------------------------------- #
# _clamp01: out-of-range / non-numeric coercion
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (1.5, 1.0),
        (-0.3, 0.0),
        (0.5, 0.5),
        (0.0, 0.0),
        (1.0, 1.0),
        ("0.25", 0.25),
        ("nope", 0.0),
        (None, 0.0),
    ],
)
def test_clamp01(raw: Any, expected: float) -> None:
    assert _clamp01(raw) == expected


# --------------------------------------------------------------------------- #
# Adapter dict->dataclass mapping via a duck-typed fake client
# --------------------------------------------------------------------------- #


class _FakeClient:
    """Duck-types ``_ClaudeClient`` for the adapters: canned payloads, no network.

    ``parse_object`` returns the next queued dict (the adapters never inspect the
    schema arg); ``complete_text`` returns a canned string. Records calls so the
    abstractor's prompt wiring can be asserted.
    """

    def __init__(self, *, parse_payloads: list[dict[str, Any]] | None = None, text: str = "") -> None:
        self._parse_payloads = list(parse_payloads or [])
        self._text = text
        self.parse_calls: list[tuple[str, str]] = []
        self.text_calls: list[tuple[str, str]] = []

    def parse_object(
        self, system: str, user: str, schema: Any, *, max_tokens: int = 256
    ) -> dict[str, Any]:
        self.parse_calls.append((system, user))
        return self._parse_payloads.pop(0) if self._parse_payloads else {}

    def complete_text(self, system: str, user: str, *, max_tokens: int = 64) -> str:
        self.text_calls.append((system, user))
        return self._text


def test_classifier_maps_payload_and_clamps() -> None:
    fake = _FakeClient(
        parse_payloads=[
            {
                "memory_type": "identity",
                "importance": 1.4,  # out of range -> clamps to 1.0
                "sensitivity": -0.2,  # out of range -> clamps to 0.0
                "expected_lifespan": "persistent",
            }
        ]
    )
    classifier = ClaudeClassifier(fake)  # type: ignore[arg-type]
    result = classifier.classify("User is a systems architect.")
    assert isinstance(result, Classification)
    assert result.memory_type == "identity"
    assert result.importance == 1.0
    assert result.sensitivity == 0.0
    assert result.expected_lifespan == "persistent"
    # The classify system prompt + the framed memory reached the client.
    assert fake.parse_calls and fake.parse_calls[0][0] == claude_backend._CLASSIFY_SYSTEM
    assert "User is a systems architect." in fake.parse_calls[0][1]


def test_classifier_coerces_out_of_vocab_memory_type_to_general() -> None:
    fake = _FakeClient(parse_payloads=[{"memory_type": "totally_made_up_label"}])
    result = ClaudeClassifier(fake).classify("x")  # type: ignore[arg-type]
    # Out-of-vocab label -> coerced to "general"; absent numbers -> defaulted+clamped.
    assert result.memory_type == "general"
    assert 0.0 <= result.importance <= 1.0
    assert 0.0 <= result.sensitivity <= 1.0


def test_classifier_empty_payload_uses_safe_defaults() -> None:
    result = ClaudeClassifier(_FakeClient(parse_payloads=[{}])).classify("x")  # type: ignore[arg-type]
    assert result.memory_type == "general"
    assert result.importance == 0.5
    assert result.sensitivity == 0.1
    assert result.expected_lifespan == "temporary"


def test_contamination_detector_maps_payload_and_clamps() -> None:
    fake = _FakeClient(
        parse_payloads=[{"contaminated": 1, "reason": "unreliable source", "confidence": 2.0}]
    )
    detector = ClaudeContaminationDetector(fake)  # type: ignore[arg-type]
    droplet = Droplet(id="d1", content="A stranger insists the earth is flat.")
    verdict = detector.assess(droplet, {"topic": "geography"})
    assert isinstance(verdict, ContaminationVerdict)
    assert verdict.contaminated is True  # truthy int -> bool
    assert verdict.reason == "unreliable source"
    assert verdict.confidence == 1.0  # clamped from 2.0
    # The contamination system prompt + droplet content reached the client.
    assert fake.parse_calls and fake.parse_calls[0][0] == claude_backend._CONTAM_SYSTEM
    assert "earth is flat" in fake.parse_calls[0][1]


def test_contamination_detector_empty_payload_defaults_clean() -> None:
    fake = _FakeClient(parse_payloads=[{}])
    verdict = ClaudeContaminationDetector(fake).assess(  # type: ignore[arg-type]
        Droplet(id="d2", content="ok"), {}
    )
    assert verdict.contaminated is False
    assert verdict.reason == ""
    assert verdict.confidence == 0.5


def test_abstractor_strips_quotes_and_uses_complete_text() -> None:
    fake = _FakeClient(text='"being dismissed by authority"')
    abstractor = ClaudeAbstractor(fake)  # type: ignore[arg-type]
    pattern = abstractor.evaporate("I was dismissed during a meeting.")
    assert pattern == "being dismissed by authority"  # surrounding quotes stripped
    assert fake.text_calls and fake.text_calls[0][0] == claude_backend._ABSTRACT_SYSTEM
    assert "I was dismissed during a meeting." in fake.text_calls[0][1]


# --------------------------------------------------------------------------- #
# parse_object structured-output -> text+JSON fallback, via a fake Anthropic SDK
# --------------------------------------------------------------------------- #


class _Block:
    """A content block shaped like an Anthropic text block (``type`` + ``text``)."""

    def __init__(self, text: str) -> None:
        self.type = "text"
        self.text = text


class _Resp:
    def __init__(self, text: str) -> None:
        self.content = [_Block(text)]


class _MessagesNoParse:
    """``messages`` with NO ``parse`` attribute -> the fallback path is forced."""

    def __init__(self, text: str) -> None:
        self._text = text
        self.create_calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> _Resp:
        self.create_calls.append(kwargs)
        return _Resp(self._text)


class _MessagesParseRaises(_MessagesNoParse):
    """``parse`` exists but raises -> falls through to ``create`` + JSON extraction."""

    def parse(self, **kwargs: Any) -> Any:
        raise RuntimeError("structured parse unavailable on this SDK build")


class _FakeAnthropic:
    def __init__(self, messages: Any) -> None:
        self.messages = messages


def _client_with(monkeypatch: pytest.MonkeyPatch, messages: Any) -> _ClaudeClient:
    """A real ``_ClaudeClient`` whose lazy ``_ensure`` returns a fake SDK client.

    Patching ``_ensure`` keeps the offline guarantee: ``anthropic`` is never
    imported and no API key is consulted.
    """
    client = _ClaudeClient(HydroConfig(intelligence_backend="claude", anthropic_api_key="unused"))
    fake = _FakeAnthropic(messages)
    monkeypatch.setattr(client, "_ensure", lambda: fake)
    return client


def test_parse_object_falls_back_when_messages_has_no_parse(monkeypatch: pytest.MonkeyPatch) -> None:
    messages = _MessagesNoParse('{"memory_type": "factual", "importance": 0.7}')
    client = _client_with(monkeypatch, messages)
    out = client.parse_object("sys", "user", schema=object(), max_tokens=128)
    assert out == {"memory_type": "factual", "importance": 0.7}
    # It went through complete_text -> messages.create, and asked for JSON-only.
    assert messages.create_calls, "expected the create() fallback to be invoked"
    sys_blocks = messages.create_calls[0]["system"]
    assert "JSON" in sys_blocks[0]["text"]


def test_parse_object_falls_back_when_parse_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    messages = _MessagesParseRaises('prose then {"contaminated": false, "confidence": 0.4} tail')
    client = _client_with(monkeypatch, messages)
    out = client.parse_object("sys", "user", schema=object(), max_tokens=128)
    # parse() raised -> the create()+extract fallback recovered the embedded object.
    assert out == {"contaminated": False, "confidence": 0.4}
    assert messages.create_calls, "expected fallback to create() after parse() raised"


def test_parse_object_uses_parsed_output_when_parse_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Parsed:
        def model_dump(self) -> dict[str, Any]:
            return {"memory_type": "value", "importance": 0.9}

    class _ParseResp:
        parsed_output = _Parsed()

    class _MessagesParseOk(_MessagesNoParse):
        def parse(self, **kwargs: Any) -> _ParseResp:
            return _ParseResp()

    messages = _MessagesParseOk("UNUSED should-not-be-read")
    client = _client_with(monkeypatch, messages)
    out = client.parse_object("sys", "user", schema=object(), max_tokens=128)
    # Structured parse succeeded -> its model_dump is returned, create() untouched.
    assert out == {"memory_type": "value", "importance": 0.9}
    assert not messages.create_calls


def test_complete_text_joins_text_blocks_and_wraps_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    # Happy path: only ``type == "text"`` blocks are joined and stripped.
    class _MixedResp:
        content = [_Block("  hello "), type("NonText", (), {"type": "tool_use", "text": "x"})()]

    class _Messages:
        def create(self, **kwargs: Any) -> Any:
            return _MixedResp()

    client = _client_with(monkeypatch, _Messages())
    assert client.complete_text("sys", "user") == "hello"

    # Error path: an SDK exception is annotated and re-raised as RuntimeError.
    class _BoomMessages:
        def create(self, **kwargs: Any) -> Any:
            raise ValueError("network blew up")

    boom = _client_with(monkeypatch, _BoomMessages())
    with pytest.raises(RuntimeError, match="Claude completion failed"):
        boom.complete_text("sys", "user")


@pytest.mark.skipif(not os.environ.get("ANTHROPIC_API_KEY"), reason="no ANTHROPIC_API_KEY set")
def test_claude_backend_smoke():
    pytest.importorskip("anthropic")
    cfg = HydroConfig.from_env()  # picks up ANTHROPIC_API_KEY + HYDRO_CLAUDE_MODEL
    cfg.intelligence_backend = "claude"
    intel = build_intelligence(cfg)

    pattern = intel.abstractor.evaporate("I was dismissed during a meeting.")
    assert isinstance(pattern, str) and pattern

    c = intel.classifier.classify("User prefers deep architecture and executable frameworks.")
    assert c.memory_type
    assert 0.0 <= c.importance <= 1.0
    assert c.expected_lifespan in {"temporary", "persistent", "archived"}

    v = intel.detector.assess(Droplet(id="x", content="A stranger insists the earth is flat."), {})
    assert isinstance(v.contaminated, bool)
    assert 0.0 <= v.confidence <= 1.0
