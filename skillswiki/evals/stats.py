"""Statistics for the eval engine (P1.4d, grading v2): Wilson confidence
intervals, a paired bootstrap delta interval, verdict rules, and the
findings that explain a scorecard number instead of just showing it.
Stdlib only (math, random, statistics)."""
import math
import random


def wilson_ci(successes: int, n: int, z: float = 1.96) -> tuple[float, float] | None:
    if n <= 0:
        return None
    p = successes / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, center - half), min(1.0, center + half))


def bootstrap_delta_ci(
    per_task_a: dict[str, list[bool]],
    per_task_b: dict[str, list[bool]],
    resamples: int = 1000,
    seed: int = 0,
) -> tuple[float, float] | None:
    ids = [t for t in sorted(set(per_task_a) & set(per_task_b)) if per_task_a[t] and per_task_b[t]]
    if not ids:
        return None
    d = {t: sum(per_task_a[t]) / len(per_task_a[t]) - sum(per_task_b[t]) / len(per_task_b[t]) for t in ids}
    rng = random.Random(seed)
    draws = []
    for _ in range(resamples):
        sample = rng.choices(ids, k=len(ids))  # with replacement, same size
        draws.append(100.0 * sum(d[t] for t in sample) / len(sample))
    draws.sort()
    return (draws[int(0.025 * resamples)], draws[int(0.975 * resamples) - 1])  # indexes 25 and 974


def normalized_gain(p_a: float, p_b: float) -> float | None:
    if p_b >= 1:
        return None
    return (p_a - p_b) / (1 - p_b)


def low_signal_tasks(per_task_baseline: dict[str, list[bool]]) -> list[str]:
    return sorted(t for t, runs in per_task_baseline.items() if runs and all(runs))


def pass_rate_excluding(per_task: dict[str, list[bool]], exclude: set[str]) -> float | None:
    runs = [r for t, rs in per_task.items() if t not in exclude for r in rs]
    if not runs:
        return None
    return sum(runs) / len(runs)


def pairwise_verdict(wins: int, losses: int, min_decisive: int = 5) -> str:
    if wins + losses < min_decisive:
        return "noise"
    ci = wilson_ci(wins, wins + losses)
    if ci is None:
        return "noise"
    lo, hi = ci
    if lo > 0.5:
        return "gain"
    if hi < 0.5:
        return "loss"
    return "noise"


def band_verdict(delta_pp: float | None, band_pp: float) -> str | None:
    # Inclusive at the band.
    if delta_pp is None:
        return None
    if delta_pp >= band_pp:
        return "gain"
    if delta_pp <= -band_pp:
        return "loss"
    return "noise"


_FINDINGS_DEFAULTS = {
    "low_headroom_below": 15,
    "packaging_below": 0.8,
    "check_gap_min": 0.3,
    "cost_ratio_above": 5,
    "low_signal_fraction": 0.5,
}


def findings(result: dict, config: dict) -> list[dict]:
    thresholds = {**_FINDINGS_DEFAULTS, **(config.get("findings") or {})}
    out: list[dict] = []

    stats = result.get("stats") or {}
    arms = result.get("arms") or {}
    cost = result.get("cost") or {}

    headroom = stats.get("headroom")
    if headroom is not None and headroom < thresholds["low_headroom_below"]:
        no_skill_rubric = 100 - headroom
        out.append({
            "code": "LOW_HEADROOM",
            "text": (
                f"The no-skill baseline already scores {no_skill_rubric:.1f}/100 on the "
                f"rubric; only {headroom:.1f} points of headroom remain, so a large gain "
                "is not possible on this task set."
            ),
            "value": headroom,
        })

    skill_check_rates = (arms.get("skill") or {}).get("check_rates") or {}
    paste_ready_rate = skill_check_rates.get("paste_ready")
    if paste_ready_rate is not None and paste_ready_rate < thresholds["packaging_below"]:
        out.append({
            "code": "PACKAGING",
            "text": (
                f"Only {paste_ready_rate:.0%} of skill-arm outputs were paste-ready; the "
                "rest wrapped the deliverable in drafts, notes, or change lists. Scores "
                "shown are for the extracted deliverable."
            ),
            "value": paste_ready_rate,
        })

    no_skill_check_rates = (arms.get("no_skill") or {}).get("check_rates") or {}
    common_ids = (set(skill_check_rates) & set(no_skill_check_rates)) - {"paste_ready"}
    if common_ids:
        gaps = {cid: skill_check_rates[cid] - no_skill_check_rates[cid] for cid in common_ids}
        worst_id = max(gaps, key=lambda cid: abs(gaps[cid]))
        if abs(gaps[worst_id]) >= thresholds["check_gap_min"]:
            out.append({
                "code": f"CHECK_GAP:{worst_id}",
                "text": (
                    f"Check '{worst_id}' passes {skill_check_rates[worst_id]:.0%} with the "
                    f"skill vs {no_skill_check_rates[worst_id]:.0%} without."
                ),
                "value": gaps[worst_id],
            })

    cost_ratio = cost.get("skill_vs_no_skill_ratio")
    if cost_ratio is not None and cost_ratio > thresholds["cost_ratio_above"]:
        out.append({
            "code": "COST_RATIO",
            "text": (
                f"The skill arm used {cost_ratio:.1f}× the executor tokens of the "
                "no-skill arm per output."
            ),
            "value": cost_ratio,
        })

    low_signal = stats.get("low_signal_tasks") or []
    tasks_n = result.get("tasks")
    if tasks_n and len(low_signal) >= thresholds["low_signal_fraction"] * tasks_n:
        delta_signal = stats.get("delta_pp_signal")
        d_str = f"{delta_signal:+.1f}" if delta_signal is not None else "—"
        out.append({
            "code": "LOW_SIGNAL_TASKS",
            "text": (
                f"{len(low_signal)} of {tasks_n} tasks were passed on every no-skill run; "
                f"they cannot show a gain (delta_pp on the remaining tasks: {d_str})."
            ),
            "value": len(low_signal),
        })

    return out
