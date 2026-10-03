import json

import pytest
from fakes import RUBRIC, TASKS
from helpers import install_fixture_skills

from skillswiki import discovery, library
from skillswiki.evals import generator, suite


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
    fake = _use(monkeypatch, Replies(json.dumps({"tasks": TASKS, "rubric": RUBRIC})))
    result = generator.generate("email-polisher")
    assert result["tasks"] == 2 and result["tokens"] == 1200
    assert len(suite.load_tasks("email-polisher")) == 2
    assert suite.status("email-polisher")["status"] == "draft"
    assert "Rewrites draft emails" in fake.prompts[0] and "<<SKILL>>" not in fake.prompts[0]
    assert '"skill": "email-polisher"' in fake.prompts[0]


def test_invalid_twice_raises_naming_the_field(adopted, monkeypatch):
    bad_rubric = {**RUBRIC, "dimensions": RUBRIC["dimensions"][:2]}
    wrong_slug = [{**TASKS[0], "skill": "other"}]
    _use(monkeypatch, Replies(json.dumps({"tasks": TASKS, "rubric": bad_rubric}),
                              json.dumps({"tasks": wrong_slug, "rubric": RUBRIC})))
    with pytest.raises(ValueError, match="skill must be 'email-polisher'"):
        generator.generate("email-polisher")
    assert not suite.exists("email-polisher")


def test_retry_recovers(adopted, monkeypatch):
    fake = _use(monkeypatch, Replies("I can't", "```json\n" + json.dumps({"tasks": TASKS, "rubric": RUBRIC}) + "\n```"))
    generator.generate("email-polisher")
    assert len(fake.prompts) == 2


def test_existing_suite_refused(adopted, monkeypatch):
    _use(monkeypatch, Replies(json.dumps({"tasks": TASKS, "rubric": RUBRIC})))
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
