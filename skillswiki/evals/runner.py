#!/usr/bin/env python3
"""Provider-agnostic eval runner (Phase 1, LLM-improvement program).

Runs a pack's evals/tasks.jsonl through two arms — with the sub-skill body
prefixed to the prompt (arm "skill") and without it (arm "no_skill") — via a
pluggable CLI backend (evals/backends/*), then grades each output with
deterministic checks (evals/<pack>/evals/verify.py, if present) and/or a
rubric-guided independent judge (SkillLens: a judge guided by these three
dimensions picks the higher-utility skill document 73.8% of the time vs.
46.4% unguided, arXiv 2605.23899 §6 — that result is about selecting skill
text; the output-grading rubric here is Skills Wiki's adaptation and has
not been validated against human graders).

Every run spends the caller's own AI subscription — Skills Wiki pays for no
inference. Driven by the `skillswiki eval run` command (skillswiki/cli.py);
suites live in ~/.skillswiki/suites/<slug>/ (skillswiki/evals/suite.py), and
"pack" in this module means that skill slug.

P1.4c (2026-09-09) — cost redesign:
  - Backends now invoke with a structured JSON envelope and RAISE
    BackendUnavailable on a failed call (non-zero exit, an error envelope,
    an empty response, or a detected quota/rate-limit message) instead of
    silently returning the failure text as if it were model output — a real
    bug that fabricated a +26.7pp/+74pp scorecard from an exhausted
    subscription quota (evals/results/humanizer/20260909T145531Z.outputs.jsonl).
  - `--tier screen|publish` applies a lean, pinned invocation profile
    (evals/config.json's "invocation_profile") that strips Claude Code's
    ~43,000-token default boot context (agent system prompt, every built-in
    tool schema, the CLAUDE.md chain) down to ~500 tokens for a trivial
    call, measured — none of that is relevant to grading a skill's output.
  - The `no_skill` arm doesn't depend on the skill body, so it is cached
    across gates (`--cache-baseline`, on by default via the CLI) instead of
    re-run on every call.
  - A tier's `max_tokens` is checked BEFORE any backend call, from a
    calibrated per-call token estimate — an oversized run is refused with
    the projected cost, not discovered by exhausting a subscription window.
  - `_select_judge_backend`'s auto-select now raises rather than silently
    falling back to the executor's own CLI as its own judge — that
    "fallback" is a §2.1 SkillLens violation (46.4% agreement, self-judged)
    and doubles Claude-quota consumption for no signal gained.
"""
import concurrent.futures
import hashlib
import json
import random
import shutil
import statistics
from datetime import datetime, timezone
from pathlib import Path

from skillswiki import paths as _paths
from skillswiki.evals import stats as _stats
from skillswiki.evals import suite as _suite
from skillswiki.evals.deliverable import VERSION as _DELIVERABLE_VERSION, extract as _extract

_JUDGE_PROMPT_TEMPLATE = """You are grading an AI assistant's response against a rubric. Be strict, and grade only what is written — not what the assistant says it did.

Task prompt given to the assistant:
{prompt}

Assistant's response (the deliverable only — any working notes were removed before grading):
{response}

Criteria (each has an id and a description; the order is random and carries no meaning):
{criteria_json}

For each criterion id above, score how well the response satisfies it on a 0/1/2 scale:
  0 = does not satisfy it
  1 = partially satisfies it
  2 = fully satisfies it

Respond with ONLY a JSON object: {{"id": 0|1|2, ...}} — one integer per criterion id above, every id present. No prose, no markdown fences.
"""

_PAIRWISE_PROMPT_TEMPLATE = """You are comparing two AI assistant responses to the same task. Decide which one better serves the person who asked, judged strictly against the criteria below. Do not reward length, formatting, confidence, or explanations of process — judge only the deliverable text.

Task prompt given to both assistants:
{prompt}

Response A:
<<<A
{response_a}
A>>>

Response B:
<<<B
{response_b}
B>>>

Criteria (the order is random and carries no meaning):
{criteria_json}

Pick the better response. Answer "tie" only if you genuinely cannot prefer one; a small but real difference is enough to pick one.

Respond with ONLY a JSON object: {{"winner": "A" | "B" | "tie", "reason": "<one sentence, at most 30 words>"}}. No prose outside the JSON, no markdown fences.
"""

_JUDGE_BACKEND_PRIORITY = ["codex", "gemini", "claude"]


def _backend_binary(name: str) -> str:
    """Backend key -> actual CLI binary, for the shutil.which() availability check below. Only "gemini"
    differs: Antigravity's `agy` or the Gemini CLI `gemini` (see skillswiki/evals/backends/gemini_cli.py)."""
    if name == "gemini":
        from skillswiki.evals.backends.gemini_cli import binary
        return binary()
    return name


# Fallback per-call token estimates for the pre-flight budget guard
# (P1.4c step 6), used when evals/config.json has no "per_call_estimate_tokens"
# key. Calibrated from real measurements against the humanizer pack under
# the lean-v1 profile (skill-arm prompt 29,016 chars -> 10,348 total input
# tokens; no_skill-arm prompt 313 chars -> ~570; judge prompt 2,717 chars ->
# 1,467) — these are per-pack estimates, not universal constants; a pack
# with a much longer skill body or task inputs will project higher than it
# actually costs, or the reverse. The guard is a pre-flight sanity check
# against a gross miscalibration (running --runs 3 on eleven packs by
# accident), not a precise budget.
_DEFAULT_PER_CALL_ESTIMATE_TOKENS = {"skill_exec": 11000, "no_skill_exec": 600, "judge": 1500, "pairwise_judge": 1800}

# Arm pairs compared blind (P1.4d task 4.2) — never a pack/industry name, just
# the three arms every pack already runs.
_PAIRWISE_COMPARISONS = (("skill", "no_skill"), ("skill_learning", "skill"))


class BudgetExceeded(RuntimeError):
    """A sweep's projected token cost exceeds its tier's max_tokens budget.
    Raised before any backend call — see _estimate_tokens."""


class NoIndependentJudgeAvailable(RuntimeError):
    """No CLI other than the executor's own is installed, and no --judge
    was given explicitly. Silently falling back to the executor as its own
    judge is a §2.1 SkillLens violation (an independent judge guided by
    SkillLens's three dimensions picks the higher-utility skill document
    73.8% of the time vs. 46.4% unguided/self-judged, arXiv 2605.23899 §6 —
    that result is about selecting skill text, not grading output) and
    doubles Claude-quota consumption for both halves of the sweep — P1.4c
    makes that fallback an explicit error instead of a silent default. Pass
    --judge <same-as-executor> to opt into same-backend judging on purpose."""


def _load_tasks(pack: str) -> list[dict]:
    return _suite.load_tasks(pack)


def _load_rubric(pack: str) -> dict:
    return _suite.load_rubric(pack)


def _load_verifier(pack: str):
    """The suite's verify.py verify(task, output), or None if the suite has none."""
    return _suite.load_verifier(pack)


def _skill_sha(pack: str, first_skill: str) -> str:
    """Content hash (sha256, first 16 hex chars) of the sub-skill's resolved
    body. P1.4b step 7 (2026-09-09): replaces the old _index.json "sha"
    lookup, which made evals/ratchet.py's _check_resynced structurally
    inert — 8 of 11 eval-bearing packs have no "sha" key at all (falls back
    to "manual"), and the 3 hand-written realtor packs carry the literal
    string "manual" as their sha, so the staleness comparison was always
    "manual" == "manual" and could never fire. Hashing the actual resolved
    text changes exactly when the content a run was scored against changes.
    Deliberately invalidates every row published before this change — they
    all carry skill_sha="manual" and will read as permanently stale; that is
    intended, not a bug to backfill around."""
    import hashlib
    body = _read_skill_body(pack, first_skill)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]


def _read_skill_body(pack: str, skill_slug: str) -> str:
    """The adopted skill's SKILL.md text. Fails loudly rather than silently scoring an empty "skill" arm."""
    return _suite.skill_body(skill_slug)


