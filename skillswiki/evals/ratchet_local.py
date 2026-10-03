"""Benchmark bookkeeping: record eval results in the local store, accept a run as the new baseline, and
compare a run against the last accepted one. The three comparability helpers are ported verbatim from the
hosted ratchet; `accepted` is {"executor", "executor_model", "judge", "judge_model", "composite",
"results": <full stored result JSON>}.

Locally the user decides: a regression is reported, never blocking. Accepting a run measured under different
conditions (executor/judge/model/profile/grading) needs force=True — an explicit re-baseline.
"""
import json
from pathlib import Path

from skillswiki import store, usage

COMPARISON = "skill_vs_no_skill"


def _grading_mismatch(result: dict, accepted: dict) -> str | None:
    """Why grading rules block a comparison, or None when they don't
    (P1.4d task 5.1). Only enforced when the NEW run carries a grading
    hash — same rule as the profile hash below, and the dashboard's
    bootstrap rule (Phase 6.1) mirrors it: a v2 run vs. a pre-v2 accepted
    row is a re-baseline opportunity, not a silent compare."""
    new_hash = (result.get("grading") or {}).get("hash")
    accepted_hash = ((accepted.get("results") or {}).get("grading") or {}).get("hash")
    if new_hash is None or new_hash == accepted_hash:
        return None
    if accepted_hash is None:
        return f"grading={new_hash}/None: the accepted row predates grading v2"
    return f"grading={new_hash}/{accepted_hash}: the accepted row was graded under a different rule set"


def _same_measurement_conditions(result: dict, accepted: dict) -> bool:
    """P1.4b step 7 (2026-09-09): a regression comparison across different
    executor/judge/model pairs is not a measurement, it's noise — observed
    empirically on marketing_skills, same day, same executor, n=1: judge
    claude scored composite 8.3, judge gemini scored composite 33.3 on the
    identical result set. The ratchet must refuse to compare across that.

    P1.4c (2026-09-09): also refuse across a differing invocation profile.
    Flags (--safe-mode, a pinned model, a short system prompt, no tools) are
    not among executor/executor_model/judge/judge_model, so a lean-profile
    run would otherwise compare cleanly against a heavy-profile accepted
    row despite a completely different effective system prompt — the exact
    failure class this function exists to catch, re-entering through a
    different door. `accepted["results"]["profile"]["hash"]` reads back from
    the full result blob already stored in that jsonb column (no schema
    change). Only enforced when the NEW run carries a profile hash — a row
    published before P1.4c has none, and comparing None==None there would
    silently accept; every P1.4c+ run always carries one.

    P1.4d (2026-09-13): also refuse across a differing grading rule set
    (`grading.hash`) — the same class of bug the profile-hash check above
    already fixed for invocation flags, now for what deliverable
    extraction / judge shuffle / judge_votes / pairwise a result was
    scored under. See `_grading_mismatch`."""
    keys = ("executor", "executor_model", "judge", "judge_model")
    if not all(result.get(k) == accepted.get(k) for k in keys):
        return False

    result_hash = (result.get("profile") or {}).get("hash")
    if result_hash is not None:
        accepted_hash = ((accepted.get("results") or {}).get("profile") or {}).get("hash")
        if result_hash != accepted_hash:
            return False

    if _grading_mismatch(result, accepted) is not None:
        return False
    return True


def _comparison_metric(result: dict, accepted: dict) -> tuple[float, float, str]:
    """Prefer rubric_score (P1.4b step 4) when both sides have one — finer
    grained than composite's pass/fail collapse, so it isn't quantized
    coarser than the regression threshold. Falls back to composite for rows
    published before step 4 added rubric_score, or packs with no rubric
    verifier. accepted["results"] is the full result blob already stored in
    that jsonb column — no schema change needed to read rubric_score back."""
    accepted_results = accepted.get("results") or {}
    new_rs = result.get("rubric_score")
    accepted_rs = accepted_results.get("rubric_score")
    if new_rs is not None and accepted_rs is not None:
        return new_rs, accepted_rs, "rubric_score"
    return result["composite"], accepted["composite"], "composite"


