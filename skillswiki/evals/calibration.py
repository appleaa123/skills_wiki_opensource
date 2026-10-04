#!/usr/bin/env python3
"""Calibration store for the JEV cascade judge (JEV Phase 2, P2.2; single-judge design 2026-09-26).

Trust is data, not configuration, and it is measured
against ONE judge: the user's own (README #11). Per (pack, judge, criterion) the store keeps PAIRED
verdicts (JEV and that judge graded the same output: every LLM-decided criterion of a cascade run,
since JEV always grades, or a past study) and a derived entry:

  trusted at the most permissive certainty threshold where, with 95% confidence (Wilson upper bound),
  JEV disagrees with the judge no more than two strong judges disagree with each other, on pass/fail
  (`baseline_disagreement`) AND on the exact 0/1/2 level (`exact_baseline`; the scorecard and the
  ratchet use levels), and JEV's mean level lean stays within `lean_limit` in every arm (the headline
  is a skill-vs-no_skill difference). For `fail_only_kinds` only JEV's FAIL verdicts can ever be final,
  so only those are measured. Two constants are yardsticks measured once by Skills Wiki (two strong LLM
  judges, Claude vs Gemini, disagreeing with each other — not JEV output); no user needs a second judge.

status: cold (too little evidence) | calibrated | escalate (evidence against JEV) | rule (count/legal/high risk)

Store: ~/.skillswiki/calibration/<slug>.json — starts empty; filled from the user's own cascade runs.
    {"pack", "updated", "runs": [ingested sources],
     "judges": {judge_key: {criterion: {"status", "threshold", "n", "n_at", "pass_fail_hi", "exact_hi", "lean"}}},
     "rows": {judge_key: {criterion: [[jev_level, llm_level, p2, arm], ...]}}}

Ingested automatically after every `skillswiki eval run --cascade`; shown by `skillswiki eval calibration <slug>`.
"""
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from skillswiki import paths
from skillswiki.decision import policy
from skillswiki.evals import jev_stats as js
from skillswiki.evals import stats as _stats

RULE_KINDS = frozenset({"count", "legal"})
# A list holding no nested list/object is written on one line: one paired verdict per line, readable diffs.
_FLAT_LIST = re.compile(r"\[\s*([^\[\]{}]*?)\s*\]")


# ── store ─────────────────────────────────────────────────────────────


def load(pack: str, directory: Path | None = None) -> dict:
    path = Path(directory or paths.calibration_dir()) / f"{pack}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"pack": pack, "updated": None, "runs": [], "judges": {}, "rows": {}}


def save(store: dict, directory: Path | None = None) -> Path:
    directory = Path(directory or paths.calibration_dir())
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{store['pack']}.json"
    text = json.dumps({**store, "updated": datetime.now(timezone.utc).isoformat()}, indent=1)
    path.write_text(_FLAT_LIST.sub(lambda m: "[" + " ".join(m.group(1).split()) + "]", text) + "\n", encoding="utf-8")
    return path


def entry(store: dict, judge_key: str, criterion: str) -> dict | None:
    return store["judges"].get(judge_key, {}).get(criterion)


def cascade_config(path: Path | None = None) -> dict:
    return json.loads(Path(path or paths.package_dir() / "evals" / "config.json").read_text(encoding="utf-8"))["cascade"]


# ── evidence ──────────────────────────────────────────────────────────


def _is_rule(props: dict) -> bool:
    return props.get("kind") in RULE_KINDS or props.get("risk") == "high"


def _candidates(rows: list[dict], props: dict, config: dict) -> list[dict]:
    """The verdicts JEV could ever make final for this criterion."""
    if policy.effective_kind(props, config) in config["fail_only_kinds"]:
        return [r for r in rows if not js.passed(r["jev"])]
    return rows


def _upper(k: int, n: int) -> float:
    return _stats.wilson_ci(k, n)[1]


def _check(at: list[dict], config: dict) -> tuple[bool, dict]:
    n = len(at)
    arms: dict[str, list[int]] = defaultdict(list)
    for r in at:
        arms[r.get("arm") or "all"].append(r["jev"] - r["llm"])
    if n < 2 * config["min_per_arm"]:
        return False, {"n_at": n}
    pf_hi = _upper(sum(js.passed(r["jev"]) != js.passed(r["llm"]) for r in at), n)
    exact_hi = _upper(sum(r["jev"] != r["llm"] for r in at), n)
    leans = {arm: sum(v) / len(v) for arm, v in arms.items() if len(v) >= config["min_per_arm"]}
    ok = (pf_hi <= config["baseline_disagreement"] and exact_hi <= config["exact_baseline"]
          and all(abs(x) <= config["lean_limit"] for x in leans.values()))
    return ok, {"n_at": n, "pass_fail_hi": round(pf_hi, 3), "exact_hi": round(exact_hi, 3),
                "lean": {a: round(x, 3) for a, x in sorted(leans.items())}}