def _render_inputs(inputs: dict) -> str:
    if not inputs:
        return ""
    return "\n\nInputs:\n" + json.dumps(inputs, indent=2)


def _build_prompt(task: dict, arm: str, pack: str, learnings_block: str | None = None) -> str:
    base = task["prompt"] + _render_inputs(task.get("inputs") or {})
    if arm == "no_skill":
        return base
    body = _read_skill_body(pack, task["skill"])
    if arm == "skill_learning":
        if not learnings_block:
            raise ValueError("arm 'skill_learning' needs live learnings for this skill — add some with: skillswiki learn add <slug> \"...\"")
        return f"{body}\n\n{learnings_block}\n\n---\n\n{base}"
    return f"{body}\n\n---\n\n{base}"


def _build_profile(config: dict, backend_name: str) -> dict | None:
    """Only ClaudeCli applies invocation_profile — the flags
    (--safe-mode/--tools/--strict-mcp-config/--setting-sources) are
    Claude-CLI-specific and don't carry over to codex/agy (P1.4c). A config
    without an "invocation_profile" key (every pre-P1.4c caller, including
    every existing test's plain {"rubric_pass_threshold", ...} config)
    yields None — unchanged behaviour."""
    if backend_name != "claude":
        return None
    return config.get("invocation_profile")


