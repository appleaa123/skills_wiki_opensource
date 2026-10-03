import json

import pytest
from fakes import RUBRIC, TASKS
from helpers import install_fixture_skills

from skillswiki import discovery, library
from skillswiki.evals import generator, suite

# Generated suites must tag every criterion (owner decision 2026-10-03); RUBRIC (hand-written style) leaves kinds out.
TAGGED = {g: [{**c, "kind": "factual"} for c in RUBRIC[g]] for g in ("dimensions", "items", "applied")}
TAGGED["items"] = [{**RUBRIC["items"][0], "kind": "style"}]


class Replies:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.prompts = []

    def judge(self, prompt, model, timeout=60, profile=None):
        self.prompts.append(prompt)
        return {"text": self.replies.pop(0), "usage": {"input_tokens": 900, "output_tokens": 300}}


@pytest.fixture
def adopted(tmp_home):
    install_fixture_skills(tmp_home / "native")
    discovery.sync_db()
    library.adopt("email-polisher")


def _use(monkeypatch, fake):
    monkeypatch.setattr(generator, "get_backend", lambda name: fake)
    return fake


def test_generate_writes_draft(adopted, monkeypatch):
    fake = _use(monkeypatch, Replies(json.dumps({"tasks": TASKS, "rubric": TAGGED})))
    result = generator.generate("email-polisher")
    assert result["tasks"] == 2 and result["tokens"] == 1200
    assert len(suite.load_tasks("email-polisher")) == 2
    assert suite.status("email-polisher")["status"] == "draft"
    assert "Rewrites draft emails" in fake.prompts[0] and "<<SKILL>>" not in fake.prompts[0]
    assert '"skill": "email-polisher"' in fake.prompts[0]


def test_invalid_twice_raises_naming_the_field(adopted, monkeypatch):
    bad_rubric = {**TAGGED, "dimensions": TAGGED["dimensions"][:2]}
    wrong_slug = [{**TASKS[0], "skill": "other"}]
    _use(monkeypatch, Replies(json.dumps({"tasks": TASKS, "rubric": bad_rubric}),
                              json.dumps({"tasks": wrong_slug, "rubric": TAGGED})))
    with pytest.raises(ValueError, match="skill must be 'email-polisher'"):
        generator.generate("email-polisher")
    assert not suite.exists("email-polisher")


def test_retry_recovers(adopted, monkeypatch):
    fake = _use(monkeypatch, Replies("I can't", "```json\n" + json.dumps({"tasks": TASKS, "rubric": TAGGED}) + "\n```"))
    generator.generate("email-polisher")
    assert len(fake.prompts) == 2


def test_existing_suite_refused(adopted, monkeypatch):
    _use(monkeypatch, Replies(json.dumps({"tasks": TASKS, "rubric": TAGGED})))
    generator.generate("email-polisher")
    with pytest.raises(ValueError, match="already exists"):
        generator.generate("email-polisher")


def test_validate_reports_each_problem():
    errors = generator.validate("s", [{"id": "a", "skill": "s", "prompt": "", "inputs": [], "verifier": "x"}],
                                {"dimensions": [], "items": [{"id": "i"}]})
    joined = " | ".join(errors)
    for needle in ("prompt missing", "inputs must be an object", "verifier must be", "failure_mechanism",
                   "string id and desc"):
        assert needle in joined


def test_generated_criteria_must_be_tagged(adopted, monkeypatch):
    _use(monkeypatch, Replies(json.dumps({"tasks": TASKS, "rubric": RUBRIC}), json.dumps({"tasks": TASKS, "rubric": RUBRIC})))
    with pytest.raises(ValueError, match="needs kind"):
        generator.generate("email-polisher")


def test_kinds_reported_and_risky_style_demoted(adopted, monkeypatch):
    risky = {**TAGGED, "applied": [{"id": "pay_review", "desc": "Keeps a human review step before any payment.",
                                     "kind": "style"}]}
    _use(monkeypatch, Replies(json.dumps({"tasks": TASKS, "rubric": risky})))
    result = generator.generate("email-polisher")
    assert result["demoted_to_factual"] == ["pay_review"] and "style" in result["kinds"]  # the item stays style
    assert suite.load_rubric("email-polisher")["applied"][0]["kind"] == "factual"
    assert suite.status("email-polisher")["demoted_to_factual"] == ["pay_review"]


def test_risk_word_style_is_set_back_to_factual():
    rubric = {"items": [{"id": "a", "desc": "Gives no legal advice.", "kind": "style"},
                        {"id": "b", "desc": "Uses a friendly tone.", "kind": "style"},
                        {"id": "c", "desc": "States the refund amount.", "kind": "style"}]}
    assert generator.demote_risky_style(rubric) == ["a", "c"]
    assert [c["kind"] for c in rubric["items"]] == ["factual", "style", "factual"]


def test_hand_written_suites_may_stay_untagged():
    assert generator.validate("email-polisher", TASKS, RUBRIC) == []
    assert "needs kind" in " ".join(generator.validate("email-polisher", TASKS, {**RUBRIC, "items": [
        {"id": "x", "desc": "y", "kind": "vibes"}]}))
