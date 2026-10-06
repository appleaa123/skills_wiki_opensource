"""`skillswiki setup` and `skillswiki uninstall`: argument parsing and the console Io for the two flows."""
import json

from skillswiki import setup_flow, uninstall_flow
from skillswiki.setup_flow import AUTOMATIC, Io, Stop


def _console_ask(question: str, choices: tuple[str, ...]) -> str:
    while True:
        try:
            answer = input(f"{question} ").strip().lower()
        except (EOFError, KeyboardInterrupt) as exc:
            raise Stop("Input ended.") from exc
        if not choices or answer in choices:  # no choices: free text (the test request)
            return answer
        print(f"Please answer one of: {', '.join(choices)}")


def _no_input(question: str, choices: tuple[str, ...]) -> str:
    raise Stop("This step needs an answer; run it without --json.")


def _io(as_json: bool) -> Io:
    return Io(say=(lambda _text: None) if as_json else print, ask=_no_input if as_json else _console_ask)


def cmd_setup(args) -> None:
    io = _io(args.json)
    if args.test:
        result = {"tests": setup_flow.run_tests(io, args.test)}
    else:
        mode = AUTOMATIC if (args.yes or args.json) else None
        result = setup_flow.run(io, mode=mode, dry_run=args.dry_run)
    if args.json:
        print(json.dumps(result, indent=2, default=str))


def cmd_uninstall(args) -> None:
    mode = AUTOMATIC if (args.yes or args.json) else "guided"
    result = uninstall_flow.run(_io(args.json), mode, dry_run=args.dry_run, delete_backups=args.delete_backups)
    if args.json:
        print(json.dumps(result, indent=2, default=str))
    if result["blocked"]:
        raise ValueError("some skills could not be put back; see above")


def register(sub) -> None:
    p = sub.add_parser("setup", help="move every skill into Skills Wiki and connect your agents (interactive)")
    p.add_argument("--dry-run", dest="dry_run", action="store_true", help="show the plan; change nothing")
    p.add_argument("--yes", action="store_true", help="automatic: no questions, agents left untested")
    p.add_argument("--test", metavar="AGENT", help="run the guided test again for one agent key, or 'all'")
    p.set_defaults(func=cmd_setup)
    p = sub.add_parser("uninstall", help="put every skill back and disconnect your agents (run before pipx uninstall)")
    p.add_argument("--dry-run", dest="dry_run", action="store_true", help="show the plan; change nothing")
    p.add_argument("--yes", action="store_true", help="no questions")
    p.add_argument("--delete-backups", dest="delete_backups", action="store_true",
                   help="with --yes: delete setup backups too")
    p.set_defaults(func=cmd_uninstall)