def _profile_hash(profile: dict | None) -> str | None:
    if not profile:
        return None
    raw = json.dumps(
        {"flags": profile.get("flags"), "system_prompt": profile.get("system_prompt"), "model": profile.get("model")},
        sort_keys=True,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _sum_usage(usage: dict | None) -> int:
    if not usage:
        return 0
    return sum(
        usage.get(k) or 0
        for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens", "output_tokens")
    )


_ARM_ESTIMATE_KEY = {"skill": "skill_exec", "no_skill": "no_skill_exec", "skill_learning": "skill_learning_exec"}


def _estimate_tokens(tasks_n: int, arms: list[str], runs: int, config: dict) -> int:
    """Pre-flight projection used by the tier budget guard (P1.4c step 6) —
    deliberately conservative (assumes every task also draws a judge call,
    even deterministic-only ones) so it errs toward refusing, not toward
    letting an oversized sweep start. P3.5 Phase 3: an unknown arm (or a
    config missing "skill_learning_exec") falls back to the skill_exec
    estimate rather than raising. P1.4d 4.1: the judge estimate is
    multiplied by judge_votes. P1.4d 4.2: adds a pairwise_judge estimate
    (two calls per pair, order-swap) for each comparison whose two arms are
    both in `arms`, only when pairwise is enabled."""
    est = config.get("per_call_estimate_tokens", _DEFAULT_PER_CALL_ESTIMATE_TOKENS)
    judge_est = est["judge"] * int(config.get("judge_votes", 1))
    total = 0
    for arm in arms:
        exec_est = est.get(_ARM_ESTIMATE_KEY.get(arm, "skill_exec"), est["skill_exec"])
        total += (exec_est + judge_est) * tasks_n * runs
    if config.get("pairwise") is True:
        pairwise_est = est.get("pairwise_judge", _DEFAULT_PER_CALL_ESTIMATE_TOKENS["pairwise_judge"])
        for x_arm, y_arm in _PAIRWISE_COMPARISONS:
            if x_arm in arms and y_arm in arms:
                total += 2 * pairwise_est * tasks_n * runs
    return total


def _criteria(rubric: dict, seed: str, applied: bool = True) -> list[dict]:
    """Rubric criteria as [{"id", "desc"}], shuffled deterministically by
    `seed` (P1.4d task 4.1: a fixed criterion order carries position bias).
    `applied` rules go to the pointwise judge only, never to pairwise.
    Weights are scoring-side and are not shown to the judge."""
    groups = rubric.get("dimensions", []) + rubric.get("items", []) + (rubric.get("applied", []) if applied else [])
    criteria = [{"id": c["id"], "desc": c.get("desc", "")} for c in groups]
    random.Random(seed).shuffle(criteria)
    return criteria


def _merge_usage(usages: list[dict | None]) -> dict | None:
    """Sum the numeric keys of several backend usage dicts (judge votes, the
    two pairwise orders). Non-numeric values (nested dicts, service_tier
    strings, bools) are dropped. None when no usage was reported."""
    present = [u for u in usages if u]
    if not present:
        return None
    merged: dict = {}
    for u in present:
        for k, v in u.items():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                merged[k] = merged.get(k, 0) + v
    return merged


def _judge(
    backend, prompt: str, response: str, rubric: dict, judge_model: str | None,
    timeout: int = 60, profile: dict | None = None, seed: str | None = None, votes: int = 1,
) -> tuple[dict[str, int], dict | None]:
    """Fail-closed on a BAD RESPONSE, fail-loud on a FAILED CALL (P1.4c):
    `backend.judge(...)` raises BackendUnavailable on a failed call (non-zero
    exit, an error envelope, empty output, a detected quota message) and
    that exception is left to propagate — a failed call must abort the
    sweep, not be scored as a regression. This function's own fail-closed
    path only covers a *successful* call whose text isn't valid rubric JSON
    (matches evals/adherence/run_eval.py::judge_response's fail-closed
    shape) and the defensive case of a backend that returns an empty string
    without raising (e.g. a test fake) — a real backend never reaches that
    branch, since it raises first.

    Returns (scale, usage) — usage is the backend's raw usage dict (or None
    for a backend that can't report it), for the caller to accumulate into
    the run's token ledger.

    `prompt` must already carry the task's rendered `inputs` (see the call
    site in run_pack) — a rubric dimension like "introduces no fact not
    present in inputs.text" cannot be graded against a prompt the judge
    never sees the inputs on (P1.4b step 1, found 2026-09-09: the judge was
    being called with the bare task prompt while the executor got inputs).

    Returns a 0/1/2 scale, not booleans (P1.4b step 4, 2026-09-09): a binary
    dict derived from today's `rub == True` collapsed a run-to-run judge
    spread the scale distinguishes — 8-12 tasks at n=1 quantized composite in
    8.3-12.5pp steps, coarser than the ratchet's 2pp threshold. Callers use
    _scale_to_bool (>= 2 satisfied) to preserve the existing binary
    pass/composite headline, and _rubric_score for the finer ratchet metric.

    P1.4d 4.1: criteria (dims, items, applied) are shuffled per call by
    `seed`; the returned scale is keyed in rubric order regardless. `votes`
    > 1 calls the judge `votes` times (seeds `{seed}|{v}`) and takes the
    per-id median; usage is merged."""
    ids = _rubric_ids(rubric) + _applied_ids(rubric)
    if not response:
        return {i: 0 for i in ids}, None

    def _once(call_seed: str) -> tuple[dict[str, int], dict | None]:
        judge_prompt = _JUDGE_PROMPT_TEMPLATE.format(
            prompt=prompt,
            response=response,
            criteria_json=json.dumps(_criteria(rubric, call_seed), indent=2),
        )
        result = backend.judge(judge_prompt, judge_model, timeout=timeout, profile=profile)
        raw = (result.get("text") or "").strip()
        usage = result.get("usage")
        if not raw:
            return {i: 0 for i in ids}, usage
        try:
            raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            parsed = json.loads(raw)
            return {i: max(0, min(2, int(parsed.get(i, 0)))) for i in ids}, usage
        except Exception:
            return {i: 0 for i in ids}, usage

    if votes <= 1:
        return _once(seed or "")
    polls = [_once(f"{seed}|{v}") for v in range(votes)]
    scale = {i: int(statistics.median(p[0][i] for p in polls)) for i in ids}
    return scale, _merge_usage([p[1] for p in polls])


def _judge_structured(
    backend, prompt: str, response: str, rubric: dict, votes: int = 1,
) -> tuple[dict[str, int], dict | None, dict | None]:
    """Structured-judge path (JEV Phase 1, development_guide/jev/): the
    backend answers per criterion with calibrated confidence instead of
    returning text, so there is no prompt to render or parse. Returns
    (scale, usage, detail); detail carries each criterion's confidence and
    probabilities for the agreement study and the Phase 2 cascade. Voting is
    not supported (config judge_votes is 1) — refuse rather than silently
    ignore it. BackendUnavailable propagates, same as _judge."""
    if votes > 1:
        raise ValueError(f"judge_votes={votes} is not supported by structured judge {backend.name!r}")
    result = backend.judge_rubric(prompt, response, rubric)
    return result["scale"], result["usage"], result["detail"]


def _judge_pairwise_once(
    backend, prompt_base: str, response_a: str, response_b: str, rubric: dict,
    judge_model: str | None, timeout: int = 60, profile: dict | None = None, seed: str | None = None,
) -> tuple[str, dict | None, dict | None]:
    """One pairwise call in one fixed order (P1.4d task 4.2). Returns
    ("A"|"B"|"tie", usage). `applied` rules never go to pairwise — they are
    the skill's own rules, not a basis for comparing two outputs. Any
    unparseable or out-of-range answer fails closed to "tie" (never a
    silent win), matching _judge's own fail-closed convention.
    BackendUnavailable propagates (P1.4c). A structured backend (JEV Phase 1)
    answers directly; its per-call detail is the third element (None for
    text backends)."""
    if getattr(backend, "structured", False):
        result = backend.judge_pair(prompt_base, response_a, response_b, rubric)
        return result["winner"], result["usage"], result["detail"]
    pairwise_prompt = _PAIRWISE_PROMPT_TEMPLATE.format(
        prompt=prompt_base,
        response_a=response_a,
        response_b=response_b,
        criteria_json=json.dumps(_criteria(rubric, seed or "", applied=False), indent=2),
    )
    result = backend.judge(pairwise_prompt, judge_model, timeout=timeout, profile=profile)
    usage = result.get("usage")
    raw = (result.get("text") or "").strip()
    try:
        raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        winner = json.loads(raw).get("winner")
        return (winner if winner in ("A", "B", "tie") else "tie"), usage, None
    except Exception:
        return "tie", usage, None


def _judge_pairwise(
    backend, prompt_base: str, out_x: str, out_y: str, rubric: dict, judge_model: str | None,
    timeout: int = 60, profile: dict | None = None, seed: str | None = None, with_detail: bool = False,
):
    """Blind pairwise comparison with an order swap (P1.4d task 4.2,
    SkillLens/Anthropic skill-creator precedent for judge position bias): x
    wins only if it's preferred in BOTH orders; disagreement between the two
    orders is a "tie", not a coin flip. An empty side loses without a call —
    an empty response can't be meaningfully compared, and P1.4c fail-closed
    grading already treats it as satisfying nothing.

    Returns (winner, usage); with_detail=True returns (winner, usage,
    [detail_ab, detail_ba] | None) for a structured judge (JEV Phase 1)."""
    if not out_x or not out_y:
        winner = "tie" if not out_x and not out_y else ("y" if not out_x else "x")
        return (winner, None, None) if with_detail else (winner, None)
    first, usage1, detail1 = _judge_pairwise_once(backend, prompt_base, out_x, out_y, rubric, judge_model, timeout, profile, f"{seed}|ab")
    second, usage2, detail2 = _judge_pairwise_once(backend, prompt_base, out_y, out_x, rubric, judge_model, timeout, profile, f"{seed}|ba")
    if first == "A" and second == "B":
        winner = "x"
    elif first == "B" and second == "A":
        winner = "y"
    else:
        winner = "tie"
    if with_detail:
        detail = [detail1, detail2] if detail1 is not None or detail2 is not None else None
        return winner, _merge_usage([usage1, usage2]), detail
    return winner, _merge_usage([usage1, usage2])


def _pairs(records_x: list[dict], records_y: list[dict]) -> list[tuple[dict, dict]]:
    """Match two arms' records on (task_id, run_index) only — arm and
    output are irrelevant to matching (P1.4d task 4.2)."""
    by_key = {(r["task_id"], r["run_index"]): r for r in records_y}
    return [
        (r, by_key[key])
        for r in sorted(records_x, key=lambda r: (r["task_id"], r["run_index"]))
        if (key := (r["task_id"], r["run_index"])) in by_key
    ]


def _run_pairwise(
    comparisons: tuple[tuple[str, str], ...],
    arm_records: dict[str, list[dict]],
    rubric: dict,
    judge_backend,
    judge_model: str | None,
    judge_profile: dict | None,
    timeout: int,
    concurrency: int,
    cascade=None,
) -> tuple[dict, list[dict | None]]:
    """Run every configured arm-pair comparison blind (P1.4d task 4.2).
    A comparison whose arms didn't both run is `None`, not omitted — the
    dashboard (Phase 6) needs to tell "didn't run" from "ran, no data".
    Deterministic-only records have no `judge_prompt_base` and are skipped
    (not counted in `n`) rather than crashing on a missing prompt."""
    out: dict = {}
    usages: list[dict | None] = []
    for x_arm, y_arm in comparisons:
        name = f"{x_arm}_vs_{y_arm}"
        if x_arm not in arm_records or y_arm not in arm_records:
            out[name] = None
            continue
        pairs = [p for p in _pairs(arm_records[x_arm], arm_records[y_arm]) if p[0].get("judge_prompt_base") is not None]
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
            results = list(pool.map(
                lambda p: _judge_pairwise(
                    judge_backend, p[0]["judge_prompt_base"],
                    p[0].get("deliverable", p[0]["output"]), p[1].get("deliverable", p[1]["output"]),
                    rubric, judge_model, timeout=timeout, profile=judge_profile,
                    seed=f"{p[0]['task_id']}|{p[0]['run_index']}", with_detail=True,
                ),
                pairs,
            ))
        wins = sum(1 for w, _u, _d in results if w == "x")
        losses = sum(1 for w, _u, _d in results if w == "y")
        ties = sum(1 for w, _u, _d in results if w == "tie")
        n = len(pairs)
        # JEV Phase 2: pairwise is SHADOW only — the LLM judge's winner stands; JEV's answer is recorded.
        shadows = ([cascade.shadow_pair(p[0]["judge_prompt_base"], p[0].get("deliverable", p[0]["output"]),
                                        p[1].get("deliverable", p[1]["output"]), rubric) for p in pairs]
                   if cascade is not None else [None] * n)
        ci = _stats.wilson_ci(wins, wins + losses)
        out[name] = {
            "wins": wins, "losses": losses, "ties": ties, "n": n,
            "win_rate": wins / n if n else None,
            "decisive_win_rate": wins / (wins + losses) if (wins + losses) else None,
            "ci95": list(ci) if ci else None,
            "per_pair": [
                {
                    "task_id": p[0]["task_id"], "run_index": p[0]["run_index"],
                    "winner": {"x": x_arm, "y": y_arm, "tie": "tie"}[w],
                    # structured judge only (JEV Phase 1): never present for text judges
                    **({"judge_detail": d} if d is not None else {}),
                    **({"jev_shadow": sh} if cascade is not None else {}),
                }
                for p, (w, _u, d), sh in zip(pairs, results, shadows)
            ],
        }
        usages.extend(u for _w, u, _d in results)
    return out, usages


def _with_jev_summary(pairwise: dict | None, config: dict) -> dict | None:
    """JEV Phase 5 P5.3: a `jev` sub-block {decisive, margin_min, verdict} on each
    comparison whose per_pair carries JEV judge_detail (margin-filtered, see
    evals/learning_rank.py). Unchanged for text judges and for the downloadable
    eval kit, which ships runner.py but not learning_rank.py."""
    if not pairwise or "learning" not in config:
        return pairwise
    try:
        from skillswiki.evals.learning_rank import from_per_pair, summarize
    except ImportError:
        return pairwise
    out = {}
    for name, comp in pairwise.items():
        if not comp or not any(p.get("judge_detail") for p in comp["per_pair"]):
            out[name] = comp
            continue
        s = summarize(from_per_pair(comp["per_pair"], name), **config["learning"])
        out[name] = {**comp, "jev": {"decisive": s["decisive"], "margin_min": s["margin_min"], "verdict": s["verdict"]}}
    return out


def _scale_to_bool(scale: dict[str, int]) -> dict[str, bool]:
    """Derive the binary pass/fail verdict from a 0/1/2 judge scale — 2
    (fully satisfies) counts as satisfied, matching the strictness of the
    old True/False judge (P1.4b step 4)."""
    return {i: v >= 2 for i, v in scale.items()}


def _rubric_score(scale: dict[str, int], rubric: dict) -> float | None:
    """Weighted mean satisfaction, 0-100 — items carry an explicit `weight`
    (default 1 if absent); dimensions are unweighted (weight 1). This is the
    ratchet metric (P1.4b step 4): finer-grained than composite's pass/fail
    collapse, so it isn't quantized coarser than the regression threshold.
    None if the rubric has no dimension/item ids to score. Ids in `scale`
    that aren't in the rubric (P1.4d task 2.1a — e.g. an `applied` id) are
    ignored rather than scored at an assumed weight of 1."""
    weights = {d["id"]: 1 for d in rubric.get("dimensions", [])}
    weights.update({i["id"]: i.get("weight", 1) for i in rubric.get("items", [])})
    scored = {i: v for i, v in scale.items() if i in weights}
    total_weight = sum(weights[i] for i in scored)
    if not scored or total_weight == 0:
        return None
    weighted_sum = sum(scored[i] * weights[i] for i in scored)
    return (weighted_sum / (total_weight * 2)) * 100


def _rubric_ids(rubric: dict) -> list[str]:
    return [d["id"] for d in rubric.get("dimensions", [])] + [i["id"] for i in rubric.get("items", [])]


def _applied_ids(rubric: dict) -> list[str]:
    return [a["id"] for a in rubric.get("applied", [])]


def _task_passes(det: dict[str, bool] | None, rub: dict[str, bool] | None, threshold: float) -> bool:
    det_ok = det is None or all(det.values())
    if rub is None:
        rub_ok = True
    else:
        rub_ok = (sum(rub.values()) / len(rub)) >= threshold if rub else True
    return det_ok and rub_ok


def _select_judge_backend(executor: str, judge_arg: str | None):
    from skillswiki.evals.backends import get_backend

    if judge_arg:
        return get_backend(judge_arg), judge_arg
    for name in _JUDGE_BACKEND_PRIORITY:
        if name != executor and shutil.which(_backend_binary(name)):
            return get_backend(name), name
    raise NoIndependentJudgeAvailable(
        f"Only one AI CLI was found ({executor}). An independent judge (codex or gemini) is more "
        f"trustworthy. To grade with the same CLI anyway, re-run with --judge {executor}. Expect it to "
        f"favour its own outputs somewhat."
    )


def _grade(
    arm: str,
    task: dict,
    run_index: int,
    output: str,
    executor_usage: dict | None,
    verify_fn,
    rubric: dict,
    threshold: float,
    judge_backend,
    judge_model: str | None,
    judge_profile: dict | None,
    timeout: int,
    learnings_applied: bool,
    judge_votes: int = 1,
    cascade=None,
) -> tuple[bool, dict]:
    """Grade one executor output on its extracted deliverable (P1.4d task 3.1,
    founder decision 1) and build the persisted record. Shared by run_pack and
    (Phase 5.2) rejudge. paste_ready is recorded, never scored. Lets
    BackendUnavailable propagate from the judge call (P1.4c)."""
    deliverable, paste_ready, paste_ready_reason = _extract(output)
    verifier = task.get("verifier", "rubric")

    det = None
    if verifier in ("deterministic", "both") and verify_fn is not None:
        det = verify_fn(task, deliverable)

    rubric_scale = None
    applied_scale = None
    rubric_score = None
    judge_prompt_base = None
    judge_usage = None
    judge_detail = None
    cascade_meta = jev_usage = None
    if verifier in ("rubric", "both"):
        judge_prompt_base = task["prompt"] + _render_inputs(task.get("inputs") or {})
        if cascade is not None:  # JEV Phase 2 (evals/cascade.py): JEV first, the LLM judge decides the rest
            gates = det if det is not None else (verify_fn(task, deliverable) if verify_fn is not None else None)
            checks = {**(gates or {}), **cascade.extra_checks(task, deliverable)}  # det (the gate) is untouched
            scale, judge_usage, judge_detail, cascade_meta, jev_usage = cascade.grade(
                judge_backend, judge_prompt_base, deliverable, rubric, checks, f"{task['id']}|{run_index}",
                judge_model, timeout, judge_profile, judge_votes,
            )
        elif getattr(judge_backend, "structured", False):
            scale, judge_usage, judge_detail = _judge_structured(
                judge_backend, judge_prompt_base, deliverable, rubric, votes=judge_votes,
            )
        else:
            scale, judge_usage = _judge(
                judge_backend, judge_prompt_base, deliverable, rubric, judge_model,
                timeout=timeout, profile=judge_profile, seed=f"{task['id']}|{run_index}",
                votes=judge_votes,
            )
        rubric_scale = {i: scale.get(i, 0) for i in _rubric_ids(rubric)}
        # Only ids the judge returned (all of them since 4.1, fail-closed
        # zeros included) — `applied` is reported via applied_rate, never
        # scored into rubric_score/passed.
        applied_scale = {i: scale[i] for i in _applied_ids(rubric) if i in scale} or None
        rubric_score = _rubric_score(rubric_scale, rubric)

    passed = _task_passes(det, _scale_to_bool(rubric_scale) if rubric_scale is not None else None, threshold)
    record = {
        "arm": arm,
        "task_id": task["id"],
        "run_index": run_index,
        "verifier": verifier,
        "judge_prompt_base": judge_prompt_base,
        "output": output,
        "det": det,
        "rubric_score": rubric_score,
        "executor_usage": executor_usage,
        "judge_usage": judge_usage,
        "learnings_applied": learnings_applied,
        "deliverable": deliverable,
        "paste_ready": paste_ready,
        "paste_ready_reason": paste_ready_reason,
        "rubric_scale": rubric_scale,
        "applied_scale": applied_scale,
        "passed": passed,
    }
    if judge_detail is not None:  # structured judge only — text-judge records keep today's shape
        record["judge_detail"] = judge_detail
    if cascade_meta is not None:  # cascade runs only
        record["cascade"] = cascade_meta
        record["jev_usage"] = jev_usage
    return passed, record


def _run_one_unit(
    arm: str,
    task: dict,
    run_index: int,
    pack: str,
    executor_backend,
    executor_model: str | None,
    executor_profile: dict | None,
    judge_backend,
    judge_model: str | None,
    judge_profile: dict | None,
    verify_fn,
    rubric: dict,
    threshold: float,
    timeout: int,
    learnings_block: str | None = None,
    judge_votes: int = 1,
    cascade=None,
) -> tuple[bool, dict]:
    """Execute one (arm, task, run) — the unit the thread pool in run_pack
    parallelizes over (P1.4b step 3, 2026-09-09). Grading (on the extracted
    deliverable, P1.4d task 3.1) is delegated to _grade, which also builds the
    raw-output record persisted by _write_result (P1.4b step 2). Lets
    BackendUnavailable propagate from either the executor or judge call
    (P1.4c) — a failed call aborts the sweep rather than being scored."""
    prompt = _build_prompt(task, arm, pack, learnings_block)
    exec_result = executor_backend.run(prompt, None, executor_model, timeout=timeout, profile=executor_profile)
    # P3.5 Phase 3: whether this unit's executor prompt actually carried the
    # <learnings> block. The full executor prompt is deliberately NOT
    # persisted (it would bloat every outputs.jsonl record and change what
    # rejudge.py replays) — this one boolean is the cheap proof, for both a
    # unit test and the C6 live-run check, that the skill_learning arm's
    # prompt really differed from the skill arm's.
    learnings_applied = arm == "skill_learning" and bool(learnings_block)
    return _grade(
        arm, task, run_index, exec_result["text"], exec_result.get("usage"),
        verify_fn, rubric, threshold, judge_backend, judge_model, judge_profile, timeout,
        learnings_applied, judge_votes, cascade,
    )


def _check_rates(records: list[dict]) -> dict[str, float]:
    """Per-check pass rate across records (P1.4d task 2.1a) — the numbers
    that explain a headline composite/pass_rate instead of just showing it.
    Covers three sources, keyed by check id: deterministic `det` ids (rate
    of True among records whose `det` has that id), `"paste_ready"` (rate of
    True among records that carry the key at all — omitted entirely if no
    record has it, so a v1-shaped or non-rubric record doesn't show a bogus
    0%), and any id appearing in a record's `rubric_scale` (rate of that id
    scoring >= 2, matching `_scale_to_bool`'s threshold)."""
    det_hits: dict[str, list[bool]] = {}
    paste_ready_hits: list[bool] = []
    rubric_hits: dict[str, list[bool]] = {}
    for rec in records:
        det = rec.get("det") or {}
        for check_id, ok in det.items():
            det_hits.setdefault(check_id, []).append(bool(ok))
        if "paste_ready" in rec:
            paste_ready_hits.append(bool(rec["paste_ready"]))
        scale = rec.get("rubric_scale") or {}
        for check_id, v in scale.items():
            rubric_hits.setdefault(check_id, []).append(v >= 2)
    rates: dict[str, float] = {}
    for check_id, hits in det_hits.items():
        rates[check_id] = sum(hits) / len(hits)
    if paste_ready_hits:
        rates["paste_ready"] = sum(paste_ready_hits) / len(paste_ready_hits)
    for check_id, hits in rubric_hits.items():
        rates[check_id] = sum(hits) / len(hits)
    return dict(sorted(rates.items()))


def _applied_summary(records: list[dict]) -> tuple[float | None, dict[str, float] | None]:
    """Mean applied-rules satisfaction (P1.4d task 2.1a) — over records that
    carry a non-empty `applied_scale`, the mean fraction of ids scoring == 2
    (fully satisfied), plus the same per-id. `applied` rules are reported,
    never scored into pass/rubric_score (founder decision: only the
    deliverable's own quality gates the pass/fail verdict)."""
    scored = [rec["applied_scale"] for rec in records if rec.get("applied_scale")]
    if not scored:
        return None, None
    per_record_rates = []
    id_hits: dict[str, list[bool]] = {}
    for scale in scored:
        per_record_rates.append(sum(1 for v in scale.values() if v == 2) / len(scale))
        for check_id, v in scale.items():
            id_hits.setdefault(check_id, []).append(v == 2)
    applied_rate = sum(per_record_rates) / len(per_record_rates)
    applied_check_rates = {i: sum(hits) / len(hits) for i, hits in id_hits.items()}
    return applied_rate, applied_check_rates


def _records_to_arm_result(records: list[dict], tasks: list[dict] | None) -> dict:
    """Aggregate a flat list of per-run records (each carrying "passed",
    "task_id", "rubric_score") for a single arm into the
    {"pass_rate","per_task","rubric_score","check_rates","applied_rate",
    "applied_check_rates"} shape — shared by a freshly-run arm and a cached
    baseline arm (P1.4c step 5), so both aggregate identically. `tasks=None`
    (P1.4d task 2.1a) lets a caller delegate here without loading the task
    list — every task with at least one run still shows up via the records
    themselves."""
    per_task: dict[str, list[bool]] = {task["id"]: [] for task in (tasks or [])}
    rubric_scores: list[float] = []
    for rec in records:
        per_task.setdefault(rec["task_id"], []).append(rec["passed"])
        if rec.get("rubric_score") is not None:
            rubric_scores.append(rec["rubric_score"])
    total_runs = sum(len(v) for v in per_task.values())
    passed_runs = sum(sum(v) for v in per_task.values())
    pass_rate = (passed_runs / total_runs) if total_runs else 0.0
    rubric_score = (sum(rubric_scores) / len(rubric_scores)) if rubric_scores else None
    applied_rate, applied_check_rates = _applied_summary(records)
    return {
        "pass_rate": pass_rate,
        "per_task": per_task,
        "rubric_score": rubric_score,
        "check_rates": _check_rates(records),
        "applied_rate": applied_rate,
        "applied_check_rates": applied_check_rates,
    }


def _suite_hash(pack: str) -> str:
    """Content hash (sha256, first 16 hex chars) of a pack's rubric.json +
    verify.py bytes, or "" for either file that's missing (P1.4d task 7.0b).
    Folded into `_baseline_key` so a rubric- or verifier-only edit orphans
    the no_skill cache instead of silently scoring cached records under
    stale rules — the cache key previously covered only the tasks and the
    grading rules, not what a deterministic check or a judge criterion
    actually says."""
    rubric_path = _suite.suite_dir(pack) / "rubric.json"
    verify_path = _suite.suite_dir(pack) / "verify.py"
    data = (rubric_path.read_bytes() if rubric_path.exists() else b"") + (verify_path.read_bytes() if verify_path.exists() else b"")
    return hashlib.sha256(data).hexdigest()[:16]


def _baseline_key(pack: str, tasks: list[dict], executor: str, executor_model: str | None,
                   judge_name: str, judge_model: str | None, profile_hash: str | None,
                   grading_hash: str | None = None, suite_hash: str | None = None,
                   runs: int | None = None) -> str:
    """Cache key for the no_skill baseline (P1.4c step 5) — changes exactly
    when something that could change the no_skill arm's score changes:
    the tasks themselves, which model answers them, which model (and
    profile) judges them, and (P1.4d task 3.1) the grading rules a record
    would be scored under (`grading_hash` — callers pass
    `_baseline_grading_hash(config)`, narrower than `_grading(config)["hash"]`:
    it omits `pairwise`, which runs after the baseline is loaded/written and
    never touches a cached record, so toggling it shouldn't force a re-run).
    Does NOT depend on the skill body — that's the whole point, the
    no_skill arm never sees it. (P1.4d task 7.0b: `suite_hash`/`runs` are
    appended only when at least one is given, so a caller that omits both
    — none exist today outside `run_pack` — gets the exact same key as
    before this task.)"""
    tasks_hash = hashlib.sha256(json.dumps(tasks, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:16]
    raw = f"{pack}|{tasks_hash}|{executor}|{executor_model}|{judge_name}|{judge_model}|{profile_hash}|{grading_hash}"
    if suite_hash is not None or runs is not None:
        raw += f"|{suite_hash}|{runs}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _baseline_path(pack: str, key: str) -> Path:
    # A ".baseline" subdirectory, not a same-directory "baseline-*.json" —
    # a same-directory file was the original design, and it was a live bug
    # (found running the real P1.6a re-baseline, 2026-09-09):
    # evals/ratchet.py::_newest_results_file does
    # `sorted(pack_dir.glob("*.json"))[-1]`, and "baseline-..." sorts AFTER
    # every "<UTC timestamp>.json" result file alphabetically ("b" > "2"),
    # so the ratchet silently picked the baseline cache file (which has no
    # "composite" key) as if it were the newest real result and crashed
    # with KeyError. Subdirectory keeps `glob("*.json")` (non-recursive)
    # from ever matching it — no filename convention to keep in sync.
    return _paths.results_dir() / pack / ".baseline" / f"{key}.json"


def _load_baseline(pack: str, key: str) -> dict | None:
    path = _baseline_path(pack, key)
    if not path.exists():
        return None
    return json.loads(path.read_text())


def _write_baseline(pack: str, key: str, records: list[dict], arm_result: dict) -> None:
    path = _baseline_path(pack, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"records": records, "arm_result": arm_result}, indent=2))


