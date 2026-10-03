"""P1.4d Phase 1, task 1.2: Wilson intervals, the paired bootstrap delta
interval, pairwise/band verdict rules, and findings. Check values are pinned
so a future edit can't silently drift them (see the P1.4d plan doc)."""
import sys
from pathlib import Path


from skillswiki.evals.stats import (  # noqa: E402
    band_verdict,
    bootstrap_delta_ci,
    findings,
    low_signal_tasks,
    normalized_gain,
    pairwise_verdict,
    wilson_ci,
)


def test_wilson_zero_of_thirty():
    lo, hi = wilson_ci(0, 30)
    assert abs(lo - 0.0) < 1e-3
    assert abs(hi - 0.1135) < 1e-3


def test_wilson_all_pass_upper_is_one():
    lo, hi = wilson_ci(30, 30)
    assert abs(lo - 0.8865) < 1e-3
    assert hi == 1.0
    # Also pin (12, 12) — a case a future edit could easily get wrong.
    lo12, hi12 = wilson_ci(12, 12)
    assert abs(lo12 - 0.7575) < 1e-3
    assert hi12 == 1.0


def test_wilson_n_zero_is_none():
    assert wilson_ci(0, 0) is None


def test_bootstrap_identical_arms_is_zero_zero():
    a = {f"t{i}": [True, True, True] for i in range(10)}
    lo, hi = bootstrap_delta_ci(a, a)
    assert lo == 0.0
    assert hi == 0.0


def test_bootstrap_full_separation_is_100_100():
    a = {f"t{i}": [True, True, True] for i in range(10)}
    b = {f"t{i}": [False, False, False] for i in range(10)}
    lo, hi = bootstrap_delta_ci(a, b)
    assert lo == 100.0
    assert hi == 100.0


def test_bootstrap_same_seed_is_deterministic():
    a = {"t1": [True, False, True], "t2": [False, False, True]}
    b = {"t1": [False, False, False], "t2": [True, False, False]}
    r1 = bootstrap_delta_ci(a, b, seed=0)
    r2 = bootstrap_delta_ci(a, b, seed=0)
    assert r1 == r2


def test_normalized_gain_none_at_ceiling():
    assert normalized_gain(0.9, 1.0) is None
    assert normalized_gain(0.9, 0.5) == 0.8


def test_low_signal_tasks_all_runs_passed():
    per_task = {
        "t1": [True, True, True],
        "t2": [True, False, True],
        "t3": [True, True, True],
        "t4": [],
    }
    assert low_signal_tasks(per_task) == ["t1", "t3"]


def test_pairwise_verdict_requires_five_decisive():
    # 4 decisive: wilson_ci(4, 4) lower bound is 0.5101 > 0.5 (would say
    # "gain" on the Wilson rule alone) but 4 < min_decisive=5, so this must
    # return "noise" regardless.
    assert pairwise_verdict(4, 0) == "noise"
    assert pairwise_verdict(4, 0, min_decisive=5) == "noise"


def test_pairwise_verdict_thresholds():
    assert pairwise_verdict(12, 0) == "gain"
    assert pairwise_verdict(3, 12) == "loss"
    assert pairwise_verdict(8, 7) == "noise"


def test_band_verdict_matches_ts_learning_verdict():
    assert band_verdict(7.9, 8) == "noise"
    assert band_verdict(8, 8) == "gain"
    assert band_verdict(-8, 8) == "loss"
    assert band_verdict(None, 8) is None


def test_findings_fire_on_humanizer_like_summary():
    result = {
        "tasks": 10,
        "arms": {
            "skill": {"check_rates": {"paste_ready": 0.0, "no_banned_ai_words": 1.0}},
            "no_skill": {"check_rates": {"paste_ready": 1.0, "no_banned_ai_words": 0.89}},
        },
        "stats": {"headroom": 6.7, "low_signal_tasks": [f"t{i}" for i in range(7)], "delta_pp_signal": -30.0},
        "cost": {"skill_vs_no_skill_ratio": 16.0},
        "lint": {},
    }
    codes = [f["code"] for f in findings(result, {})]
    assert codes == ["LOW_HEADROOM", "PACKAGING", "COST_RATIO", "LOW_SIGNAL_TASKS"]


def test_findings_silent_on_empty_result():
    assert findings({}, {}) == []
