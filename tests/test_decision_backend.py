"""Verifies JEV Phase 0 (P0.4/P0.5): the vendor-neutral DecisionBackend
interface, the JEV httpx backend (payloads, parsing, retry/backoff, failure
mapping to DecisionUnavailable) and the flag-gated factory. No network:
every request goes through httpx.MockTransport."""
import json

import httpx
import pytest

from skillswiki.decision import DecisionUnavailable, get_decision_backend
from skillswiki.decision.base import ChoiceA, ChoiceQ, NoulA, NoulQ, ScoreA, ScoreQ
from skillswiki.decision.jev import JevBackend
from skillswiki.decision.null import NullBackend

API_KEY = "test-key-123"

# JEV_REFERENCE.md §4 response shape (replace with the captured wire shape in P0.3b).
FAKE_RESPONSE = {
    "model": "jev-1.13.0",
    "answers": {
        "best_skill": {
            "type": "choice",
            "choice": "humanizer",
            "probabilities": {"humanizer": 0.81, "offer_design": 0.19},
            "confidence": 0.78,
        },
        "needs_skill": {"type": "noul", "noul": 0.91},
        "quality": {
            "type": "score",
            "score": 1.43,
            "probabilities": {"0": 0.0, "1": 0.57, "2": 0.43},
            "confidence": 0.55,
            "legend": {"0": "none", "1": "buried", "2": "clear"},
        },
    },
    "usage": {"input_tokens": 812, "output_tokens": 0},
}

QUESTIONS = {
    "best_skill": ChoiceQ(
        instructions="Which skill fits?",
        criteria={"humanizer": "Rewrites AI text", "offer_design": "Designs offers"},
    ),
    "needs_skill": NoulQ(instructions="The request requires a documented procedure."),
    "quality": ScoreQ(
        instructions="How clear is the call to action?",
        criteria=["none", "buried", "clear"],
    ),
}


