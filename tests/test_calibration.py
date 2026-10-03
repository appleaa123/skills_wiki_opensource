"""Verifies JEV Phase 2 P2.2 (revised 2026-09-26, single-judge design):
evals/calibration.py — per (pack, judge, criterion) trust is computed from
PAIRED verdicts against that one judge. A criterion is trusted at the most
permissive certainty threshold where, with 95% confidence, JEV disagrees with
the judge no more than two strong judges disagree with each other (pass/fail
and exact 0/1/2 level), and JEV does not lean one way in any arm. Synthetic
data only."""
import json
import sys
from pathlib import Path

import pytest


from skillswiki.evals import calibration as cal  # noqa: E402

CONFIG = {"ladder": [0.95, 0.9, 0.8, 0.7, 0.6], "fail_only_kinds": ["factual", "constraint"], "untagged_kind": "factual",
          "baseline_disagreement": 0.21, "exact_baseline": 0.334, "lean_limit": 0.1, "min_per_arm": 8}


def _rows(n, p2, jev, llm, arm="skill"):
    return [{"p2": p2, "jev": jev, "llm": llm, "arm": arm} for _ in range(n)]


def _agreeing(n_per_arm=20):
    rows = []
    for arm in ("skill", "no_skill"):
        rows += _rows(n_per_arm // 2, 0.99, 2, 2, arm) + _rows(n_per_arm // 2, 0.02, 0, 0, arm)
    return rows


def test_rule_kinds_never_get_a_threshold():
    for props in ({"kind": "legal"}, {"kind": "count"}, {"risk": "high"}):
        e = cal.evaluate(_agreeing(), props, CONFIG)
        assert e["status"] == "rule" and e["threshold"] is None


def test_good_evidence_trusts_the_most_permissive_threshold():
    e = cal.evaluate(_agreeing(), {"kind": "style"}, CONFIG)
    assert (e["status"], e["threshold"]) == ("calibrated", 0.6)


def test_too_little_evidence_stays_cold():
    e = cal.evaluate(_agreeing(n_per_arm=6), {"kind": "style"}, CONFIG)
    assert (e["status"], e["threshold"]) == ("cold", None)


def test_confident_disagreement_escalates():
    rows = _agreeing() + _rows(10, 0.99, 2, 0, "skill") + _rows(10, 0.99, 2, 0, "no_skill")
    e = cal.evaluate(rows, {"kind": "style"}, CONFIG)
    assert (e["status"], e["threshold"]) == ("escalate", None)


def test_uncertain_disagreement_only_raises_the_threshold():
    rows = _agreeing() + _rows(10, 0.65, 2, 0, "skill") + _rows(10, 0.65, 2, 0, "no_skill")
    e = cal.evaluate(rows, {"kind": "style"}, CONFIG)
    assert (e["status"], e["threshold"]) == ("calibrated", 0.7)


def test_exact_level_disagreement_counts_even_when_pass_fail_agrees():
    rows = []
    for arm in ("skill", "no_skill"):
        rows += _rows(10, 0.02, 0, 1, arm) + _rows(10, 0.99, 2, 2, arm)  # 0 vs 1: both "fail"
    assert cal.evaluate(rows, {"kind": "style"}, CONFIG)["status"] == "escalate"


def test_one_sided_lean_in_one_arm_blocks_trust():
    # JEV says 1 where the judge says 0: pass/fail agrees, but the no_skill arm's score leans up
    small = _agreeing(40) + _rows(4, 0.02, 1, 0, "no_skill")   # lean 4/44 = 0.09: within 0.1
    assert cal.evaluate(small, {"kind": "style"}, CONFIG)["status"] == "calibrated"
    large = _agreeing(20) + _rows(6, 0.02, 1, 0, "no_skill")   # lean 6/26 = 0.23: too one-sided
    assert cal.evaluate(large, {"kind": "style"}, CONFIG)["status"] == "escalate"


def test_fail_only_kinds_are_measured_on_jev_fail_verdicts():
    rows = []
    for arm in ("skill", "no_skill"):
        rows += _rows(10, 0.02, 0, 0, arm)  # JEV fails, judge agrees
        rows += _rows(10, 0.99, 2, 0, arm)  # JEV wrongly passes: never final for factual (LLM confirms)
    assert cal.evaluate(rows, {"kind": "factual"}, CONFIG)["status"] == "calibrated"
    assert cal.evaluate(rows, {"kind": "style"}, CONFIG)["status"] == "escalate"
    assert cal.evaluate(rows, {}, CONFIG)["status"] == "calibrated"  # untagged = factual: measured on fails only


def test_store_roundtrip(tmp_path):
    store = cal.load("demo", tmp_path)
    assert store["judges"] == {} and cal.entry(store, "claude", "c") is None
    store["judges"]["claude"] = {"c": {"status": "calibrated", "threshold": 0.8}}
    cal.save(store, tmp_path)
    assert cal.entry(cal.load("demo", tmp_path), "claude", "c")["threshold"] == 0.8


def test_paired_rows_from_a_cascade_result(tmp_path):
    result = {"pack": "demo", "cascade": {"judge_key": "claude"}}
    rec = {"arm": "skill", "task_id": "t1", "run_index": 0,
           "rubric_scale": {"a": 1, "b": 2}, "applied_scale": {"c": 0},
           "judge_detail": {"a": {"level": 2, "p2": 0.9}, "b": {"level": 2, "p2": 0.99}, "c": {"level": 0, "p2": 0.1}},
           "cascade": {"a": {"final_by": "llm", "reason": "audit"}, "b": {"final_by": "jev", "reason": "jev"},
                       "c": {"final_by": "verify", "reason": "verify:fail"}}}
    (tmp_path / "run.json").write_text(json.dumps(result))
    (tmp_path / "run.outputs.jsonl").write_text(json.dumps(rec) + "\n")
    judge, rows = cal.paired_rows(tmp_path / "run.json")
    assert judge == "claude"
    assert rows == [{"judge": "claude", "criterion": "a", "jev": 2, "llm": 1, "p2": 0.9, "arm": "skill",
                     "key": "run.json|skill|t1|0|a"}]


def test_update_ingests_once_and_reevaluates():
    store = {"pack": "demo", "updated": None, "runs": [], "judges": {}, "rows": {}}
    rows = [{"criterion": "c", **r} for r in _agreeing()]
    cal.update(store, "run1.json", "claude", rows, {"c": {"kind": "style"}}, CONFIG)
    assert store["judges"]["claude"]["c"]["status"] == "calibrated"
    assert len(store["rows"]["claude"]["c"]) == 40 and store["runs"] == ["run1.json"]
    with pytest.raises(SystemExit):
        cal.update(store, "run1.json", "claude", rows, {"c": {"kind": "style"}}, CONFIG)


def test_saved_store_keeps_one_paired_row_per_line(tmp_path):
    store = {"pack": "demo", "updated": None, "runs": ["seed:phase1"],
             "judges": {"claude": {"c": {"status": "cold", "threshold": None, "lean": {"skill": 0.1}}}},
             "rows": {"claude": {"c": [[2, 2, 0.99, "skill"], [0, 1, 0.02, "no_skill"]]}}}
    path = cal.save(store, tmp_path)
    text = path.read_text()
    assert '[2, 2, 0.99, "skill"]' in text and '[0, 1, 0.02, "no_skill"]' in text
    loaded = cal.load("demo", tmp_path)
    assert loaded["rows"] == store["rows"] and loaded["judges"] == store["judges"]