def _judge_votes(config: dict) -> int:
    """Validate and return config's judge_votes (P1.4d task 4.1, extracted
    to a shared helper in task 5.2 so rejudge.py validates the same way as
    run_pack). Refuses before any backend call — an even vote count has no
    median tie-break and would silently pick one of two candidate ints
    depending on statistics.median's averaging, which is not what "median"
    means here."""
    judge_votes = config.get("judge_votes", 1)
    if isinstance(judge_votes, bool) or not isinstance(judge_votes, int) or judge_votes < 1 or judge_votes % 2 == 0:
        raise ValueError("judge_votes must be an odd integer >= 1")
    return judge_votes


def run_pack(
    pack: str,
    executor: str,
    executor_model: str | None,
    judge_arg: str | None,
    judge_model: str | None,
    runs: int,
    arms: list[str],
    config: dict,
    timeout: int = 180,
    concurrency: int = 1,
    tier: str | None = None,
    allow_large: bool = False,
    cache_baseline: bool = False,
    refresh_baseline: bool = False,
    learnings_block: str | None = None,
    learnings_meta: dict | None = None,
    cascade: bool = False,
) -> dict:
    from skillswiki.evals.backends import get_backend

    tasks = _load_tasks(pack)
    rubric = _load_rubric(pack)
    verify_fn = _load_verifier(pack)

    # P3.5 Phase 3: refuse before any backend call, not partway through a
    # sweep — measuring an empty/absent learnings block would read as "my
    # learnings don't help" when really nothing was measured at all.
    if "skill_learning" in arms and not learnings_block:
        raise ValueError("arm 'skill_learning' needs live learnings for this skill — add some with: skillswiki learn add <slug> \"...\"")

    # P1.4d task 4.1: refuse before any backend call, same reasoning as the
    # skill_learning guard above.
    judge_votes = _judge_votes(config)

    # P1.4c step 6: refuse an oversized sweep before spending anything — a
    # pre-flight check from a calibrated estimate, not a mid-sweep cutoff
    # that would leave already-spent calls unrecoverable.
    if tier is not None:
        tier_cfg = (config.get("tiers") or {}).get(tier)
        if tier_cfg is None:
            raise ValueError(f"unknown tier {tier!r} — add it to evals/config.json's \"tiers\"")
        max_tokens = tier_cfg.get("max_tokens")
        if max_tokens is not None and not allow_large:
            projected = _estimate_tokens(len(tasks), arms, runs, config)
            if projected > max_tokens:
                raise BudgetExceeded(
                    f"{pack}: projected ~{projected:,} tokens for tier={tier!r} "
                    f"({len(arms)} arm(s) x {len(tasks)} tasks x runs={runs}) exceeds its "
                    f"max_tokens={max_tokens:,} budget. Reduce --runs/--arms, or pass "
                    f"--allow-large to override this guard."
                )

    executor_backend = get_backend(executor)
    judge_backend, judge_name = _select_judge_backend(executor, judge_arg)
    cascade_judge = None
    if cascade:  # lazy: the downloadable eval kit ships runner.py but not evals/cascade.py
        try:
            from skillswiki.evals.cascade import Cascade
        except ImportError as exc:
            raise SystemExit(f"--cascade is not available in this build: {exc}")
        cascade_judge = Cascade.build(pack, judge_backend, judge_name, judge_model, config)
    cascade_fp = cascade_judge.fingerprint() if cascade_judge else None

    threshold = config["rubric_pass_threshold"]

    executor_profile = _build_profile(config, executor)
    judge_profile = _build_profile(config, judge_name)
    profile_hash = _profile_hash(executor_profile)
    # A pinned model beats an unset one that silently rides the caller's
    # *saved* CLI default — the ratchet's comparability check can't detect
    # that changing between two runs otherwise (P1.4c, F4).
    pinned_executor_model = executor_model or (executor_profile.get("model") if executor_profile else None)

    # P1.4c step 5: the no_skill arm doesn't depend on the skill body, so
    # it's cached across gates instead of re-run on every one. Only takes
    # effect when the caller opts in (cache_baseline=True) — every existing
    # direct run_pack() caller (all current tests) gets exactly the old
    # behaviour with cache_baseline defaulting to False.
    baseline_key = None
    cached_baseline = None
    arms_to_run = list(arms)
    if cache_baseline and "no_skill" in arms:
        baseline_key = _baseline_key(pack, tasks, executor, pinned_executor_model, judge_name, judge_model, profile_hash, _baseline_grading_hash(config, cascade_fp), _suite_hash(pack), runs)
        if not refresh_baseline:
            cached_baseline = _load_baseline(pack, baseline_key)
        if cached_baseline is not None:
            arms_to_run = [a for a in arms if a != "no_skill"]

    # Flat unit list in canonical (arm, task, run) order. subprocess calls
    # are IO-bound, so a thread pool (stdlib, no new dependency) parallelizes
    # wall clock without touching correctness — ThreadPoolExecutor.map
    # returns results in the SAME order as `units`, so the aggregate below
    # is deterministic regardless of which unit actually finishes first
    # (P1.4b step 3, 2026-09-09; a serial 11-pack x n=3 x 2-arm sweep was
    # 600+ sequential subprocess calls, hours of wall clock).
    units = [(arm, task, run_index) for arm in arms_to_run for task in tasks for run_index in range(runs)]

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        unit_results = list(pool.map(
            lambda u: _run_one_unit(
                u[0], u[1], u[2], pack, executor_backend, pinned_executor_model, executor_profile,
                judge_backend, judge_model, judge_profile, verify_fn, rubric, threshold, timeout,
                learnings_block, judge_votes, cascade_judge,
            ),
            units,
        ))

    # Raw per-run records (P1.4b step 2, 2026-09-09): run_pack used to keep
    # only pass/fail booleans, so a rubric or judge change couldn't be
    # re-scored without a fresh, costly executor sweep, and the ~25pp
    # run-to-run spread observed on marketing_skills couldn't be attributed
    # to judge vs. executor sampling. _write_result persists these to a
    # sibling <ts>.outputs.jsonl; evals/rejudge.py reads them back.
    arm_records: dict[str, list[dict]] = {}
    result_iter = iter(unit_results)
    for arm in arms_to_run:
        arm_records[arm] = [next(result_iter)[1] for _task in tasks for _run_index in range(runs)]

    if cached_baseline is not None:
        # P1.4d 3.2: cached no_skill records go through _summarize like a
        # fresh arm. The stored arm_result is never read: delta/stats/cost/
        # example and pairwise (4.2) all need the records themselves.
        arm_records["no_skill"] = cached_baseline["records"]

    outputs = [rec for recs in arm_records.values() for rec in recs]
    summary = _summarize(arm_records, tasks)
    if "no_skill" in arms_to_run and cache_baseline and baseline_key is not None:
        _write_baseline(pack, baseline_key, arm_records["no_skill"], summary["arms"]["no_skill"])

    # P1.4d task 4.2: pairwise runs after the baseline cache is written, so a
    # BackendUnavailable from a pairwise call can't lose a freshly-cached
    # no_skill baseline (cached records already carry deliverable and
    # judge_prompt_base from 3.1, so a cached-baseline sweep can pair too).
    pairwise = None
    pairwise_usages: list[dict | None] = []
    if config.get("pairwise") is True:
        pairwise, pairwise_usages = _run_pairwise(
            _PAIRWISE_COMPARISONS, arm_records, rubric, judge_backend, judge_model, judge_profile, timeout, concurrency,
            cascade_judge,
        )
        pairwise = _with_jev_summary(pairwise, config)

    # Token ledger (P1.4c) — real, measured usage (per-call `usage` from the
    # backend's own response), not the pre-flight estimate. None for
    # backends that don't report usage (codex/gemini today).
    executor_tokens = sum(_sum_usage(o.get("executor_usage")) for o in outputs)
    judge_tokens = sum(_sum_usage(o.get("judge_usage")) for o in outputs) + sum(_sum_usage(u) for u in pairwise_usages)

    return {
        "pack": pack,
        "skill_slug": tasks[0]["skill"],
        "skill_sha": _skill_sha(pack, tasks[0]["skill"]),
        "executor": executor,
        "executor_model": pinned_executor_model,
        "judge": judge_name,
        "judge_model": judge_model,
        "n": runs,
        "tasks": len(tasks),
        "arms": summary["arms"],
        "delta_pp": summary["delta_pp"],
        "composite": summary["composite"],
        "rubric_score": summary["rubric_score"],
        "rubric_score_delta_pp": summary["rubric_score_delta_pp"],
        "learning_delta_pp": summary["learning_delta_pp"],
        "learning_rubric_delta_pp": summary["learning_rubric_delta_pp"],
        "learnings": learnings_meta,
        "profile": {"id": executor_profile.get("id"), "hash": profile_hash} if executor_profile else None,
        "token_usage": {"executor": executor_tokens, "judge": judge_tokens, "total": executor_tokens + judge_tokens},
        "ran_at": datetime.now(timezone.utc).isoformat(),
        "outputs": outputs,
        "schema_version": 2,
        "grading": _grading(config, cascade_fp=cascade_fp),
        "stats": summary["stats"],
        "cost": summary["cost"],
        "example": summary["example"],
        "pairwise": pairwise,
        **_verdicts_and_findings({**summary, "tasks": len(tasks)}, pairwise, config),
        **({"cascade": cascade_judge.summary(outputs)} if cascade_judge else {}),
    }


