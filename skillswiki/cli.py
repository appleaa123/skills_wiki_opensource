"""`skillswiki` command line. Thin wrappers over the library; human text by default, JSON with --json.

Expected failures (ValueError) print one line to stderr and exit 1. Anything else is a bug and shows a traceback.
"""
import argparse
import json
import sys

from skillswiki import cards, discovery, learnings, library, loader, paths, store
from skillswiki.route import suggest

CONFIG_KEYS = {"learning": ("on", "off")}


def _print(data, as_json: bool, human: str = "") -> None:
    print(json.dumps(data, indent=2) if as_json else human)


def _csv_ints(value: str | None) -> list[int]:
    return [int(v) for v in value.split(",") if v.strip()] if value else []


def _skill_rows(status: str | None) -> list[dict]:
    sql, params = "SELECT slug, name, description, status, has_scripts FROM skills", ()
    if status:
        sql, params = sql + " WHERE status = ?", (status,)
    with store.connect() as conn:
        rows = [dict(r) for r in conn.execute(sql + " ORDER BY status, slug", params)]
    with store.connect() as conn:
        carded = {r["slug"] for r in conn.execute("SELECT slug FROM cards")}
    return [{**r, "has_card": r["slug"] in carded} for r in rows]


def cmd_scan(args) -> None:
    report = discovery.sync_db()
    human = (f"{report['total']} skills found ({len(report['added'])} new, {len(report['removed'])} gone"
             f"{', ' + str(len(report['missing'])) + ' adopted missing' if report['missing'] else ''}).")
    if report["conflicts"]:
        human += (f"\n{len(report['conflicts'])} duplicate copies ignored (same name in another agent's skill "
                  "folder; the first one found is used). See: skillswiki --json scan")
    _print(report, args.json, human)


def cmd_list(args) -> None:
    rows = _skill_rows(args.status)
    human = "\n".join(f"{r['status']:8} {r['slug']:30} card:{'yes' if r['has_card'] else 'no ':3} "
                      f"{r['description'][:70]}" for r in rows) or "No skills. Run: skillswiki scan"
    _print(rows, args.json, human)


def _adopt_lines(result: dict, dry_run: bool) -> str:
    verb, also = ("Would adopt", "would also move") if dry_run else ("Adopted", "also moved")
    human = f"{verb} {result['slug']}: {result['from']} -> {result['to']}"
    for other in result["moved_copies"]:
        human += f"\n  {also} an identical copy from {other} (release puts it back)"
    for other in result["linked_copies"]:
        human += (f"\n  note: {other} is the folder a symlink points to; left in place so the link keeps working "
                  "(your agents can still trigger it natively from there)")
    for other in result["differing_copies"]:
        human += (f"\n  warning: a different version is still active natively at {other}; your agent may still "
                  "trigger it on its own. Remove it, or make it identical and adopt again.")
    for warning in result.get("warnings", []):
        human += f"\n  warning: {warning['message']}"
    return human


def cmd_adopt(args) -> None:
    result = library.plan_adopt(args.slug) if args.dry_run else library.adopt(args.slug)
    _print(result, args.json, _adopt_lines(result, args.dry_run))


def _release_lines(result: dict, dry_run: bool) -> str:
    verb, also = ("Would release", "would also restore") if dry_run else ("Released", "also restored")
    missing = "is missing, so it will not be restored" if dry_run else "was missing, so it was not restored"
    human = f"{verb} {result['slug']}: {result['from']} -> {result['to']}"
    for origin in result["restored_copies"]:
        human += f"\n  {also} the copy at {origin}"
    for origin in result["missing_copies"]:
        human += f"\n  warning: the stored copy for {origin} {missing}"
    return human


def cmd_release(args) -> None:
    if args.all:
        return cmd_release_all(args)
    if not args.slug:
        raise ValueError("give a skill slug or --all")
    result = library.plan_release(args.slug) if args.dry_run else library.release(args.slug)
    _print(result, args.json, _release_lines(result, args.dry_run))


