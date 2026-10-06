"""Everything `skillswiki setup` and `skillswiki uninstall` say, kept in one place so the wording stays consistent."""
from pathlib import Path

from skillswiki import paths

MODES = ("How would you like to set up Skills Wiki?\n"
         "  g  Guided: I explain each step and ask before doing it, then walk you through testing each agent.\n"
         "  a  Automatic: I do every step now without stopping. You test your agents later with:\n"
         "     skillswiki setup --test all")
ASK_MODE = "Guided or automatic? [g/a]"
WELCOME = ("Skills Wiki manages the AI skills you already installed. It routes each request to the right skill, keeps "
           "your corrections as learnings, and evaluates skills on your own tokens. Everything stays on this "
           "machine.\n\nWhat happens next:\n"
           "  1. I scan every agent's skill folders on this machine.\n"
           "  2. I show you the plan: which skills move into one central library, and how each agent gets connected.\n"
           "  3. I back up every skill folder and settings file I touch, then move the skills.\n"
           "  4. I connect each agent so it asks Skills Wiki for the right skill, and help you test it.\n"
           "  5. I show you a summary.\n"
           "Nothing changes until you confirm. `skillswiki uninstall` puts every skill back where it was.")
BENEFITS = ("Why move them: one central library instead of a copy per agent; your agent sees only the skill that fits "
            "instead of every skill's description; your corrections are kept per skill and applied every time; and "
            "you can evaluate each skill against your own preferences. Uninstalling puts every skill back in its "
            "original folder.")
WIRING_INTRO = ("How each agent gets connected. Every agent is treated the same way: first its hook, then a rule in its "
                "rules file, each followed by a quick test; if neither works, I tell you what to do. Where an agent "
                "supports it, I also register the Skills Wiki server so it can reach your skills.")
WAYS = {"hook": "hook", "rules": "rules file"}
ASK_GO = "Back up, move these skills and connect your agents now? [y/n]"
ASK_WIRE = "Do this now? [y/n]"
DECLINED = "You chose not to continue."
DRY_RUN_END = "Dry run: nothing was changed. Run `skillswiki setup` to do it."
NOT_ON_PATH = ("Warning: the `skillswiki` command is not on your PATH, so your agents' hooks can't run it. Run "
               "`pipx ensurepath`, open a new terminal, then run `skillswiki setup` again.")
GUI_PATH_NOTE = ("Note: an agent you start from the Dock or Start menu may not see your terminal's PATH. If its test "
                 "fails, start it from a terminal and run `skillswiki setup --test <agent>` again.")
TRY_NEXT = "Undoing that and trying the next way."
NO_TEST_SKILL = "There is no adopted skill to test with yet, so testing is skipped. Later: skillswiki setup --test all"
TEST_CHANGES_FILES = ("If an agent fails its test, I undo its current connection and try the next way, backing up each "
                      "file first.")


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'s' if n != 1 else ''}"


def detected_lines(rows: list[dict], plugins: list[str], conflicts: list[dict]) -> str:
    lines = ["Agents found on this machine:"]
    lines += [f"  {r['name']}: {_plural(r['skills'], 'skill')}" for r in rows] or ["  none"]
    if plugins:
        lines.append("Plugin skills (managed by their plugin, never moved): " + ", ".join(plugins))
    library = str(paths.library_dir())
    lines += [f"  note: {c['slug']} is already in Skills Wiki; the other copy at {c['path']} stays where it is"
              for c in conflicts if str(c["kept"]).startswith(library)]
    return "\n".join(lines)


def plan_lines(preview: dict) -> str:
    moving = preview["adopted"]
    if not moving and not preview["failed"]:
        return "No new skills to move: every skill is already in Skills Wiki."
    lines = [f"{_plural(len(moving), 'skill')} will move into {paths.library_dir()}:"]
    for p in moving:
        lines.append(f"  {p['slug']}  (from {p['from']})")
        lines += [f"    + the identical copy at {c} moves with it" for c in p["moved_copies"]]
        lines += [f"    note: a different copy stays at {c}; that agent may still use it on its own"
                  for c in p["differing_copies"]]
        lines += [f"    note: {c} is what a link points to; it stays so the link keeps working"
                  for c in p["linked_copies"]]
        lines += [f"    warning: {w['message']}" for w in p.get("warnings", [])]
    lines += [f"  can't move {f['slug']}: {f['error']}" for f in preview["failed"]]
    return "\n".join(lines)


def wiring_lines(items: list[tuple[str, list[str], str]]) -> str:
    lines = [WIRING_INTRO]
    for name, methods, notes in items:
        ways = [WAYS[m] for m in methods if m in WAYS]
        how = " → ".join(ways) if ways else "no automatic way; you'll get a short instruction"
        lines.append(f"  {name}: {how}" + (f"  ({notes})" if notes else ""))
    return "\n".join(lines)