def _summarize(arm_records: dict[str, list[dict]], tasks: list[dict] | None = None) -> dict:
    """Aggregate per-arm record lists into the full v1+v2 summary shape
    (P1.4d task 2.1b) — the one path shared by `run_pack` (Phase 3),
    `rejudge.py`, and `rescore.py`. `tasks=None` is fine: every task with
    at least one recorded run still appears, via the records themselves
    (see `_records_to_arm_result`)."""
    arms = {arm: _records_to_arm_result(recs, tasks) for arm, recs in arm_records.items()}

    delta_pp = None
    if "skill" in arms and "no_skill" in arms:
        delta_pp = (arms["skill"]["pass_rate"] - arms["no_skill"]["pass_rate"]) * 100
    composite = arms["skill"]["pass_rate"] * 100 if "skill" in arms else None

    rubric_score = arms.get("skill", {}).get("rubric_score")
    rubric_score_delta_pp = None
    if "skill" in arms and "no_skill" in arms:
        s, n = arms["skill"]["rubric_score"], arms["no_skill"]["rubric_score"]
        if s is not None and n is not None:
            rubric_score_delta_pp = s - n

    learning_delta_pp = None
    learning_rubric_delta_pp = None
    if "skill" in arms and "skill_learning" in arms:
        learning_delta_pp = (arms["skill_learning"]["pass_rate"] - arms["skill"]["pass_rate"]) * 100
        s_rs, l_rs = arms["skill"].get("rubric_score"), arms["skill_learning"].get("rubric_score")
        if s_rs is not None and l_rs is not None:
            learning_rubric_delta_pp = l_rs - s_rs

    # stats (P1.4d): the confidence-interval and headroom/low-signal layer
    # that turns a bare composite/delta_pp into something a customer can
    # trust or distrust correctly (SkillsBench §2.1's low-signal-rejection
    # idea, adapted).
    pass_rate_ci95 = {}
    for arm, data in arms.items():
        total = sum(len(v) for v in data["per_task"].values())
        passed = sum(sum(v) for v in data["per_task"].values())
        ci = _stats.wilson_ci(passed, total)
        pass_rate_ci95[arm] = list(ci) if ci else None

    delta_pp_ci95 = None
    if "skill" in arms and "no_skill" in arms:
        ci = _stats.bootstrap_delta_ci(arms["skill"]["per_task"], arms["no_skill"]["per_task"])
        delta_pp_ci95 = list(ci) if ci else None

    learning_delta_pp_ci95 = None
    if "skill" in arms and "skill_learning" in arms:
        ci = _stats.bootstrap_delta_ci(arms["skill_learning"]["per_task"], arms["skill"]["per_task"])
        learning_delta_pp_ci95 = list(ci) if ci else None

    gain = None
    if "skill" in arms and "no_skill" in arms:
        gain = _stats.normalized_gain(arms["skill"]["pass_rate"], arms["no_skill"]["pass_rate"])

    headroom = None
    if "no_skill" in arms and arms["no_skill"]["rubric_score"] is not None:
        headroom = 100 - arms["no_skill"]["rubric_score"]

    low_signal = _stats.low_signal_tasks(arms["no_skill"]["per_task"]) if "no_skill" in arms else []

    delta_pp_signal = None
    if "skill" in arms and "no_skill" in arms:
        excluded = set(low_signal)
        skill_pr = _stats.pass_rate_excluding(arms["skill"]["per_task"], excluded)
        no_skill_pr = _stats.pass_rate_excluding(arms["no_skill"]["per_task"], excluded)
        if skill_pr is not None and no_skill_pr is not None:
            delta_pp_signal = 100 * (skill_pr - no_skill_pr)

    stats = {
        "pass_rate_ci95": pass_rate_ci95,
        "delta_pp_ci95": delta_pp_ci95,
        "learning_delta_pp_ci95": learning_delta_pp_ci95,
        "normalized_gain": gain,
        "headroom": headroom,
        "low_signal_tasks": low_signal,
        "delta_pp_signal": delta_pp_signal,
        "bootstrap": {"seed": 0, "resamples": 1000},
    }

    # cost (P1.4d): per-arm mean token usage from real per-call `usage`
    # (None for backends that don't report it), and the ratios that surface
    # in a COST_RATIO finding.
    cost_per_arm = {}
    for arm, recs in arm_records.items():
        usages = [_sum_usage(r.get("executor_usage")) for r in recs if r.get("executor_usage")]
        output_tokens = [r["executor_usage"].get("output_tokens") or 0 for r in recs if r.get("executor_usage")]
        n = len(usages)
        cost_per_arm[arm] = {
            "mean_executor_tokens": (sum(usages) / n) if n else None,
            "mean_output_tokens": (sum(output_tokens) / n) if n else None,
            "n": n,
        }

    def _ratio(a: str, b: str) -> float | None:
        ma = cost_per_arm.get(a, {}).get("mean_executor_tokens")
        mb = cost_per_arm.get(b, {}).get("mean_executor_tokens")
        if not ma or not mb:
            return None
        return ma / mb

    cost = {
        "per_arm": cost_per_arm,
        "skill_vs_no_skill_ratio": _ratio("skill", "no_skill"),
        "skill_learning_vs_skill_ratio": _ratio("skill_learning", "skill"),
    }

    # example (P1.4d): the first sorted task_id with both a skill and a
    # no_skill record at run_index 0 — schema rule for the side-by-side card.
    by_task: dict[str, dict[str, dict]] = {}
    for arm in ("skill", "no_skill"):
        for rec in arm_records.get(arm, []):
            if rec.get("run_index") == 0:
                by_task.setdefault(rec["task_id"], {})[arm] = rec
    example = None
    for task_id in sorted(by_task):
        pair = by_task[task_id]
        if "skill" in pair and "no_skill" in pair:
            skill_rec, no_skill_rec = pair["skill"], pair["no_skill"]
            example = {
                "task_id": task_id,
                "run_index": 0,
                "prompt": skill_rec.get("judge_prompt_base"),
                "skill": skill_rec.get("deliverable", skill_rec.get("output")),
                "no_skill": no_skill_rec.get("deliverable", no_skill_rec.get("output")),
            }
            break

    return {
        "arms": arms,
        "delta_pp": delta_pp,
        "composite": composite,
        "rubric_score": rubric_score,
        "rubric_score_delta_pp": rubric_score_delta_pp,
        "learning_delta_pp": learning_delta_pp,
        "learning_rubric_delta_pp": learning_rubric_delta_pp,
        "stats": stats,
        "cost": cost,
        "example": example,
    }


