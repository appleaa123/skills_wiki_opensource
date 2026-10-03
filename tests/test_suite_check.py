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