def evaluate(rows: list[dict], props: dict, config: dict) -> dict:
    """Entry for one criterion from its paired rows {"p2", "jev", "llm", "arm"} against one judge."""
    if _is_rule(props):
        return {"status": "rule", "threshold": None, "n": len(rows)}
    candidates = _candidates(rows, props, config)
    stats = {}
    for t in sorted(config["ladder"]):  # most permissive first
        ok, stats_t = _check([r for r in candidates if js.margin(r["p2"]) >= t], config)
        stats = stats or stats_t
        if ok:
            return {"status": "calibrated", "threshold": t, "n": len(rows), **stats_t}
    status = "cold" if len(candidates) < 2 * config["min_per_arm"] else "escalate"
    return {"status": status, "threshold": None, "n": len(rows), **stats}


# ── update (cascade runs) ─────────────────────────────────────────────


def _pack_props(pack: str) -> dict[str, dict]:
    from skillswiki.evals import runner
    rubric = runner._load_rubric(pack)
    return {c["id"]: c for g in ("dimensions", "items", "applied") for c in rubric.get(g, [])}


def _as_dicts(rows: list[list]) -> list[dict]:
    return [{"jev": j, "llm": llm, "p2": p2, "arm": arm} for j, llm, p2, arm in rows]


def paired_rows(result_path: Path) -> tuple[str, list[dict]]:
    """(judge_key, paired verdicts) from a cascade run: criteria the LLM decided that JEV also graded."""
    result_path = Path(result_path)
    result = json.loads(result_path.read_text(encoding="utf-8"))
    judge = (result.get("cascade") or {}).get("judge_key")
    if not judge:
        raise SystemExit(f"{result_path} is not a cascade run (no cascade.judge_key)")
    rows = []
    outputs = result_path.with_suffix(".outputs.jsonl")
    for line in outputs.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        final = {**(rec.get("rubric_scale") or {}), **(rec.get("applied_scale") or {})}
        detail = rec.get("judge_detail") or {}
        for crit, meta in sorted((rec.get("cascade") or {}).items()):
            if meta.get("final_by") == "llm" and crit in detail and crit in final:
                rows.append({"judge": judge, "criterion": crit, "jev": detail[crit]["level"], "llm": final[crit],
                             "p2": detail[crit]["p2"], "arm": rec["arm"],
                             "key": f"{result_path.name}|{rec['arm']}|{rec['task_id']}|{rec['run_index']}|{crit}"})
    return judge, rows


def update(store: dict, source: str, judge: str, rows: list[dict], props_by_crit: dict, config: dict) -> dict:
    """Ingest one run's paired rows (refused if already ingested) and re-evaluate the touched criteria."""
    if source in store["runs"]:
        raise SystemExit(f"{source} already ingested into {store['pack']}")
    judge_rows = store["rows"].setdefault(judge, {})
    judge_entries = store["judges"].setdefault(judge, {})
    for crit in sorted({r["criterion"] for r in rows}):
        judge_rows.setdefault(crit, []).extend([r["jev"], r["llm"], r["p2"], r["arm"]] for r in rows if r["criterion"] == crit)
        judge_entries[crit] = evaluate(_as_dicts(judge_rows[crit]), props_by_crit.get(crit, {}), config)
    store["runs"].append(source)
    return store


# ── CLI ───────────────────────────────────────────────────────────────


def _show(store: dict) -> None:
    print(f"{store['pack']}  (updated {store.get('updated')}; sources: {', '.join(store['runs'])})")
    for judge, entries in sorted(store["judges"].items()):
        for crit, e in sorted(entries.items()):
            print(f"  {judge:8} {crit:40} {e['status']:10} threshold={e['threshold']}  n={e.get('n')}  "
                  f"n_at={e.get('n_at')}  pass_fail_hi={e.get('pass_fail_hi')}  exact_hi={e.get('exact_hi')}  lean={e.get('lean')}")
