"""`skillswiki eval ...` subcommands. Every run spends the user's own AI tokens; the cost estimate is shown first
and the larger tiers ask for confirmation."""
import hashlib
import json
import sys
from datetime import datetime, timezone

from skillswiki import learnings, paths
from skillswiki.evals import ratchet_local, suite

BACKENDS = ["claude", "codex", "gemini"]
TIERS = ["screen", "publish", "learning", "learning_screen"]
CONFIRM_TIERS = frozenset({"publish", "learning"})
DEFAULT_ARMS = ["skill", "no_skill"]
DEFAULT_RUNS = 3


def load_config() -> dict:
    return json.loads((paths.package_dir() / "evals" / "config.json").read_text())


def _arms_runs(args, config: dict) -> tuple[list[str], int]:
    tier_cfg = (config.get("tiers") or {}).get(args.tier) if args.tier else None
    arms = list(tier_cfg["arms"]) if tier_cfg else list(DEFAULT_ARMS)
    runs = args.runs if args.runs is not None else (tier_cfg["runs"] if tier_cfg else DEFAULT_RUNS)
    return arms, runs


def _learnings(slug: str, arms: list[str]) -> tuple[str | None, dict | None]:
    if "skill_learning" not in arms:
        return None, None
    rows = learnings.list_live(slug)
    block = learnings.build_learnings_block(slug, rows)
    if not block:
        return None, None
    return block, {"count": len(rows), "sha256": hashlib.sha256(block.encode()).hexdigest()[:16]}


def _confirm(args, message: str) -> None:
    if args.tier in CONFIRM_TIERS and not args.yes:
        print(message, file=sys.stderr)
        try:
            answer = input("Continue? [y/N] ")
        except EOFError:
            answer = ""
        if answer.strip().lower() != "y":
            raise ValueError("cancelled (pass --yes to run without asking)")


def _unique_result_path(slug: str):
    """<results>/<slug>/<UTC ts>.json; a run finishing in the same second as an earlier one gets a -N suffix
    instead of overwriting it."""
    folder = paths.results_dir() / slug
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out, n = folder / f"{ts}.json", 1
    while out.exists():
        out, n = folder / f"{ts}-{n}.json", n + 1
    return out


def cmd_run(args) -> None:
    from skillswiki.evals import runner

    config = load_config()
    arms, runs = _arms_runs(args, config)
    try:
        tasks = runner._load_tasks(args.slug)
    except FileNotFoundError as exc:
        raise ValueError(str(exc)) from exc
    status = suite.status(args.slug).get("status")
    if status != "checked":
        print(f"warning: suite for {args.slug} is '{status or 'unknown'}', not checked — run: skillswiki eval check "
              f"{args.slug}", file=sys.stderr)
    estimate = runner._estimate_tokens(len(tasks), arms, runs, config)
    message = (f"{args.slug}: {len(tasks)} tasks x {len(arms)} arm(s) x {runs} run(s), about {estimate:,} tokens "
               "on your own AI plan.")
    print(message, file=sys.stderr)
    _confirm(args, "This is a larger run.")
    block, meta = _learnings(args.slug, arms)
    try:
        result = runner.run_pack(
            pack=args.slug, executor=args.executor, executor_model=args.executor_model, judge_arg=args.judge,
            judge_model=args.judge_model, runs=runs, arms=arms, config=config, concurrency=args.concurrency,
            tier=args.tier, allow_large=args.allow_large, cache_baseline=args.tier is not None,
            learnings_block=block, learnings_meta=meta, cascade=args.cascade)
    except (runner.BudgetExceeded, runner.NoIndependentJudgeAvailable, FileNotFoundError) as exc:
        raise ValueError(str(exc)) from exc
    out = _unique_result_path(args.slug)
    runner._write_result(result, out)
    eval_id = ratchet_local.record(out, args.tier)
    calibration_line = ingest_calibration(args.slug, out) if args.cascade else None
    if args.json:
        print(json.dumps({"eval_id": eval_id, "result_path": str(out), "calibration": calibration_line}))
        return
    runner.print_summary(result)
    if calibration_line:
        print(calibration_line)
    print(f"\nRecorded as eval #{eval_id}. Accept it as the baseline with: skillswiki eval accept {eval_id}")


def ingest_calibration(slug: str, result_path) -> str:
    """Feed a cascade run's paired JEV/LLM grades into the user's calibration store; return a one-line summary."""
    from skillswiki.evals import calibration

    judge, rows = calibration.paired_rows(result_path)
    store = calibration.load(slug)
    try:
        store = calibration.update(store, result_path.name, judge, rows, calibration._pack_props(slug),
                                   calibration.cascade_config())
    except SystemExit:
        return f"calibration: {result_path.name} already ingested"
    calibration.save(store)
    entries = store["judges"].get(judge, {})
    trusted = sum(1 for e in entries.values() if e.get("status") == "calibrated")
    return (f"calibration: {len(rows)} paired grades added; {len(entries)} criteria tracked, {trusted} trusted for "
            f"judge {judge}")


def cmd_calibration(args) -> None:
    from skillswiki.evals import calibration

    store = calibration.load(args.slug)
    if args.json:
        print(json.dumps(store["judges"], indent=2))
    elif not store["judges"]:
        print(f"No calibration yet for {args.slug}. Run: skillswiki eval run {args.slug} --cascade")
    else:
        calibration._show(store)