def _row_to_accepted(row) -> dict:
    results = json.loads(row["results"])
    return {"id": row["id"], "executor": results.get("executor"), "executor_model": results.get("executor_model"),
            "judge": results.get("judge"), "judge_model": results.get("judge_model"),
            "composite": results.get("composite"), "results": results}


def record(result_path: Path | str, tier: str | None) -> int:
    """Insert one eval run (from runner._write_result's JSON) and log its tokens. Returns the eval id."""
    result = json.loads(Path(result_path).read_text())
    verdict = ((result.get("verdicts") or {}).get(COMPARISON) or {}).get("verdict")
    tokens = (result.get("token_usage") or {}).get("total") or 0
    with store.connect() as conn:
        eval_id = conn.execute(
            "INSERT INTO evals (slug, tier, result_path, verdict, delta_pp, rubric_score, tokens_total, results, "
            "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (result["pack"], tier, str(result_path), verdict, result.get("delta_pp"), result.get("rubric_score"),
             tokens, json.dumps(result), store.now())).lastrowid
    usage.log(result["pack"], "eval", tokens)
    return int(eval_id)


def _get(eval_id: int):
    with store.connect() as conn:
        row = conn.execute("SELECT * FROM evals WHERE id = ?", (eval_id,)).fetchone()
    if row is None:
        raise ValueError(f"eval #{eval_id} not found")
    return row


def latest_accepted(slug: str) -> dict | None:
    with store.connect() as conn:
        row = conn.execute("SELECT * FROM evals WHERE slug = ? AND accepted = 1 ORDER BY id DESC LIMIT 1",
                           (slug,)).fetchone()
    return _row_to_accepted(row) if row else None


def compare(eval_id: int, regression_threshold_pp: float) -> dict:
    """{"comparable", "reason"?, "metric", "accepted_value", "new_value", "regression"} vs the last accepted run."""
    row = _get(eval_id)
    result = json.loads(row["results"])
    accepted = latest_accepted(row["slug"])
    if accepted is None or accepted["id"] == eval_id:
        return {"comparable": False, "reason": "no earlier accepted run"}
    if not _same_measurement_conditions(result, accepted):
        reason = _grading_mismatch(result, accepted) or (
            f"measured differently: accepted run used executor={accepted['executor']!r} judge={accepted['judge']!r}, "
            f"this run executor={result.get('executor')!r} judge={result.get('judge')!r}")
        return {"comparable": False, "reason": reason}
    new, old, metric = _comparison_metric(result, accepted)
    return {"comparable": True, "metric": metric, "accepted_value": old, "new_value": new,
            "regression": new < old - regression_threshold_pp}


def accept(eval_id: int, regression_threshold_pp: float, force: bool = False) -> dict:
    """Mark a run as the new baseline. Refuses a run that can't be compared with the current baseline unless
    force=True (explicit re-baseline). A regression is reported in the result, not blocked."""
    comparison = compare(eval_id, regression_threshold_pp)
    if not comparison["comparable"] and comparison["reason"] != "no earlier accepted run" and not force:
        raise ValueError(f"cannot compare with the accepted baseline ({comparison['reason']}). "
                         "Re-run under the same conditions, or accept with --force to re-baseline.")
    with store.connect() as conn:
        conn.execute("UPDATE evals SET accepted = 1 WHERE id = ?", (eval_id,))
    return {"accepted": eval_id, **comparison}


def report(slug: str) -> list[dict]:
    """Every recorded run for a skill, newest first, with the headline numbers."""
    with store.connect() as conn:
        rows = conn.execute("SELECT * FROM evals WHERE slug = ? ORDER BY id DESC", (slug,)).fetchall()
    out = []
    for r in rows:
        res = json.loads(r["results"])
        pair = (res.get("pairwise") or {}).get(COMPARISON) or {}
        out.append({"id": r["id"], "tier": r["tier"], "verdict": r["verdict"], "delta_pp": r["delta_pp"],
                    "rubric_score": r["rubric_score"], "tokens_total": r["tokens_total"],
                    "accepted": bool(r["accepted"]), "created_at": r["created_at"],
                    "ci": (res.get("stats") or {}).get("delta_pp_ci95"),
                    "pairwise": {k: pair.get(k) for k in ("wins", "losses", "ties")} if pair else None,
                    "result_path": r["result_path"]})
    return out
