import json

import pytest

from skillswiki import paths, store
from skillswiki.evals import ratchet_local

BASE = {"pack": "s", "executor": "claude", "executor_model": "sonnet", "judge": "codex", "judge_model": None,
        "composite": 80.0, "rubric_score": 80.0, "delta_pp": 30.0, "profile": {"hash": "p1"},
        "grading": {"hash": "g1"}, "token_usage": {"total": 1234},
        "verdicts": {"skill_vs_no_skill": {"verdict": "gain", "basis": "pairwise"}},
        "stats": {"delta_pp_ci95": [10.0, 50.0]}, "pairwise": {"skill_vs_no_skill": {"wins": 5, "losses": 1, "ties": 0}}}


def _write(name, **over):
    path = paths.results_dir() / "s" / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({**BASE, **over}))
    return path


def test_record_logs_row_and_usage(tmp_home):
    eval_id = ratchet_local.record(_write("a"), "screen")
    rows = ratchet_local.report("s")
    assert rows[0]["id"] == eval_id and rows[0]["verdict"] == "gain" and rows[0]["tokens_total"] == 1234
    assert rows[0]["ci"] == [10.0, 50.0] and rows[0]["pairwise"] == {"wins": 5, "losses": 1, "ties": 0}
    with store.connect() as conn:
        assert conn.execute("SELECT tokens_est FROM usage WHERE event = 'eval'").fetchone()[0] == 1234


def test_first_accept_bootstraps_then_compares(tmp_home):
    first = ratchet_local.record(_write("a"), "publish")
    assert ratchet_local.accept(first, 8)["comparable"] is False
    second = ratchet_local.record(_write("b", rubric_score=60.0), "publish")
    comparison = ratchet_local.compare(second, 8)
    assert comparison == {"comparable": True, "metric": "rubric_score", "accepted_value": 80.0, "new_value": 60.0,
                          "regression": True}
    assert ratchet_local.accept(second, 8)["regression"] is True  # reported, not blocked
    assert ratchet_local.latest_accepted("s")["id"] == second


def test_different_judge_refused_unless_forced(tmp_home):
    ratchet_local.accept(ratchet_local.record(_write("a"), "publish"), 8)
    other = ratchet_local.record(_write("b", judge="gemini"), "publish")
    with pytest.raises(ValueError, match="--force"):
        ratchet_local.accept(other, 8)
    assert ratchet_local.accept(other, 8, force=True)["accepted"] == other


def test_grading_change_names_the_reason(tmp_home):
    ratchet_local.accept(ratchet_local.record(_write("a"), "publish"), 8)
    other = ratchet_local.record(_write("b", grading={"hash": "g2"}), "publish")
    assert "different rule set" in ratchet_local.compare(other, 8)["reason"]


def test_unknown_eval(tmp_home):
    with pytest.raises(ValueError, match="not found"):
        ratchet_local.accept(99, 8)