def cmd_report(args) -> None:
    rows = ratchet_local.report(args.slug)
    if args.json:
        print(json.dumps(rows, indent=2))
        return
    if not rows:
        print(f"No eval runs for {args.slug}.")
        return
    for r in rows:
        pw = r["pairwise"] or {}
        print(f"#{r['id']} {r['created_at'][:16]} tier={r['tier']} verdict={r['verdict']} "
              f"delta={r['delta_pp']} ci={r['ci']} pairwise W/L/T={pw.get('wins')}/{pw.get('losses')}/{pw.get('ties')} "
              f"tokens={r['tokens_total']}{' ACCEPTED' if r['accepted'] else ''}")
        c = r.get("cascade")
        if c:
            jev_final = (c.get("final_by") or {}).get("jev", 0)
            print(f"    JEV cascade: {jev_final}/{c.get('verdicts')} grades final by JEV, "
                  f"{c.get('jev_input_tokens'):,} JEV tokens (${c.get('jev_cost_usd')}), "
                  f"{c.get('llm_judge_calls')} LLM judge calls")
            saved = r.get("cascade_savings") or {}
            if saved:
                print(f"    saved ~{saved['judge_calls_saved']} of {saved['outputs']} judge calls "
                      f"(~{saved['judge_tokens_saved_est']:,} judge tokens, estimate)")


def cmd_accept(args) -> None:
    result = ratchet_local.accept(args.eval_id, load_config()["regression_threshold_pp"], force=args.force)
    if args.json:
        print(json.dumps(result))
        return
    line = f"Accepted eval #{args.eval_id} as the baseline."
    if result.get("comparable"):
        line += (f" {result['metric']}: {result['accepted_value']:.1f} -> {result['new_value']:.1f}"
                 f"{' (REGRESSION)' if result['regression'] else ''}")
    print(line)


def cmd_generate(args) -> None:
    from skillswiki.evals import generator

    from skillswiki.evals.backends import BackendUnavailable

    try:
        suite.skill_body(args.slug)  # adopted? (before any token is spent)
        print(f"Asking {args.backend} to draft an eval suite for {args.slug} (uses your own AI tokens)...",
              file=sys.stderr)
        result = generator.generate(args.slug, args.backend, overwrite=args.overwrite)
    except (BackendUnavailable, RuntimeError) as exc:
        raise ValueError(str(exc)) from exc
    if args.json:
        print(json.dumps(result))
        return
    print(f"Drafted {result['tasks']} tasks at {result['path']} ({result['tokens']:,} tokens).\n"
          f"Review and edit the suite files before running: skillswiki eval show {args.slug}, then "
          f"skillswiki eval check {args.slug}")


def cmd_check(args) -> None:
    from skillswiki.evals import suite_check

    from skillswiki.evals.backends import BackendUnavailable

    try:
        result = suite_check.check(args.slug, llm=args.llm, backend=args.backend)
    except (BackendUnavailable, RuntimeError) as exc:
        raise ValueError(str(exc)) from exc
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        for issue in result["issues"]:
            print(f"{issue['level'].upper():5} {issue['where']}: {issue['message']}")
        jev = result.get("jev")
        if jev:
            print(f"JEV: {jev['input_tokens']:,} tokens, ${jev['usd']:.6f}" if "usd" in jev
                  else f"JEV unavailable ({jev['unavailable']}); other checks ran.")
        print(f"{'OK — suite marked checked.' if result['ok'] else 'Blocking problems — fix them and re-run.'}")
    if not result["ok"]:
        raise ValueError("suite has blocking problems")


def cmd_show(args) -> None:
    from skillswiki.evals import suite_check

    try:
        print(suite_check.show(args.slug))
    except FileNotFoundError as exc:
        raise ValueError(str(exc)) from exc


def register(sub) -> None:
    parser = sub.add_parser("eval", help="evaluate and benchmark a skill (uses your own AI tokens)")
    esub = parser.add_subparsers(dest="eval_command", required=True)

    p = esub.add_parser("run", help="run a skill's suite with and without the skill, then grade")
    p.add_argument("slug")
    p.add_argument("--tier", choices=TIERS)
    p.add_argument("--executor", choices=BACKENDS, default="claude")
    p.add_argument("--executor-model")
    p.add_argument("--judge", choices=BACKENDS, help="grading CLI (default: another installed CLI)")
    p.add_argument("--judge-model")
    p.add_argument("--runs", type=int)
    p.add_argument("--concurrency", type=int, default=4)
    p.add_argument("--allow-large", action="store_true", help="skip the tier's token budget guard")
    p.add_argument("--cascade", action="store_true", help="JEV grading cascade (needs your TYPESAFE_API_KEY)")
    p.add_argument("--yes", action="store_true", help="skip the confirmation for larger tiers")
    p.set_defaults(func=cmd_run)

    p = esub.add_parser("generate", help="draft an eval suite from the skill's SKILL.md (uses your tokens)")
    p.add_argument("slug")
    p.add_argument("--backend", choices=BACKENDS, default="claude")
    p.add_argument("--overwrite", action="store_true")
    p.set_defaults(func=cmd_generate)

    p = esub.add_parser("check", help="check a suite before running it")
    p.add_argument("slug")
    p.add_argument("--llm", action="store_true", help="also ask your AI CLI to flag restating prompts (uses tokens)")
    p.add_argument("--backend", choices=BACKENDS, default="claude")
    p.set_defaults(func=cmd_check)

    p = esub.add_parser("show", help="print a suite's tasks and rubric")
    p.add_argument("slug")
    p.set_defaults(func=cmd_show)

    p = esub.add_parser("calibration", help="show where JEV has earned trust against your judge")
    p.add_argument("slug")
    p.set_defaults(func=cmd_calibration)

    p = esub.add_parser("report", help="list a skill's eval runs, newest first")
    p.add_argument("slug")
    p.set_defaults(func=cmd_report)

    p = esub.add_parser("accept", help="accept an eval run as the skill's baseline")
    p.add_argument("eval_id", type=int)
    p.add_argument("--force", action="store_true", help="re-baseline even if measured under different conditions")
    p.set_defaults(func=cmd_accept)
