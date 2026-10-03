"""Verifies JEV Phase 1 P1.2: the structured JEV judge backend
(evals/backends/jev.py) — question construction from rubric.json, level
mapping, failure mapping to BackendUnavailable, and the pairwise ChoiceQ.
No network: a fake DecisionBackend stands in for core/decision/jev.py."""
import sys
from pathlib import Path

import pytest


from skillswiki.decision import DecisionUnavailable  # noqa: E402
from skillswiki.decision.base import (  # noqa: E402
    ChoiceA, ChoiceQ, DecisionResult, NoulA, ScoreA, ScoreQ,
)
from skillswiki.evals.backends import BackendUnavailable, get_backend  # noqa: E402
from skillswiki.evals.backends import jev as jev_backend  # noqa: E402
from skillswiki.evals.backends.jev import LEVELS, JevJudge  # noqa: E402

RUBRIC = {
    "dimensions": [{"id": "dim_a", "desc": "Output is complete."}],
    "items": [{"id": "item_b", "desc": "Ends with one clear call to action.", "weight": 2}],
    "applied": [{"id": "rule_c", "desc": "Uses plain words."}],
}
PROMPT_WITH_INPUTS = "Rewrite this.\n\ninputs.text: The dashboard shipped."


def _score(score: float, probs: dict[str, float], confidence: float = 0.8) -> ScoreA:
    return ScoreA(score=score, probabilities=probs, confidence=confidence,
                  legend={str(i): lvl for i, lvl in enumerate(LEVELS)})


class FakeDecision:
    name = "fake"

    def __init__(self, answers=None, error: Exception | None = None, input_tokens: int = 700):
        self.answers = answers
        self.error = error
        self.input_tokens = input_tokens
        self.calls: list[tuple] = []

    def decide(self, state, questions):
        self.calls.append((state, questions))
        if self.error:
            raise self.error
        answers = self.answers(questions) if callable(self.answers) else self.answers
        return DecisionResult(answers=answers, input_tokens=self.input_tokens, model="jev-test")


def _all_level(level: int):
    probs = {str(i): (1.0 if i == level else 0.0) for i in range(3)}
    return lambda questions: {k: _score(float(level), probs) for k in questions}


# ── construction / registry ───────────────────────────────────────────