def cmd_release_all(args) -> None:
    result = library.release_all(dry_run=args.dry_run)
    if not result["released"] and not result["failed"]:
        return _print(result, args.json, "No adopted skills to release.")
    n = len(result["released"])
    verb = "Would release" if args.dry_run else "Released"
    lines = [f"{verb} {n} skill{'s' if n != 1 else ''} back to {'their folders' if n != 1 else 'its folder'}."]
    lines += [f"  {r['slug']} -> {r['to']}" for r in result["released"]]
    lines += [f"  FAILED {f['slug']}: {f['error']}" for f in result["failed"]]
    _print(result, args.json, "\n".join(lines))
    if result["failed"]:
        raise ValueError(f"{len(result['failed'])} skill(s) could not be released; see above")


def cmd_suggest(args) -> None:
    result = suggest(args.request)
    if result.get("skill"):
        human = f"Suggested: {result['skill']} (confidence {result.get('confidence', 0):.2f})"
    elif result["shortlist"]:
        human = "Shortlist: " + ", ".join(i["skill"] for i in result["shortlist"])
    else:
        human = "No matching skill."
    _print(result, args.json, human)


def cmd_load(args) -> None:
    result = loader.load(args.slug)
    human = "\n\n".join(p for p in (result["skill_md"], result["learnings"], result["note"]) if p)
    _print(result, args.json, human)


def cmd_card(args) -> None:
    if args.action == "show":
        card = cards.get(args.slug)
        _print(card, args.json, json.dumps(card, indent=2) if card else f"No card for {args.slug}.")
    elif args.action == "delete":
        cards.delete(args.slug)
        _print({"deleted": args.slug}, args.json, f"Deleted card for {args.slug}.")
    else:
        split = lambda v: [s.strip() for s in (v or "").split("|") if s.strip()]  # noqa: E731
        card = cards.set_manual(args.slug, split(args.examples), split(args.keywords), split(args.not_for))
        _print(card, args.json, f"Saved card for {args.slug}.")


def cmd_enrich(args) -> None:
    from skillswiki.evals.backends import BackendUnavailable

    print(f"Asking {args.backend} to draft a routing card (uses your own AI tokens)...", file=sys.stderr)
    try:
        card = cards.enrich(args.slug, args.backend)
    except BackendUnavailable as exc:
        raise ValueError(str(exc)) from exc
    _print(card, args.json, json.dumps(card, indent=2))


def cmd_learn(args) -> None:
    if args.action == "add":
        if learnings.enabled():
            learnings.require_adopted(args.slug)
        new_id = learnings.record(args.slug, args.text, supersedes=_csv_ints(args.supersedes))
        _print({"status": "ok", "id": new_id}, args.json, f"Recorded learning #{new_id} for {args.slug}.")
    elif args.action == "list":
        rows = learnings.list_all(args.slug) if args.all else learnings.list_live(args.slug)
        human = "\n".join(f"#{r['id']} {r['body']}" for r in rows) or f"No learnings for {args.slug}."
        _print(rows, args.json, human)
    else:
        learnings.retire(int(args.slug))
        _print({"retired": int(args.slug)}, args.json, f"Retired learning #{args.slug}.")


def cmd_config(args) -> None:
    if args.key not in CONFIG_KEYS:
        raise ValueError(f"unknown key {args.key!r}; known: {', '.join(CONFIG_KEYS)}")
    if args.action == "set":
        if args.value not in CONFIG_KEYS[args.key]:
            raise ValueError(f"{args.key} must be one of: {', '.join(CONFIG_KEYS[args.key])}")
        store.set_setting(args.key, args.value)
    value = store.get_setting(args.key, CONFIG_KEYS[args.key][0])
    _print({args.key: value}, args.json, f"{args.key} = {value}")


def cmd_serve_mcp(args) -> None:
    from skillswiki.mcp_server import run
    run()