class Recorder:
    """MockTransport handler that replays scripted responses and records requests."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        nxt = self.responses.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt

    def body(self, i: int = 0) -> dict:
        return json.loads(self.requests[i].content)


def ok(payload=None) -> httpx.Response:
    return httpx.Response(200, json=FAKE_RESPONSE if payload is None else payload)


def make_backend(handler, sleeps=None, model="jev-latest") -> JevBackend:
    record = sleeps if sleeps is not None else []
    return JevBackend(
        API_KEY,
        model=model,
        transport=httpx.MockTransport(handler),
        sleep=record.append,
    )


# ── payload construction ──────────────────────────────────────────────


def test_payload_choice_question():
    rec = Recorder(ok())
    make_backend(rec).decide({"request": "hi"}, QUESTIONS)
    body = rec.body()
    assert body["model"] == "jev-latest"
    assert body["state"] == {"request": "hi"}
    assert body["questions"]["best_skill"] == {
        "type": "choice",
        "instructions": "Which skill fits?",
        "criteria": {"humanizer": "Rewrites AI text", "offer_design": "Designs offers"},
    }


def test_payload_score_question_keeps_level_order():
    rec = Recorder(ok())
    make_backend(rec).decide("text", QUESTIONS)
    q = rec.body()["questions"]["quality"]
    assert q["type"] == "score"
    assert q["criteria"] == ["none", "buried", "clear"]


def test_payload_noul_omits_none_criteria():
    noul_only = {**FAKE_RESPONSE, "answers": {"n": {"type": "noul", "noul": 0.7}}}
    rec = Recorder(ok(noul_only), ok(noul_only))
    backend = make_backend(rec)
    backend.decide("text", {"n": NoulQ(instructions="Holds.")})
    backend.decide("text", {"n": NoulQ(instructions="Holds.", criteria={"true": "yes"})})
    assert rec.body(0)["questions"]["n"] == {"type": "noul", "instructions": "Holds."}
    assert rec.body(1)["questions"]["n"]["criteria"] == {"true": "yes"}


def test_request_has_bearer_auth_and_model_env(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", API_KEY)
    monkeypatch.setenv("JEV_MODEL", "jev-preview")
    backend = get_decision_backend()
    rec = Recorder(ok())
    backend._transport = httpx.MockTransport(rec)
    backend.decide("text", QUESTIONS)
    assert rec.requests[0].headers["Authorization"] == f"Bearer {API_KEY}"
    assert rec.requests[0].url == "https://api.typesafe.ai/v1/systemone"
    assert rec.body()["model"] == "jev-preview"


# ── response parsing ──────────────────────────────────────────────────


def test_parses_all_three_answer_types():
    result = make_backend(Recorder(ok())).decide("text", QUESTIONS)
    assert result.answers["best_skill"] == ChoiceA(
        choice="humanizer",
        probabilities={"humanizer": 0.81, "offer_design": 0.19},
        confidence=0.78,
    )
    assert result.answers["needs_skill"] == NoulA(noul=0.91)
    quality = result.answers["quality"]
    assert isinstance(quality, ScoreA)
    assert quality.score == pytest.approx(1.43)
    assert quality.confidence == pytest.approx(0.55)
    assert quality.legend["2"] == "clear"
    assert result.input_tokens == 812
    assert result.model == "jev-1.13.0"


# ── retry / failure mapping ───────────────────────────────────────────


@pytest.mark.parametrize("status", [429, 529])
def test_retries_retryable_status_then_succeeds(status):
    sleeps: list[float] = []
    rec = Recorder(httpx.Response(status, json={"error": "slow down"}), ok())
    result = make_backend(rec, sleeps).decide("text", QUESTIONS)
    assert len(rec.requests) == 2
    assert sleeps == [0.5]
    assert result.input_tokens == 812


def test_unavailable_after_max_retries():
    sleeps: list[float] = []
    rec = Recorder(*[httpx.Response(529, json={"error": "overloaded"})] * 3)
    with pytest.raises(DecisionUnavailable):
        make_backend(rec, sleeps).decide("text", QUESTIONS)
    assert len(rec.requests) == 3
    assert sleeps == [0.5, 1.0]


@pytest.mark.parametrize("status", [401, 422])
def test_client_errors_fail_fast_without_retry(status):
    sleeps: list[float] = []
    rec = Recorder(httpx.Response(status, json={"error": "bad request"}))
    with pytest.raises(DecisionUnavailable) as exc:
        make_backend(rec, sleeps).decide("text", QUESTIONS)
    assert len(rec.requests) == 1
    assert sleeps == []
    assert str(status) in str(exc.value)
    assert API_KEY not in str(exc.value)


def test_timeout_maps_to_unavailable():
    rec = Recorder(*[httpx.ReadTimeout("timed out")] * 3)
    with pytest.raises(DecisionUnavailable):
        make_backend(rec).decide("text", QUESTIONS)
    assert len(rec.requests) == 3


def test_timeout_then_success_recovers():
    rec = Recorder(httpx.ConnectError("refused"), ok())
    result = make_backend(rec).decide("text", QUESTIONS)
    assert result.answers["needs_skill"] == NoulA(noul=0.91)


@pytest.mark.parametrize(
    "payload",
    [
        {"model": "jev", "usage": {"input_tokens": 1}},  # no answers
        {**FAKE_RESPONSE, "answers": {"x": {"type": "mystery"}}},  # unknown type
        {**FAKE_RESPONSE, "answers": {"x": {"type": "choice"}}},  # missing fields
        {**FAKE_RESPONSE, "answers": {"needs_skill": {"type": "noul", "noul": 0.5}}},  # unanswered questions
    ],
)
def test_malformed_response_maps_to_unavailable(payload):
    with pytest.raises(DecisionUnavailable):
        make_backend(Recorder(ok(payload))).decide("text", QUESTIONS)


def test_non_json_body_maps_to_unavailable():
    rec = Recorder(httpx.Response(200, text="<html>gateway</html>"))
    with pytest.raises(DecisionUnavailable):
        make_backend(rec).decide("text", QUESTIONS)


# ── null backend + factory ────────────────────────────────────────────


def test_null_backend_raises_immediately():
    with pytest.raises(DecisionUnavailable):
        NullBackend().decide("text", QUESTIONS)


def test_factory_no_key_returns_null(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "  ")
    assert isinstance(get_decision_backend(), NullBackend)


@pytest.mark.parametrize("value", ["off", "OFF", "false", "0", "no"])
def test_factory_switch_off_returns_null_even_with_key(monkeypatch, value):
    monkeypatch.setenv("TYPESAFE_API_KEY", API_KEY)
    monkeypatch.setenv("SKILLSWIKI_JEV", value)
    assert isinstance(get_decision_backend(), NullBackend)


@pytest.mark.parametrize("value", [None, "on", ""])
def test_factory_key_returns_jev(monkeypatch, value):
    monkeypatch.setenv("TYPESAFE_API_KEY", API_KEY)
    if value is None:
        monkeypatch.delenv("SKILLSWIKI_JEV", raising=False)
    else:
        monkeypatch.setenv("SKILLSWIKI_JEV", value)
    backend = get_decision_backend()
    assert isinstance(backend, JevBackend)
    assert backend.name == "jev"


# Captured verbatim from the live API on 2026-09-25.
CAPTURED_LIVE_RESPONSE = {
    "model": "jev-1.13.0",
    "answers": {
        "best_skill": {"type": "choice", "choice": "humanizer", "confidence": 1.0,
                       "probabilities": {"offer_design": 0.0, "humanizer": 1.0, "linkedin_post_writer": 0.0}},
        "needs_skill": {"type": "noul", "noul": 0.06},
        "quality": {"type": "score", "score": 2.0, "confidence": 0.99,
                    "legend": {"0": "No call to action present",
                               "1": "A call to action exists but is buried or vague",
                               "2": "Ends with one clear call to action"},
                    "probabilities": {"0": 0.0, "1": 0.0, "2": 1.0}},
    },
    "usage": {"input_tokens": 491, "output_tokens": 77},
}


def test_parses_captured_live_response():
    result = make_backend(Recorder(ok(CAPTURED_LIVE_RESPONSE))).decide("text", QUESTIONS)
    assert result.answers["best_skill"] == ChoiceA(
        choice="humanizer",
        probabilities={"offer_design": 0.0, "humanizer": 1.0, "linkedin_post_writer": 0.0},
        confidence=1.0,
    )
    assert result.answers["needs_skill"] == NoulA(noul=0.06)
    quality = result.answers["quality"]
    assert quality.score == pytest.approx(2.0) and quality.probabilities == {"0": 0.0, "1": 0.0, "2": 1.0}
    assert result.input_tokens == 491 and result.model == "jev-1.13.0"


def test_live_400_invalid_request_fails_fast():
    body = {"detail": {"error_type": "api_usage_error", "message": "Invalid request."}}
    rec = Recorder(httpx.Response(400, json=body))
    with pytest.raises(DecisionUnavailable, match="400"):
        make_backend(rec).decide("text", QUESTIONS)
    assert len(rec.requests) == 1
