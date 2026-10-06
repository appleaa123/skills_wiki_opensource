"""`skillswiki setup` (spec Section A): mode → welcome → scan → plan → move → go live → summary. All talk goes through
Io so tests can script the answers; Stop ends the run cleanly at any question."""
import shutil
from dataclasses import dataclass
from typing import Callable

from skillswiki import agents, backup, cards, discovery, library, paraphrase, store, wiring, wiring_data
from skillswiki import setup_text as text

GUIDED, AUTOMATIC = "guided", "automatic"
YES, NO = "y", "n"


class Stop(Exception):
    """The user declined, or input ended (Ctrl-C, end of piped input)."""


@dataclass(frozen=True)
class Io:
    say: Callable[[str], None]
    ask: Callable[[str, tuple[str, ...]], str]


def confirm(io: Io, mode: str, question: str) -> None:
    if mode != AUTOMATIC and io.ask(question, (YES, NO)) != YES:
        raise Stop(text.DECLINED)


def choose_mode(io: Io) -> str:
    io.say(text.MODES)
    return GUIDED if io.ask(text.ASK_MODE, ("g", "a")) == "g" else AUTOMATIC


def _plugin_slugs() -> list[str]:
    with store.connect() as conn:
        return [r["slug"] for r in conn.execute("SELECT slug FROM skills WHERE status = 'plugin' ORDER BY slug")]


def step_scan(io: Io) -> list[str]:
    report = discovery.sync_db()
    found = [r for r in agents.rows() if r["detected"]]
    io.say(text.detected_lines(found, _plugin_slugs(), report["conflicts"]))
    return [r["key"] for r in found]


def step_plan(io: Io, keys: list[str]) -> dict:
    preview = library.adopt_all(dry_run=True)
    io.say(text.plan_lines(preview))
    io.say(text.BENEFITS)
    io.say(text.wiring_lines([(agents.by_key(k).name, wiring.methods(k), wiring_data.by_key(k).notes)
                              for k in keys]))
    return preview


def run(io: Io, mode: str | None = None, dry_run: bool = False) -> dict:
    result = {"mode": None, "stopped": False, "plan": None, "moved": None, "agents": [], "backup": None}
    try:
        mode = AUTOMATIC if dry_run else (mode or choose_mode(io))
        result["mode"] = mode
        io.say(text.WELCOME)
        keys = step_scan(io)
        result["plan"] = step_plan(io, keys)
        if dry_run:
            io.say(text.DRY_RUN_END)
            return result
        confirm(io, mode, text.ASK_GO)
        zip_path = backup.start("setup")
        result["backup"] = str(zip_path)
        result["moved"] = library.adopt_all(zip_path=zip_path)
        io.say(text.move_lines(result["moved"], zip_path))
        result["agents"] = go_live(io, mode, keys, zip_path)
        io.say(text.summary(result["agents"], zip_path))
    except (Stop, KeyboardInterrupt) as exc:
        result["stopped"] = True
        io.say(text.stopped(str(exc) or "Interrupted."))
    return result


TEST_ANSWERS = {"y": "tested_ok", "s": "untested", "n": "no"}


def _on_path() -> bool:
    return shutil.which("skillswiki") is not None


def test_prompt(io: Io) -> tuple[str, str] | None:
    """(slug, request) for the guided test: a routing-card example, else a paraphrase from the user's AI CLI, else a
    request the user types. Never the description itself: any search matches that word for word."""
    with store.connect() as conn:
        rows = conn.execute("SELECT slug, description FROM skills WHERE status = 'adopted' ORDER BY slug").fetchall()
    for r in rows:
        card = cards.get(r["slug"])
        if card and card["examples"]:
            return r["slug"], card["examples"][0]
    if not rows:
        return None
    slug, description = rows[0]["slug"], rows[0]["description"]
    backend = paraphrase.available_backend()
    if backend:
        io.say(text.PARAPHRASE_NOTICE.format(backend=backend))
        request = paraphrase.from_ai(description, backend)
        if request:
            return slug, request
    io.say(text.type_request(slug, description))
    typed = io.ask(text.ASK_REQUEST, ()).strip()
    return (slug, typed) if typed else None


def guided_test(io: Io, name: str, test: tuple[str, str]) -> str:
    slug, request = test
    io.say(text.test_instructions(name, request))
    return TEST_ANSWERS[io.ask(text.ask_test(name, slug), ("y", "n", "s"))]


