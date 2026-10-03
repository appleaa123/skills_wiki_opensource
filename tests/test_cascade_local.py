"""The cascade end to end through the CLI: an empty calibration store means the LLM judge decides everything; after
enough agreeing runs JEV earns trust on a style criterion and starts deciding it. Fakes only (no JEV, no LLM)."""
import json

import pytest
from fakes import RUBRIC, TASKS, FakeBackend
from helpers import install_fixture_skills

from skillswiki import cli, discovery, library
from skillswiki.decision.base import ChoiceA, ChoiceQ, DecisionResult, ScoreA
from skillswiki.evals import calibration, suite


class AgreeingJudge(FakeBackend):
    """Grades every criterion 2, like the fake JEV below."""

    def judge(self, prompt, model, timeout=60, profile=None):
        reply = super().judge(prompt, model, timeout, profile)
        data = json.loads(reply["text"])
        if "winner" not in data:
            reply["text"] = json.dumps({k: 2 for k in data})
        return reply


class FakeJevDecision:
    name = "jev"

    def decide(self, state, questions):
        answers = {}
        for key, q in questions.items():
            if isinstance(q, ChoiceQ):
                first = next(iter(q.criteria))
                answers[key] = ChoiceA(choice=first, probabilities={k: (0.6 if k == first else 0.4) for k in q.criteria},
                                       confidence=0.6)
            else:
                answers[key] = ScoreA(score=1.98, probabilities={"0": 0.005, "1": 0.005, "2": 0.99}, confidence=0.99,
                                      legend={})
        return DecisionResult(answers=answers, input_tokens=400, model="fake-jev")


@pytest.fixture
def ready(tmp_home, monkeypatch):
    install_fixture_skills(tmp_home / "native")
    discovery.sync_db()
    library.adopt("email-polisher")
    rubric = {**RUBRIC, "items": [{**RUBRIC["items"][0], "kind": "style"}]}
    suite.write("email-polisher", TASKS, rubric)
    suite.set_status("email-polisher", "checked")
    judge = AgreeingJudge()
    monkeypatch.setattr("skillswiki.evals.backends.get_backend", lambda name: judge)
    monkeypatch.setattr("skillswiki.evals.cascade.get_decision_backend", lambda: FakeJevDecision())
    monkeypatch.setenv("TYPESAFE_API_KEY", "user-key")


def _run(capsys):
    code = cli.main(["--json", "eval", "run", "email-polisher", "--cascade", "--judge", "claude", "--runs", "3"])
    out = capsys.readouterr()
    assert code == 0, out.err
    return json.loads(out.out)


def _final_by(result_path, criterion):
    outputs = [json.loads(line) for line in open(str(result_path).replace(".json", ".outputs.jsonl"))]
    return [o["cascade"][criterion]["final_by"] for o in outputs]


def test_empty_store_means_llm_decides_everything(ready, capsys):
    first = _run(capsys)
    assert set(_final_by(first["result_path"], "has_next_step")) == {"llm"}
    outputs = [json.loads(line) for line in open(first["result_path"].replace(".json", ".outputs.jsonl"))]
    assert {o["cascade"]["has_next_step"]["reason"] for o in outputs} == {"no_evidence"}
    assert all(o["judge_detail"]["has_next_step"]["level"] == 2 for o in outputs)  # JEV graded anyway
    assert "60 paired grades added; 5 criteria tracked, 0 trusted" in first["calibration"]  # 5 criteria x 12 outputs


def test_trust_is_earned_from_the_users_own_runs(ready, capsys):
    _run(capsys)
    second = _run(capsys)
    assert "1 trusted" in second["calibration"]
    entry = calibration.entry(calibration.load("email-polisher"), "claude", "has_next_step")
    assert entry["status"] == "calibrated" and entry["n"] == 24
    third = _run(capsys)
    final_by = _final_by(third["result_path"], "has_next_step")
    assert final_by.count("jev") >= len(final_by) - 3  # the audit slice may still send a few to the judge
    # factual (untagged) criteria: JEV may only fail, so its passes never become final
    assert set(_final_by(third["result_path"], "failure_mechanism")) == {"llm"}


def test_cascade_without_key_is_a_clean_error(ready, capsys, monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY")
    monkeypatch.setattr("skillswiki.evals.cascade.get_decision_backend",
                        lambda: __import__("skillswiki.decision.null", fromlist=["NullBackend"]).NullBackend())
    code = cli.main(["eval", "run", "email-polisher", "--cascade", "--judge", "claude", "--runs", "1"])
    assert code == 1 and "TYPESAFE_API_KEY" in capsys.readouterr().err


def test_calibration_command(ready, capsys):
    cli.main(["eval", "calibration", "email-polisher"])
    assert "No calibration yet" in capsys.readouterr().out
    _run(capsys)
    cli.main(["eval", "calibration", "email-polisher"])
    assert "has_next_step" in capsys.readouterr().out
