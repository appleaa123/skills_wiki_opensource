"""`skillswiki setup` (spec Section A): mode → welcome → scan → plan → move → go live → summary. All talk goes through
Io so tests can script the answers; Stop ends the run cleanly at any question."""
from dataclasses import dataclass
from typing import Callable

from skillswiki import agents, backup, discovery, library, store, wiring, wiring_data
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
        io.say(text.summary(result["agents"], zip_path))
    except Stop as exc:
        result["stopped"] = True
        io.say(text.stopped(str(exc)))
    return result
