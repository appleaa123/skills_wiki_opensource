"""Verifies JEV Phase 1's gate statistics (evals/jev_stats.py), per the
human-approved pre-registration of 2026-09-25: binary (>=2) pass/fail
Cohen's kappa as the gate metric, informative-criterion rule, task-level
cluster bootstrap CIs, and escalation measured on the pass/fail margin."""
import sys
from pathlib import Path

import pytest


from skillswiki.evals import jev_stats as js  # noqa: E402


def test_qwk_perfect_and_hand_computed():
    assert js.qwk([0, 1, 2, 2, 0], [0, 1, 2, 2, 0]) == pytest.approx(1.0)
    # O: one (0,2) disagreement, weight 1 -> observed 1/4; expected (6+2)/16 -> kappa 0.5
    assert js.qwk([0, 0, 2, 2], [0, 2, 2, 2]) == pytest.approx(0.5)
    assert js.qwk([2, 2, 2], [2, 2, 2]) is None


def test_kappa2_hand_computed():
    # 2x2: both pass 4, both fail 3, ref pass/jev fail 1, ref fail/jev pass 2 (n=10)
    ref = [True] * 5 + [False] * 5
    jev = [True] * 4 + [False] + [True] * 2 + [False] * 3
    # po = 0.7; pe = 0.5*0.6 + 0.5*0.4 = 0.5 -> kappa = 0.4
    assert js.kappa2(ref, jev) == pytest.approx(0.4)


def test_kappa2_always_pass_judge_scores_zero_not_97_percent():
    ref = [True] * 58 + [False] * 2
    lazy = [True] * 60
    assert js.binary_agreement([2 if r else 0 for r in ref], [2] * 60) == pytest.approx(58 / 60)
    assert js.kappa2(ref, lazy) == pytest.approx(0.0)


def test_kappa2_undefined_when_both_constant():
    assert js.kappa2([True] * 5, [True] * 5) is None


@pytest.mark.parametrize("levels,expected", [
    ([2] * 60, False),                 # zero variance
    ([2] * 58 + [0] * 2, False),       # minority below MIN_MINORITY
    ([2] * 57 + [0] * 3, True),
    ([0] * 30 + [1] * 30, False),      # all fail at the >=2 cut
])
def test_informative_rule(levels, expected):
    assert js.informative(levels) is expected


def test_margin_is_pass_fail_certainty():
    assert js.margin(0.7) == pytest.approx(0.7)
    assert js.margin(0.1) == pytest.approx(0.9)   # certain FAIL is high margin
    assert js.margin(0.5) == pytest.approx(0.5)


def _row(margin, ref, jev, task="t"):
    return {"margin": margin, "llm": ref, "jev": jev, "cluster": task}


def test_threshold_table_on_margin_by_hand():
    rows = [_row(0.55, 2, 0), _row(0.65, 2, 2), _row(0.75, 2, 0), _row(0.95, 2, 2)]
    table = {t["threshold"]: t for t in js.threshold_table(rows, (0.6, 0.7, 0.9))}
    assert table[0.6]["escalation"] == pytest.approx(0.25)
    assert table[0.6]["residual_binary"] == pytest.approx(1 / 3)
    assert table[0.7]["escalation"] == pytest.approx(0.5)
    assert table[0.7]["residual_binary"] == pytest.approx(0.5)
    assert table[0.9]["escalation"] == pytest.approx(0.75)
    assert table[0.9]["residual_binary"] == pytest.approx(0.0)


def test_decile_table_covers_rows_and_handles_small_n():
    rows = [_row(0.5 + c / 40, 2, 2 if c > 5 else 0) for c in range(20)]
    bins = js.decile_table(rows)
    assert len(bins) == 10 and sum(b["n"] for b in bins) == 20
    assert bins[0]["disagree_binary"] == pytest.approx(1.0)
    assert bins[-1]["disagree_binary"] == pytest.approx(0.0)
    few = js.decile_table([_row(0.6, 2, 2), _row(0.6, 2, 0), _row(0.9, 0, 0)])
    assert sum(b["n"] for b in few) == 3


def test_cluster_bootstrap_resamples_tasks_and_is_seeded():
    # 10 tasks, each with 3 near-duplicate rows: perfect agreement on 8 tasks, disagreement on 2.
    rows = []
    for t in range(10):
        agree = t < 8
        rows += [{"llm": 2 if t % 2 else 0, "jev": (2 if t % 2 else 0) if agree else (0 if t % 2 else 2),
                  "cluster": f"t{t}"} for _ in range(3)]
    stat = lambda rs: js.kappa2([r["llm"] >= 2 for r in rs], [r["jev"] >= 2 for r in rs])  # noqa: E731
    ci = js.cluster_bootstrap_ci(rows, stat, reps=500, seed=3)
    assert ci == js.cluster_bootstrap_ci(rows, stat, reps=500, seed=3)
    lo, hi = ci
    assert -1.0 <= lo < stat(rows) < hi <= 1.0


def test_cluster_bootstrap_none_when_stat_always_undefined():
    rows = [{"llm": 2, "jev": 2, "cluster": f"t{i}"} for i in range(5)]
    stat = lambda rs: js.kappa2([True] * len(rs), [True] * len(rs))  # noqa: E731
    assert js.cluster_bootstrap_ci(rows, stat, reps=50, seed=1) is None


def test_det_cross_check_is_one_directional_and_lists_rows():
    rows = [
        {"pack": "p", "criterion": "cta", "det": {"chk": False}, "llm": 2, "jev": 0, "key": "a"},
        {"pack": "p", "criterion": "cta", "det": {"chk": False}, "llm": 0, "jev": 2, "key": "b"},
        {"pack": "p", "criterion": "cta", "det": {"chk": True}, "llm": 2, "jev": 2, "key": "c"},
    ]
    (out,) = js.det_cross_check(rows, {"p": {"chk": ["cta"]}})
    assert out["n_flags"] == 2
    assert out["ref_scored_2"] == pytest.approx(0.5)
    assert out["jev_scored_2"] == pytest.approx(0.5)
    assert out["keys"] == ["a", "b"]