def _verdicts_and_findings(summary: dict, pairwise: dict | None, config: dict) -> dict:
    """Plain-language verdicts + named findings (P1.4d tasks 3.2, 4.2).
    `summary` must carry "tasks" (the task count, for LOW_SIGNAL_TASKS). A
    comparison present in `pairwise` is decided by its paired preference;
    otherwise by the delta_pp band. No `regression_threshold_pp` in config
    -> no band-based verdict."""
    band = config.get("regression_threshold_pp")
    verdicts = {}
    for name, key in (("skill_vs_no_skill", "delta_pp"), ("skill_learning_vs_skill", "learning_delta_pp")):
        cmp = (pairwise or {}).get(name)
        if cmp is not None:
            verdicts[name] = {"verdict": _stats.pairwise_verdict(cmp["wins"], cmp["losses"]), "basis": "pairwise"}
            continue
        verdict = _stats.band_verdict(summary.get(key), band) if band is not None else None
        verdicts[name] = {"verdict": verdict, "basis": "delta_pp" if verdict is not None else None}
    return {"verdicts": verdicts, "findings": _stats.findings(summary, config)}


def _grading(config: dict, partial: str | None = None, cascade_fp: dict | None = None) -> dict:
    """The `grading` block (P1.4d): what rules produced this result, hashed
    so the ratchet (Phase 5) can refuse to compare across an incompatible
    change and the dashboard (Phase 6) can key its comparability check off
    the same value. `judge_shuffle`: criteria order is shuffled per judge
    call (task 4.1). `partial` (e.g. "v1_verdicts") marks a rescore that
    reused a stored verdict instead of re-judging (task 2.2) — a rescore
    makes no model call at all, so judge_shuffle/pairwise/judge_votes never
    ran; report what actually happened, not the live config's current
    settings for a fresh judge call (cosmetic cleanup, Open items list)."""
    if partial:
        g = {
            "version": 2,
            "deliverable_extraction": _DELIVERABLE_VERSION,
            "judge_votes": 0,
            "pairwise": False,
            "judge_shuffle": False,
            "partial": partial,
        }
        return {**g, "hash": hashlib.sha256(json.dumps(g, sort_keys=True).encode()).hexdigest()[:16]}
    g = {
        "version": 2,
        "deliverable_extraction": _DELIVERABLE_VERSION,
        "judge_votes": int(config.get("judge_votes", 1)),
        "pairwise": bool(config.get("pairwise")),
        "judge_shuffle": True,
        **({"cascade": cascade_fp} if cascade_fp else {}),  # JEV Phase 2: absent → today's hash
    }
    return {**g, "hash": hashlib.sha256(json.dumps(g, sort_keys=True).encode()).hexdigest()[:16]}


