import json

import pytest
from helpers import install_fixture_skills
from test_router import FakeBackend

from skillswiki import cards, discovery, library, store
from skillswiki.route import jev_route, suggest

EMAIL_LINE = "Rewrites draft emails so they sound natural and clear."
NOTES_LINE = "Turns a meeting transcript into decisions, owners and next steps."
CSV_LINE = "Cleans messy CSV files: trims, dedupes, normalises dates."


@pytest.fixture
def adopted(tmp_home, monkeypatch):
    install_fixture_skills(tmp_home / "native")
    discovery.sync_db()
    for slug in ("email-polisher", "meeting-notes", "csv-cleaner"):
        library.adopt(slug)
    monkeypatch.setenv("TYPESAFE_API_KEY", "user-key")


def _use(monkeypatch, backend):
    monkeypatch.setattr(jev_route, "get_decision_backend", lambda: backend)
    return backend


def test_confident_pick_suggests_one_skill(adopted, monkeypatch):
    b = _use(monkeypatch, FakeBackend({EMAIL_LINE: 0.9, NOTES_LINE: 0.05, CSV_LINE: 0.05}, fit={EMAIL_LINE: 0.93}))
    result = suggest("please fix my email draft")
    assert result["mode"] == "jev" and result["skill"] == "email-polisher" and result["reason"] == "suggested"
    assert result["confidence"] == pytest.approx(0.93) and result["jev_input_tokens"] == 300
    assert "candidates" in b.calls[-1][0] and "# Email polisher" in json.dumps(b.calls[-1][0])
    with store.connect() as conn:
        row = conn.execute("SELECT * FROM routing_log").fetchone()
    assert (row["mode"], row["suggested"], row["reason"]) == ("jev", "email-polisher", "suggested")


def test_below_bar_returns_shortlist_only(adopted, monkeypatch):
    _use(monkeypatch, FakeBackend({EMAIL_LINE: 0.9}, fit={EMAIL_LINE: 0.6}))
    result = suggest("please fix my email draft")
    assert result["skill"] is None and result["reason"] == "below_bar"
    assert result["shortlist"][0]["skill"] == "email-polisher" and "shortlist[0]" in result["note"]


def test_gates_say_no_skill(adopted, monkeypatch):
    _use(monkeypatch, FakeBackend({}, gate_p=0.1))
    result = suggest("what's 2+2, quickly?")
    assert result["skill"] is None and result["reason"] == "gated" and result["shortlist"] == []


def test_jev_down_falls_back_to_keyword(adopted, monkeypatch):
    _use(monkeypatch, FakeBackend({}, fail=True))
    result = suggest("please fix my email draft")
    assert result["mode"] == "keyword" and result["fallback"] == "keyword"
    assert result["shortlist"][0]["skill"] == "email-polisher"


def test_no_key_uses_keyword(adopted, monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY")
    assert suggest("please fix my email draft")["mode"] == "keyword"


def test_switch_off_uses_keyword(adopted, monkeypatch):
    monkeypatch.setenv("SKILLSWIKI_JEV", "off")
    assert suggest("please fix my email draft")["mode"] == "keyword"


def test_card_examples_go_into_the_line(adopted):
    cards.set_manual("email-polisher", examples=["this reads robotic", "make it human", "fix tone", "x"],
                     keywords=[], not_for=[])
    skills = jev_route._adopted()
    line = jev_route.catalog(["email-polisher"], skills)["skills"]["email-polisher"]["line"]
    assert line == EMAIL_LINE + " Examples: this reads robotic; make it human; fix tone"


def test_prefilter_caps_candidates(adopted, monkeypatch):
    monkeypatch.setattr(jev_route, "PREFILTER_K", 2)
    skills = {f"s{i}": {} for i in range(5)}
    assert len(jev_route.candidates("zzz unmatched", skills)) == 2
    assert jev_route.candidates("please fix my email draft", jev_route._adopted())[0] == "email-polisher"