def move_lines(result: dict, zip_path: Path) -> str:
    lines = [f"Moved {_plural(len(result['adopted']), 'skill')}. Backup: {zip_path}"]
    lines += [f"  could not move {f['slug']}: {f['error']}" for f in result["failed"]]
    return "\n".join(lines)


def wire_intro(name: str, description: str) -> str:
    return f"{name}: {description}."


def cannot_edit(name: str, reason: str) -> str:
    return f"I can't edit {name}'s settings myself ({reason}). {name} can do it after your approval:"


def mcp_not_registered(name: str, reason: str) -> str:
    return f"The Skills Wiki server is not registered for {name}: {reason}"


def test_instructions(name: str, request: str) -> str:
    return (f"Test {name}: restart it (agents read their settings at startup), open a new chat, and send:\n\n"
            f"    {request}\n\nThen come back here. {GUI_PATH_NOTE}")


def ask_test(name: str, slug: str) -> str:
    return f"Did {name} check Skills Wiki or use the skill {slug}? [y = yes, n = no, s = skip for now]"


def agent_error(name: str, exc: BaseException) -> str:
    return f"{name}: something went wrong ({exc}). Skipping it; the others continue."


def not_connected(key: str) -> str:
    return f"{key} is not connected yet; run `skillswiki setup` first."


def _status(o: dict) -> str:
    name, way = o["name"], WAYS.get(o["method"], o["method"])
    if o["method"] == "error":
        return f"{name}: not connected ({o.get('error', 'error')}). Fix it, then run `skillswiki setup` again."
    if o["method"] == "skipped":
        return f"{name}: skipped; run `skillswiki setup` again to connect it."
    if o["method"] == "advice":
        return f"{name}: tell it \"check Skills Wiki\" when you need one of your skills (instruction above)."
    if o["test"] == "tested_ok":
        return f"{name}: asks Skills Wiki through its {way} (tested)."
    after = f" once you've pasted the prompt above into {name}" if o["self_setup"] else ""
    return f"{name}: connected through its {way}{after}, not tested yet. Test it: skillswiki setup --test {o['agent']}"


def summary(outcomes: list[dict], zip_path: Path | None) -> str:
    lines = ["Summary:"] + [f"  {_status(o)}" for o in outcomes]
    lines += [f"Backup: {zip_path}"] if zip_path else []
    lines += ["Installed new skills later? Run `skillswiki setup` (or `skillswiki adopt --all`) again.",
              f"To undo everything: `skillswiki uninstall` puts every skill back. Your learnings stay in {paths.home()}."]
    return "\n".join(lines)


def stopped(reason: str) -> str:
    return f"{reason} Stopped here; everything done so far is listed above. Run the command again to continue."


UNINSTALL_INTRO = ("Uninstalling Skills Wiki. I will:\n"
                   "  1. save your current learnings as Markdown files,\n"
                   "  2. put every skill back in its original folder,\n"
                   "  3. remove the hooks, rules and server entries setup added (backing up each file first),\n"
                   "  4. ask whether to keep the backups.\n"
                   "Then you remove the program itself with pipx.")
ASK_UNINSTALL = "Go ahead? [y/n]"
ASK_DELETE_BACKUPS = "Delete the backups now? [y/n]"
DRY_RUN_END_UNINSTALL = "Dry run: nothing was changed. Run `skillswiki uninstall` to do it."
RELEASE_BLOCKED = ("Some skills could not go back (see above), so I stopped before disconnecting your agents: they can "
                   "still reach the skills that are left. Fix each problem, then run `skillswiki uninstall` again.")


def uninstall_plan(preview: dict, rows: list[dict]) -> str:
    lines = [f"{_plural(len(preview['released']), 'skill')} go back:"]
    lines += [f"  {r['slug']} -> {r['to']}" for r in preview["released"]]
    lines += [f"  can't go back: {f['slug']}: {f['error']}" for f in preview["failed"]]
    lines.append(f"{_plural(len(rows), 'agent')} get disconnected: " + (", ".join(r["agent"] for r in rows) or "none"))
    return "\n".join(lines)


def exported(files: list[str]) -> str:
    return ("Saved your learnings:\n" + "\n".join(f"  {f}" for f in files)) if files else "No learnings to save."


def released(result: dict) -> str:
    lines = [f"Put back {_plural(len(result['released']), 'skill')}."]
    lines += [f"  FAILED {f['slug']}: {f['error']}" for f in result["failed"]]
    return "\n".join(lines)


def disconnected(removed: list[dict]) -> str:
    return "Disconnected: " + (", ".join(r["agent"] for r in removed) or "no agents")


def backup_warning() -> str:
    return (f"Backups of your skills and settings are in {paths.home() / 'backups'}. Deleting {paths.home()} later "
            "also deletes them, and your learnings.")


def uninstall_final() -> str:
    return (f"Done. Finish with:\n  pipx uninstall skillswiki\nYour learnings stay in {paths.db_path()} and as Markdown "
            f"in {paths.home() / 'learnings'}; any agent can read those files.")
