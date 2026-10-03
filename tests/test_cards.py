import json

import pytest
from helpers import install_fixture_skills

from skillswiki import cards, discovery
from skillswiki.evals.backends import BackendUnavailable


@pytest.fixture
def skills(tmp_home):
    install_fixture_skills(tmp_home / "native")
    discovery.sync_db()


class FakeBackend:
    def __init__(self, replies):
        self.replies = list(replies)
        self.prompts = []

    def judge(self, prompt, model, timeout=60, profile=None):
        self.prompts.append(prompt)
        return {"text": self.replies.pop(0), "usage": {"input_tokens": 10, "output_tokens": 5}}


GOOD = {"examples": ["fix this email"], "keywords": ["email", "tone"], "not_for": ["translation"]}


def test_set_get_delete(skills):
    assert cards.get("email-polisher") is None
    cards.set_manual("email-polisher", **GOOD)
    card = cards.get("email-polisher")
    assert card["examples"] == ["fix this email"] and card["source"] == "manual"
    cards.delete("email-polisher")
    assert cards.get("email-polisher") is None


@pytest.mark.parametrize("bad", [
    {"examples": "not a list", "keywords": [], "not_for": []},
    {"examples": [1], "keywords": [], "not_for": []},
    {"examples": ["x"] * 16, "keywords": [], "not_for": []},
    {"examples": ["x" * 201], "keywords": [], "not_for": []},
])
def test_validation(skills, bad):
    with pytest.raises(ValueError):
        cards.set_manual("email-polisher", **bad)


def test_unknown_skill(skills):
    with pytest.raises(ValueError, match="not found"):
        cards.set_manual("nope", **GOOD)


def test_enrich_stores_card(skills, monkeypatch):
    fake = FakeBackend([json.dumps(GOOD)])
    monkeypatch.setattr(cards, "get_backend", lambda name: fake)
    card = cards.enrich("email-polisher")
    assert card["source"] == "enrich" and card["keywords"] == ["email", "tone"]
    assert "Rewrites draft emails" in fake.prompts[0]


def test_enrich_accepts_fenced_json_and_retries_once(skills, monkeypatch):
    fake = FakeBackend(["sorry, here you go", "```json\n" + json.dumps(GOOD) + "\n```"])
    monkeypatch.setattr(cards, "get_backend", lambda name: fake)
    assert cards.enrich("email-polisher")["examples"] == ["fix this email"]
    assert len(fake.prompts) == 2 and "Return only the JSON object" in fake.prompts[1]


def test_enrich_bad_json_twice_raises(skills, monkeypatch):
    fake = FakeBackend(["nope", "still nope"])
    monkeypatch.setattr(cards, "get_backend", lambda name: fake)
    with pytest.raises(ValueError, match="did not return a valid routing card"):
        cards.enrich("email-polisher")
    assert cards.get("email-polisher") is None


def test_enrich_backend_failure_propagates(skills, monkeypatch):
    class Down:
        def judge(self, *a, **k):
            raise BackendUnavailable("claude -p exited 1")
    monkeypatch.setattr(cards, "get_backend", lambda name: Down())
    with pytest.raises(BackendUnavailable):
        cards.enrich("email-polisher")