def cmd_ui(args) -> None:
    import webbrowser

    from skillswiki.web.server import make_server

    try:
        server = make_server(args.port)
    except OSError as exc:
        raise ValueError(f"cannot listen on port {args.port} ({exc.strerror}); try --port {args.port + 1}") from exc
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    print(f"Skills Wiki page: {url} (Ctrl-C to stop)")
    if not args.no_open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def cmd_hook(args) -> None:
    from skillswiki import hook
    hook.main()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="skillswiki", description="Manage the AI skills you installed locally.")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("scan", help="find installed skills").set_defaults(func=cmd_scan)
    p = sub.add_parser("list", help="list known skills")
    p.add_argument("--status", choices=["adopted", "native", "plugin"])
    p.set_defaults(func=cmd_list)
    p = sub.add_parser("adopt", help="move a skill into the Skills Wiki library")
    p.add_argument("slug")
    p.add_argument("--dry-run", dest="dry_run", action="store_true", help="show what would move; move nothing")
    p.set_defaults(func=cmd_adopt)
    p = sub.add_parser("load", help="print an adopted skill with its learnings")
    p.add_argument("slug")
    p.set_defaults(func=cmd_load)
    p = sub.add_parser("release", help="move an adopted skill back (--all: every adopted skill)")
    p.add_argument("slug", nargs="?")
    p.add_argument("--all", action="store_true", help="release every adopted skill (run this before uninstalling)")
    p.add_argument("--dry-run", dest="dry_run", action="store_true", help="show what would move; move nothing")
    p.set_defaults(func=cmd_release)
    p = sub.add_parser("suggest", help="route a request to an adopted skill")
    p.add_argument("request")
    p.set_defaults(func=cmd_suggest)

    p = sub.add_parser("card", help="show, set or delete a routing card")
    p.add_argument("action", choices=["show", "set", "delete"])
    p.add_argument("slug")
    p.add_argument("--examples", help="'|'-separated example requests")
    p.add_argument("--keywords", help="'|'-separated keywords")
    p.add_argument("--not-for", dest="not_for", help="'|'-separated requests this skill should not handle")
    p.set_defaults(func=cmd_card)
    p = sub.add_parser("enrich", help="draft a routing card with your AI CLI (uses your tokens)")
    p.add_argument("slug")
    p.add_argument("--backend", default="claude", choices=["claude", "codex", "gemini"])
    p.set_defaults(func=cmd_enrich)

    p = sub.add_parser("learn", help="add, list or retire learnings")
    p.add_argument("action", choices=["add", "list", "retire"])
    p.add_argument("slug", help="skill slug (for retire: the learning id)")
    p.add_argument("text", nargs="?", default="")
    p.add_argument("--supersedes", help="comma-separated learning ids this one replaces")
    p.add_argument("--all", action="store_true", help="include superseded and retired learnings")
    p.set_defaults(func=cmd_learn)

    p = sub.add_parser("config", help="get or set a setting")
    p.add_argument("action", choices=["get", "set"])
    p.add_argument("key")
    p.add_argument("value", nargs="?")
    p.set_defaults(func=cmd_config)

    from skillswiki import cli_eval
    cli_eval.register(sub)

    p = sub.add_parser("ui", help="open the local management page")
    p.add_argument("--port", type=int, default=7878)
    p.add_argument("--no-open", action="store_true", help="don't open a browser")
    p.set_defaults(func=cmd_ui)

    sub.add_parser("serve-mcp", help="run the MCP server over stdio").set_defaults(func=cmd_serve_mcp)
    sub.add_parser("hook", help="Claude Code UserPromptSubmit hook (reads stdin)").set_defaults(func=cmd_hook)
    return parser


def _safe_console() -> None:
    """A console or pipe in a legacy encoding (common on Windows) must not crash on non-English skill text."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass


def main(argv: list[str] | None = None) -> int:
    _safe_console()
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["hook"]:  # runs on every prompt: never print a traceback, always exit 0
        from skillswiki import hook
        hook.main()
        return 0
    args = build_parser().parse_args(argv)
    try:
        paths.load_env()
        args.func(args)
    except ValueError as exc:
        print(f"skillswiki: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
