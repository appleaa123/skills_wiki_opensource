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
    monkeypatch.setattr(jev_route, "get_decision_backend", lambda deadline=None: backend)
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
    # unrelated skills are now candidates too (small library), so the fake gives them the low fit JEV would
    _use(monkeypatch, FakeBackend({EMAIL_LINE: 0.9}, fit={EMAIL_LINE: 0.6, NOTES_LINE: 0.05, CSV_LINE: 0.05}))
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


def test_routing_has_a_time_budget(adopted, monkeypatch):
    seen = {}

    def fake(deadline=None):
        seen["deadline"] = deadline
        return FakeBackend({}, fail=True)
    monkeypatch.setattr(jev_route, "get_decision_backend", fake)
    monkeypatch.setattr(jev_route.time, "monotonic", lambda: 100.0)
    suggest("please fix my email draft", budget_s=5.0)
    assert seen["deadline"] == 105.0
    suggest("please fix my email draft")
    assert seen["deadline"] == 100.0 + jev_route.DEFAULT_BUDGET_S


def test_backend_gets_the_deadline(monkeypatch):
    from skillswiki.decision import get_decision_backend
    monkeypatch.setenv("TYPESAFE_API_KEY", "k")
    assert get_decision_backend(deadline=42.0)._deadline == 42.0


def test_small_library_sends_every_skill_to_jev(adopted):
    # Found in a real Claude Code run: a weak keyword hit on one skill used to hide the right skill from JEV.
    cands = jev_route.candidates("help me get started on my notes", jev_route._adopted())
    assert set(cands) == {"email-polisher", "meeting-notes", "csv-cleaner"}
    assert cands[0] == "meeting-notes"  # keyword matches still come first


def test_large_library_pads_keyword_hits_with_recent_skills(adopted, monkeypatch):
    monkeypatch.setattr(jev_route, "PREFILTER_K", 2)
    cands = jev_route.candidates("my notes", jev_route._adopted())
    assert cands[0] == "meeting-notes" and len(cands) == 2
