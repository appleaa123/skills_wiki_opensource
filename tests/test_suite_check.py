import json

import pytest
from fakes import RUBRIC, TASKS, write_suite
from helpers import install_fixture_skills

from skillswiki import discovery, library
from skillswiki.evals import suite, suite_check


@pytest.fixture
def adopted(tmp_home):
    install_fixture_skills(tmp_home / "native")
    discovery.sync_db()
    library.adopt("email-polisher")


def _levels(result):
    return [(i["level"], i["where"], i["message"].split(":")[0]) for i in result["issues"]]


def test_clean_suite_is_checked(adopted):
    write_suite()
    suite.set_status("email-polisher", "draft")
    result = suite_check.check("email-polisher")
    assert result["ok"] is True, result
    assert suite.status("email-polisher")["status"] == "checked"


def test_restating_prompt_flagged(adopted):
    tasks = TASKS + [{**TASKS[0], "id": "t03",
                      "prompt": "Keep the sender's meaning and every fact; never add new claims."}]
    suite.write("email-polisher", tasks, RUBRIC)
    flags = [i for i in suite_check.check("email-polisher")["issues"] if i["where"] == "t03"]
    assert flags and flags[0]["message"].startswith("possible_restatement")


def test_too_few_implicit_and_negative_wording(adopted):
    tasks = [{**t, "tags": []} for t in TASKS]
    rubric = {**RUBRIC, "items": [{"id": "no_filler", "desc": "Does not use filler openers.", "weight": 1}]}
    suite.write("email-polisher", tasks, rubric)
    issues = _levels(suite_check.check("email-polisher"))
    assert ("warn", "tasks", "only 0 task(s) tagged implicit; at least 2 measure the skill's default behaviour") in issues
    assert ("warn", "no_filler", "negative wording") in issues


def test_schema_error_blocks(adopted):
    suite.write("email-polisher", [{**TASKS[0], "skill": "wrong"}], RUBRIC)
    result = suite_check.check("email-polisher")
    assert result["ok"] is False and result["issues"][0]["level"] == "error"
    assert suite.status("email-polisher").get("status") != "checked"


def test_missing_suite(adopted):
    result = suite_check.check("email-polisher")
    assert result["ok"] is False and "eval generate" in result["issues"][0]["message"]


def test_llm_flags(adopted, monkeypatch):
    write_suite()

    class Fake:
        def judge(self, prompt, model, timeout=60, profile=None):
            assert "t01: Can you tidy" in prompt
            return {"text": json.dumps({"t01": {"restates": True, "reason": "mentions the rules"},
                                        "t02": {"restates": False, "reason": ""}}), "usage": None}
    monkeypatch.setattr(suite_check, "get_backend", lambda name: Fake())
    flags = [i for i in suite_check.check("email-polisher", llm=True)["issues"] if i["message"].startswith("llm_")]
    assert [f["where"] for f in flags] == ["t01"]


def test_show(adopted):
    write_suite()
    text = suite_check.show("email-polisher")
    assert "t01 [implicit] Can you tidy this up" in text and "failure_mechanism:" in text


class FakeJev:
    def __init__(self, nouls=None, fail=False):
        self.nouls, self.fail, self.calls = nouls or {}, fail, []

    def decide(self, state, questions):
        from skillswiki.decision.base import DecisionResult, DecisionUnavailable, NoulA
        if self.fail:
            raise DecisionUnavailable("down", reason="timeout")
        self.calls.append((state, questions))
        answers = {k: NoulA(noul=self.nouls.get(k, 0.2 if k.startswith("restates") else 0.9)) for k in questions}
        return DecisionResult(answers=answers, input_tokens=2000, model="fake")


def test_jev_flags_with_key(adopted, monkeypatch):
    write_suite()
    monkeypatch.setenv("TYPESAFE_API_KEY", "user-key")
    fake = FakeJev({"restates_t01": 0.8, "realistic_t02": 0.3, "gradeable_has_next_step": 0.1})
    monkeypatch.setattr(suite_check, "get_decision_backend", lambda: fake)
    result = suite_check.check("email-polisher")
    labels = sorted((i["where"], i["message"].split(":")[0]) for i in result["issues"] if i["message"].startswith("jev_"))
    assert labels == [("has_next_step", "jev_not_gradeable"), ("t01", "jev_restatement"), ("t02", "jev_unrealistic")]
    assert len(fake.calls) == 1 and set(fake.calls[0][0]["tasks"]) == {"t01", "t02"}
    assert result["jev"] == {"input_tokens": 2000, "usd": 0.000084}
    assert result["ok"] is True


def test_jev_down_still_returns_other_checks(adopted, monkeypatch):
    write_suite()
    monkeypatch.setenv("TYPESAFE_API_KEY", "user-key")
    monkeypatch.setattr(suite_check, "get_decision_backend", lambda: FakeJev(fail=True))
    result = suite_check.check("email-polisher")
    assert result["ok"] is True and result["jev"] == {"unavailable": "timeout"}


def test_no_key_no_jev(adopted, monkeypatch):
    write_suite()
    monkeypatch.setattr(suite_check, "get_decision_backend", lambda: pytest.fail("JEV must not be called"))
    assert "jev" not in suite_check.check("email-polisher")


def test_slug_rules():
    assert suite.suite_dir("My Skill").name == "My Skill"
    for bad in ("../x", "a/b", "..", "", "a\\b"):
        with pytest.raises(ValueError, match="invalid skill slug"):
            suite.suite_dir(bad)
