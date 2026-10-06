"""`skillswiki uninstall` (spec Section E): explain → export learnings → put every skill back → disconnect agents →
keep or delete backups → the pipx command. Stops before disconnecting if any skill could not go back."""
from pathlib import Path

from skillswiki import backup, learnings, library, wiring, wiring_data, wiring_io
from skillswiki import setup_text as text
from skillswiki.setup_flow import AUTOMATIC, YES, NO, Io, Stop, confirm

FILE_KINDS = {"json_hook", "json_mcp", "toml_block", "md_block"}


def _back_up_agent(zip_path: Path, key: str) -> None:
    w = wiring_data.by_key(key)
    for t in (w.hook, w.rules, w.mcp):
        if t is not None and t.kind in FILE_KINDS:
            backup.add_file(zip_path, wiring_io.resolve(t))


def _disconnect(io: Io, zip_path: Path) -> list[dict]:
    removed = []
    for row in wiring.rows():
        try:
            _back_up_agent(zip_path, row["agent"])
            res = wiring.remove_all(row["agent"])
        except Exception as exc:  # noqa: BLE001 — report this agent and keep disconnecting the others
            io.say(text.agent_error(row["agent"], exc))
            continue
        for prompt in (res["method"].get("prompt"), res["mcp"].get("prompt")):
            if prompt:
                io.say(prompt)
        removed.append(res)
    io.say(text.disconnected(removed))
    return removed


def _delete_backups(io: Io, mode: str, delete_backups: bool) -> bool:
    if mode == AUTOMATIC:
        return delete_backups
    io.say(text.backup_warning())
    return io.ask(text.ASK_DELETE_BACKUPS, (YES, NO)) == YES


def run(io: Io, mode: str, dry_run: bool = False, delete_backups: bool = False) -> dict:
    result = {"stopped": False, "blocked": False, "exported": [], "released": None, "disconnected": [],
              "backups_deleted": 0}
    try:
        io.say(text.UNINSTALL_INTRO)
        io.say(text.uninstall_plan(library.release_all(dry_run=True), wiring.rows()))
        if dry_run:
            io.say(text.DRY_RUN_END_UNINSTALL)
            return result
        confirm(io, mode, text.ASK_UNINSTALL)
        result["exported"] = [str(p) for p in learnings.export_markdown()]
        io.say(text.exported(result["exported"]))
        result["released"] = library.release_all()
        io.say(text.released(result["released"]))
        if result["released"]["failed"]:
            result["blocked"] = True
            io.say(text.RELEASE_BLOCKED)
            return result
        result["disconnected"] = _disconnect(io, backup.start("uninstall"))
        if _delete_backups(io, mode, delete_backups):
            result["backups_deleted"] = backup.delete_all()
        io.say(text.uninstall_final())
    except (Stop, KeyboardInterrupt) as exc:
        result["stopped"] = True
        io.say(text.stopped(str(exc) or "Interrupted."))
    return result