def _say_result(io: Io, name: str, res: dict) -> None:
    if res.get("reason"):
        io.say(text.cannot_edit(name, res["reason"]))
    for prompt in (res.get("prompt"), res["mcp"].get("prompt")):
        if prompt:
            io.say(prompt)
    if res["mcp"].get("reason"):
        io.say(text.mcp_not_registered(name, res["mcp"]["reason"]))
    if res.get("notes"):
        io.say(f"Note: {res['notes']}")
    if res.get("advice"):
        io.say(res["advice"])


def _outcome(key: str, method: str, test: str) -> dict:
    row = wiring.get_row(key) or {}
    return {"agent": key, "name": agents.by_key(key).name, "method": method,
            "self_setup": bool(row.get("self_setup")), "test": test}


def _skips_own_test(io: Io, mode: str, key: str, method: str) -> bool:
    """True when we tested this agent's hook ourselves and the user (or automatic mode) skips their own test."""
    tested = wiring_data.by_key(key).tested if method == "hook" else ""
    if not tested:
        return False
    if mode == AUTOMATIC:
        return True
    io.say(text.known_result(agents.by_key(key).name, tested))
    return io.ask(text.ASK_TEST_ANYWAY, (YES, NO)) != YES


def _apply_step(io: Io, mode: str, key: str, method: str, zip_path) -> bool:
    """Apply one method (asking first in guided mode). False when the user declines this agent."""
    name = agents.by_key(key).name
    if mode == GUIDED and method != "advice":
        io.say(text.wire_intro(name, wiring.describe(key, method)))
        if io.ask(text.ASK_WIRE, (YES, NO)) != YES:
            return False
    _say_result(io, name, wiring.apply(key, method, zip_path))
    return True


def wire_agent(io: Io, mode: str, key: str, zip_path, test, already_applied: bool = False) -> dict:
    order = wiring.methods(key)
    row = wiring.get_row(key)
    if row and row["test"] != "failed" and row["method"] in order:  # a failed agent starts over from its hook
        order = order[order.index(row["method"]):]
    for i, method in enumerate(order):
        if not (already_applied and i == 0) and not _apply_step(io, mode, key, method, zip_path):
            return _outcome(key, "skipped", "untested")
        if method == "advice":
            wiring.set_test(key, "failed")
            return _outcome(key, method, "failed")
        if not already_applied and _skips_own_test(io, mode, key, method):
            wiring.set_vouched(key)
            return _outcome(key, method, "vouched")
        if mode == AUTOMATIC or test is None:
            if mode == GUIDED:
                io.say(text.NO_TEST_SKILL)
            return _outcome(key, method, "untested")
        answer = guided_test(io, agents.by_key(key).name, test)
        if answer != "no":
            wiring.set_test(key, answer)
            return _outcome(key, method, answer)
        io.say(text.TRY_NEXT)
        undone = wiring.undo(key, method)
        if undone.get("prompt"):
            io.say(undone["prompt"])
    return _outcome(key, order[-1], "failed")


def _wire_safely(io: Io, mode: str, key: str, zip_path, test, already_applied: bool = False) -> dict:
    """One agent's unexpected failure is reported and never stops the others (spec: Errors)."""
    try:
        return wire_agent(io, mode, key, zip_path, test, already_applied)
    except Stop:
        raise
    except Exception as exc:  # noqa: BLE001 — a local tool: show the user what failed, keep going
        name = agents.by_key(key).name
        io.say(text.agent_error(name, exc))
        return {"agent": key, "name": name, "method": "error", "self_setup": False, "test": "untested",
                "error": str(exc)}


def go_live(io: Io, mode: str, keys: list[str], zip_path) -> list[dict]:
    rows = {k: wiring.get_row(k) or {"test": "untested", "vouched": 0} for k in keys}
    todo = [k for k in keys if rows[k]["test"] in ("untested", "failed") and not rows[k]["vouched"]]
    if todo and not _on_path():
        io.say(text.NOT_ON_PATH)
    test = test_prompt(io) if todo and mode == GUIDED else None
    return [_wire_safely(io, mode, k, zip_path, test) for k in todo]


def run_tests(io: Io, which: str) -> list[dict]:
    keys = [r["agent"] for r in wiring.rows()] if which == "all" else [which]
    test = test_prompt(io)
    if test is None:
        io.say(text.NO_TEST_SKILL)
        return []
    io.say(text.TEST_CHANGES_FILES)
    results, zip_path = [], None
    try:
        for key in keys:
            row = wiring.get_row(key)
            if row is None:
                io.say(text.not_connected(key))
                continue
            zip_path = zip_path or backup.start("test")
            results.append(_wire_safely(io, GUIDED, key, zip_path, test, already_applied=row["test"] != "failed"))
    except (Stop, KeyboardInterrupt) as exc:
        io.say(text.stopped(str(exc) or "Interrupted."))
    io.say(text.summary(results, zip_path))
    return results
