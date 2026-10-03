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
        if input("Continue? [y/N] ").strip().lower() != "y":
            raise ValueError("cancelled")


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
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = paths.results_dir() / args.slug / f"{ts}.json"
    runner._write_result(result, out)
    eval_id = ratchet_local.record(out, args.tier)
    if args.json:
        print(json.dumps({"eval_id": eval_id, "result_path": str(out)}))
        return
    runner.print_summary(result)
    print(f"\nRecorded as eval #{eval_id}. Accept it as the baseline with: skillswiki eval accept {eval_id}")


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

    p = esub.add_parser("report", help="list a skill's eval runs, newest first")
    p.add_argument("slug")
    p.set_defaults(func=cmd_report)

    p = esub.add_parser("accept", help="accept an eval run as the skill's baseline")
    p.add_argument("eval_id", type=int)
    p.add_argument("--force", action="store_true", help="re-baseline even if measured under different conditions")
    p.set_defaults(func=cmd_accept)