def _baseline_grading_hash(config: dict, cascade_fp: dict | None = None) -> str:
    """A narrower grading hash for the no_skill baseline cache key (cosmetic
    cleanup, Open items list). `_grading(config)["hash"]` changes when
    `pairwise` toggles, forcing a fresh no_skill sweep even though pairwise
    runs strictly after the baseline is loaded/written and never touches a
    cached record. `judge_votes` and `judge_shuffle` do NOT get the same
    exclusion — `_run_one_unit` grades every arm's units identically
    (verified: the no_skill arm's cached rubric_scale/rubric_score/passed
    are produced by the same `_grade` call, with the same `judge_votes`, as
    the skill arm's), so a cache built under a different vote count or
    shuffle setting would silently serve stale judge-graded records."""
    g = {
        "version": 2,
        "deliverable_extraction": _DELIVERABLE_VERSION,
        "judge_votes": int(config.get("judge_votes", 1)),
        "judge_shuffle": True,
        **({"cascade": cascade_fp} if cascade_fp else {}),
    }
    return hashlib.sha256(json.dumps(g, sort_keys=True).encode()).hexdigest()[:16]


def _write_result(result: dict, out_path: Path) -> Path:
    """Write the result JSON (booleans/composite only, matching the shape
    every existing reader — publish.py, ratchet.py, the dashboard route —
    already expects) plus a sibling <ts>.outputs.jsonl carrying each run's
    raw executor output, judge prompt base, and deterministic verdict.
    Mutates `result` (pops "outputs") — call after run_pack, not before.
    P1.4b step 2, 2026-09-09."""
    outputs = result.pop("outputs", [])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2))

    outputs_path = out_path.with_suffix(".outputs.jsonl")
    outputs_path.write_text("".join(json.dumps(o) + "\n" for o in outputs))
    return outputs_path