def test_get_backend_constructs_without_key_or_flag(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    assert get_backend("claude").name == "claude"
    assert get_backend("jev").name == "jev"


def test_no_key_raises_backend_unavailable_naming_the_key(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    with pytest.raises(BackendUnavailable, match="TYPESAFE_API_KEY"):
        JevJudge().judge_rubric(PROMPT_WITH_INPUTS, "some deliverable", RUBRIC)


def test_text_judge_and_run_are_not_supported():
    judge = JevJudge(FakeDecision(_all_level(2)))
    with pytest.raises(BackendUnavailable):
        judge.judge("prompt", None)
    with pytest.raises(BackendUnavailable):
        judge.run("prompt", None, None)


# ── judge_rubric: questions + state ───────────────────────────────────


def test_one_score_question_per_criterion_with_verbatim_desc():
    fake = FakeDecision(_all_level(2))
    JevJudge(fake).judge_rubric(PROMPT_WITH_INPUTS, "deliverable text", RUBRIC)
    assert len(fake.calls) == 1
    _state, questions = fake.calls[0]
    assert sorted(questions) == ["dim_a", "item_b", "rule_c"]
    for cid, desc in [("dim_a", "Output is complete."), ("item_b", "Ends with one clear call to action."),
                      ("rule_c", "Uses plain words.")]:
        q = questions[cid]
        assert isinstance(q, ScoreQ)
        assert q.instructions == jev_backend.INSTRUCTIONS.format(desc=desc)
        assert list(q.criteria) == LEVELS


def test_state_is_prompt_with_inputs_and_deliverable_only():
    fake = FakeDecision(_all_level(2))
    JevJudge(fake).judge_rubric(PROMPT_WITH_INPUTS, "deliverable text", RUBRIC)
    state, _q = fake.calls[0]
    assert state == {"task_prompt": PROMPT_WITH_INPUTS, "deliverable": "deliverable text"}


# Pass rule approved by the human 2026-09-25 (replaces nearest-level): level 2 iff
# P(level 2) >= 0.5, otherwise the likelier of levels 0/1 (tie -> 0, fail-closed).
@pytest.mark.parametrize("probs,expected", [
    ({"0": 0.3, "1": 0.0, "2": 0.7}, 2),   # 0/2 split: nearest-level (1.4 -> 1) would have failed it
    ({"0": 0.25, "1": 0.25, "2": 0.5}, 2),  # exactly 0.5 passes
    ({"0": 0.2, "1": 0.4, "2": 0.4}, 1),
    ({"0": 0.6, "1": 0.1, "2": 0.3}, 0),
    ({"0": 0.3, "1": 0.3, "2": 0.4}, 0),    # 0/1 tie -> 0
])
def test_level_uses_p2_pass_rule(probs, expected):
    score = sum(int(k) * v for k, v in probs.items())
    fake = FakeDecision(lambda qs: {k: _score(score, probs) for k in qs})
    out = JevJudge(fake).judge_rubric(PROMPT_WITH_INPUTS, "x", RUBRIC)
    assert out["scale"]["dim_a"] == expected
    assert out["detail"]["dim_a"]["level"] == expected


def test_detail_carries_score_nearest_argmax_confidence_probabilities_and_usage():
    probs = {"0": 0.3, "1": 0.0, "2": 0.7}
    fake = FakeDecision(lambda qs: {k: _score(1.4, probs, 0.3) for k in qs}, input_tokens=812)
    out = JevJudge(fake).judge_rubric(PROMPT_WITH_INPUTS, "x", RUBRIC)
    d = out["detail"]["item_b"]
    assert d["score"] == pytest.approx(1.4)
    assert d["level"] == 2
    assert d["nearest"] == 1  # kept for the report, never used as the verdict
    assert d["argmax"] == 2
    assert d["p2"] == pytest.approx(0.7)
    assert d["confidence"] == pytest.approx(0.3)
    assert d["probabilities"] == probs
    assert out["usage"] == {"input_tokens": 812}


def test_empty_deliverable_scores_zero_without_a_call():
    fake = FakeDecision(_all_level(2))
    out = JevJudge(fake).judge_rubric(PROMPT_WITH_INPUTS, "", RUBRIC)
    assert fake.calls == []
    assert out["scale"] == {"dim_a": 0, "item_b": 0, "rule_c": 0}
    assert out["detail"] is None
    assert out["usage"] is None


# ── failure mapping ───────────────────────────────────────────────────


def test_decision_unavailable_maps_to_backend_unavailable():
    fake = FakeDecision(error=DecisionUnavailable("HTTP 529"))
    with pytest.raises(BackendUnavailable, match="529"):
        JevJudge(fake).judge_rubric(PROMPT_WITH_INPUTS, "x", RUBRIC)


def test_missing_or_wrong_answer_type_maps_to_backend_unavailable():
    wrong = FakeDecision(lambda qs: {k: NoulA(noul=0.9) for k in qs})
    with pytest.raises(BackendUnavailable):
        JevJudge(wrong).judge_rubric(PROMPT_WITH_INPUTS, "x", RUBRIC)


# ── judge_pair ────────────────────────────────────────────────────────


def test_judge_pair_builds_choice_and_maps_winner():
    fake = FakeDecision(lambda qs: {k: ChoiceA(choice="B", probabilities={"A": 0.3, "B": 0.7}, confidence=0.6)
                                    for k in qs})
    out = JevJudge(fake).judge_pair(PROMPT_WITH_INPUTS, "resp A", "resp B", RUBRIC)
    state, questions = fake.calls[0]
    assert state == {"task_prompt": PROMPT_WITH_INPUTS, "response_a": "resp A", "response_b": "resp B"}
    (q,) = questions.values()
    assert isinstance(q, ChoiceQ)
    assert sorted(q.criteria) == ["A", "B"]
    assert "Output is complete." in q.instructions and "Ends with one clear call to action." in q.instructions
    assert "Uses plain words." not in q.instructions  # applied rules never go to pairwise (runner parity)
    assert out["winner"] == "B"
    assert out["detail"] == {"p_a": 0.3, "p_b": 0.7, "confidence": 0.6}
    assert out["usage"] == {"input_tokens": 700}


def test_judge_pair_unknown_choice_maps_to_backend_unavailable():
    fake = FakeDecision(lambda qs: {k: ChoiceA(choice="tie", probabilities={"tie": 1.0}, confidence=1.0)
                                    for k in qs})
    with pytest.raises(BackendUnavailable):
        JevJudge(fake).judge_pair(PROMPT_WITH_INPUTS, "a", "b", RUBRIC)


# ── criterion levels (JEV Phase 2 P2.2b: wording is rubric data, never code) ──


def test_criterion_levels_are_sent_to_jev_verbatim():
    own = ["Missing a fact.", "Every fact, one blurred.", "Every fact, unchanged."]
    rubric = {"dimensions": [{"id": "dim_a", "desc": "Output is complete."}],
              "items": [{"id": "facts", "desc": "Keeps every fact.", "levels": own}]}
    fake = FakeDecision(_all_level(2))
    JevJudge(fake).judge_rubric(PROMPT_WITH_INPUTS, "x", rubric)
    _state, questions = fake.calls[0]
    assert list(questions["facts"].criteria) == own
    assert list(questions["dim_a"].criteria) == LEVELS


def test_criterion_without_levels_uses_generic_wording():
    fake = FakeDecision(_all_level(2))
    JevJudge(fake).judge_rubric(PROMPT_WITH_INPUTS, "x", {"items": [{"id": "facts", "desc": "Keeps every fact."}]})
    assert list(fake.calls[0][1]["facts"].criteria) == LEVELS


def test_situational_variant_is_retired():
    import skillswiki.evals.backends.jev as jev_mod
    assert not hasattr(jev_mod, "SITUATIONAL_LEVELS")
    with pytest.raises(KeyError):
        get_backend("jev_situational")
    assert get_backend("jev").name == "jev"
