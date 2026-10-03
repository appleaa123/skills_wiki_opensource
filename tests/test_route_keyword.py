import json

import pytest
from helpers import install_fixture_skills

from skillswiki import cards, discovery, library, store
from skillswiki.route import keyword


@pytest.fixture
def adopted(tmp_home):
    install_fixture_skills(tmp_home / "native")
    discovery.sync_db()
    for slug in ("email-polisher", "meeting-notes", "csv-cleaner"):
        library.adopt(slug)


def test_tokenize_lowercases_drops_stopwords_and_strips_plurals():
    assert keyword.tokenize("Fix the Emails, please!") == ["fix", "email"]
    assert keyword.tokenize("a I x") == []
    assert keyword.tokenize("class address") == ["class", "address"]


def test_description_match(adopted):
    result = keyword.suggest_keyword("fix my email draft")
    assert result["mode"] == "keyword" and result["skill"] is None
    assert result["reason"] == "shortlist"
    assert result["shortlist"][0]["skill"] == "email-polisher"


def test_no_match_without_card_then_match_with_card(adopted):
    request = "it reads robotic, make it human"
    assert keyword.suggest_keyword(request)["reason"] == "no_match"
    cards.set_manual("email-polisher", examples=["this reads robotic, fix it"], keywords=["robotic", "human tone"],
                     not_for=[])
    result = keyword.suggest_keyword(request)
    assert result["shortlist"][0]["skill"] == "email-polisher"


def test_not_for_penalty(adopted):
    cards.set_manual("email-polisher", examples=["translate this email to French"], keywords=[],
                     not_for=["translate an email into another language"])
    plain = keyword.shortlist("translate this email")
    assert plain and plain[0][0] == "email-polisher"
    with_penalty = dict(plain)["email-polisher"]
    cards.set_manual("email-polisher", examples=["translate this email to French"], keywords=[], not_for=[])
    without_penalty = dict(keyword.shortlist("translate this email"))["email-polisher"]
    assert with_penalty == pytest.approx(without_penalty / 2)


def test_only_adopted_skills_are_routed(tmp_home):
    install_fixture_skills(tmp_home / "native")
    discovery.sync_db()
    assert keyword.shortlist("fix my email draft") == []


def test_logs_routing_and_usage(adopted):
    keyword.suggest_keyword("clean this csv file and dedupe rows")
    keyword.suggest_keyword("zzz qqq")
    with store.connect() as conn:
        logs = [dict(r) for r in conn.execute("SELECT * FROM routing_log ORDER BY id")]
        usage = [dict(r) for r in conn.execute("SELECT * FROM usage")]
    assert [r["reason"] for r in logs] == ["shortlist", "no_match"]
    assert json.loads(logs[0]["shortlist"])[0]["skill"] == "csv-cleaner"
    assert logs[0]["mode"] == "keyword"
    assert all(u["event"] == "suggest" for u in usage) and usage[0]["slug"] == "csv-cleaner"


def test_request_excerpt_truncated(adopted):
    keyword.suggest_keyword("email " * 100)
    with store.connect() as conn:
        excerpt = conn.execute("SELECT request_excerpt FROM routing_log").fetchone()[0]
    assert len(excerpt) == keyword.EXCERPT_MAX_CHARS