def print_summary(result: dict) -> None:
    print(f"\n{result['pack']} — {result['tasks']} task(s), n={result['n']}, executor={result['executor']}, judge={result['judge']}")
    for arm, data in result["arms"].items():
        rs = data.get("rubric_score")
        rs_str = f", rubric_score {rs:.1f}" if rs is not None else ""
        print(f"  {arm}: {data['pass_rate'] * 100:.0f}%{rs_str}")
        check_rates = data.get("check_rates")
        if check_rates:
            checks_str = " ".join(f"{k}={v:.2f}" for k, v in sorted(check_rates.items()))
            print(f"    checks: {checks_str}")
    if result["delta_pp"] is not None:
        sign = "+" if result["delta_pp"] >= 0 else ""
        print(f"  delta_pp: {sign}{result['delta_pp']:.1f}")
    if result.get("rubric_score_delta_pp") is not None:
        sign = "+" if result["rubric_score_delta_pp"] >= 0 else ""
        print(f"  rubric_score_delta_pp: {sign}{result['rubric_score_delta_pp']:.1f}")
    if result.get("learning_delta_pp") is not None:
        sign = "+" if result["learning_delta_pp"] >= 0 else ""
        meta = result.get("learnings") or {}
        print(f"  learning_delta_pp: {sign}{result['learning_delta_pp']:.1f}  (skill+learnings vs skill; {meta.get('count', '?')} learnings, sha {meta.get('sha256', '?')})")
    token_usage = result.get("token_usage")
    if token_usage:
        print(f"  token_usage: executor={token_usage['executor']:,} judge={token_usage['judge']:,} total={token_usage['total']:,}")

    # P1.4d grading-v2 explanation layer: every field below is optional and
    # skipped when absent, so this function stays safe to call on a plain
    # v1 result dict (test_print_summary_tolerates_v1_result).
    for cmp_name, v in (result.get("verdicts") or {}).items():
        if v and v.get("verdict") is not None:
            print(f"  verdict {cmp_name}: {v['verdict']} ({v.get('basis')})")
    for cmp_name, p in (result.get("pairwise") or {}).items():
        if p:
            print(f"  pairwise {cmp_name}: preferred {p['wins']} of {p['n']} (lost {p['losses']}, tie {p['ties']})")
    stats = result.get("stats") or {}
    if stats.get("delta_pp_ci95") is not None:
        lo, hi = stats["delta_pp_ci95"]
        print(f"  delta_pp_ci95: [{lo:.1f}, {hi:.1f}]")
    if stats.get("low_signal_tasks"):
        low = stats["low_signal_tasks"]
        signal = stats.get("delta_pp_signal")
        signal_str = f"{signal:+.1f}" if signal is not None else "—"
        print(f"  low_signal_tasks: {len(low)} ({', '.join(low)}) — delta_pp_signal: {signal_str}")
    if stats.get("headroom") is not None:
        print(f"  headroom: {stats['headroom']:.1f}")
    cost = result.get("cost") or {}
    if cost.get("skill_vs_no_skill_ratio") is not None:
        print(f"  cost ratio (skill/no_skill): {cost['skill_vs_no_skill_ratio']:.1f}x")
    example = result.get("example")
    if example:
        print(f"  example: task {example['task_id']}")
    for finding in result.get("findings") or []:
        print(f"  finding: {finding['code']} — {finding['text']}")
    grading = result.get("grading")
    if grading:
        partial_str = f" partial={grading['partial']}" if grading.get("partial") else ""
        print(f"  grading: v{grading['version']} {grading['hash']}{partial_str}")
