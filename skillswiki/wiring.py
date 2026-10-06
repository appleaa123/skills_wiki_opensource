"""Per-agent wiring: the methods that apply (hook → rules → advice), applying and undoing one, MCP registration,
the self-setup prompts for what Skills Wiki cannot edit itself (spec D31), and one `wiring` row per agent."""
import json
import shutil
import subprocess
from pathlib import Path

from skillswiki import agents, backup, store, wiring_io
from skillswiki.errors import SkillsWikiError
from skillswiki.wiring_data import MCP_NAME, RULES_TEXT, Target, by_key

METHODS = ("hook", "rules", "advice")
CLI_TIMEOUT_S = 60


def _which(name: str) -> str | None:
    """Seam for tests: they switch agent CLIs off here instead of patching shutil globally."""
    return shutil.which(name)
ADVICE = ("{name} can't be set up to ask Skills Wiki automatically. When you need one of your skills, tell it "
          "\"check Skills Wiki\", or add this to its global rules yourself:\n\n{rules}")


def detected_keys() -> list[str]:
    return [a.key for a in agents.AGENTS if agents.detected(a)]


def methods(key: str) -> list[str]:
    w = by_key(key)
    return [m for m in METHODS if m == "advice" or getattr(w, m) is not None]


def _target(key: str, method: str) -> Target | None:
    return getattr(by_key(key), method) if method in ("hook", "rules") else None


def _user(key: str, slot: str) -> str:
    return f"{key}:{slot}"


def _snippet(t: Target) -> tuple[str, str, str]:
    """(language, text to add, where it goes) for any target."""
    d = t.detail
    if t.kind == "self_setup":
        return d["lang"], d["snippet"], d["where"]
    if t.kind == "json_hook":
        return "json", json.dumps(wiring_io.hook_entry(d), indent=2), f"{t.path}, in the list at {' → '.join(d['keys'])}"
    if t.kind == "json_mcp":
        entry = {MCP_NAME: wiring_io.MCP_ENTRIES[d["entry"]]}
        return "json", json.dumps(entry, indent=2), f"{t.path}, inside \"{d['key']}\""
    if t.kind == "toml_block":
        return "toml", wiring_io.TOML_BLOCK.strip(), t.path
    if t.kind == "cli":
        return "shell", " ".join(d["add"]), "your terminal"
    return "markdown", wiring_io.MD_BLOCK.strip(), t.path


def self_setup_prompt(key: str, t: Target) -> str:
    lang, snippet, where = _snippet(t)
    if t.detail.get("audience") == "user":
        return f"In {where}, add this text:\n\n{snippet}"
    return (f"Paste this into {agents.by_key(key).name}:\n\nPlease add a Skills Wiki entry to your own configuration: "
            f"{where} (in my home folder). Add exactly this {lang}, keep the part marked 'skillswiki', and change "
            f"nothing else. Show me the change and wait for my approval before editing.\n\n```{lang}\n{snippet}\n```")


def self_removal_prompt(key: str, t: Target) -> str:
    _, _, where = _snippet(t)
    if t.detail.get("audience") == "user":
        return f"In {where}, delete the Skills Wiki rule you added."
    return (f"Paste this into {agents.by_key(key).name}:\n\nPlease remove the Skills Wiki entry (the part marked "
            f"'skillswiki') from {where} in my home folder. Change nothing else. Show me the change and wait for my "
            "approval before editing.")


def advice(key: str) -> str:
    return ADVICE.format(name=agents.by_key(key).name, rules=RULES_TEXT)


def describe(key: str, method: str) -> str:
    t = _target(key, method)
    if t is None:
        return "no automatic way; you'll get a short instruction instead"
    if t.kind == "self_setup":
        return f"{agents.by_key(key).name} adds it itself after you approve (setup gives you the prompt)"
    return f"add a Skills Wiki {method} to {wiring_io.resolve(t)}"


def _apply_target(key: str, slot: str, t: Target, zip_path: Path | None) -> dict:
    if t.kind == "self_setup":
        return {"state": "self_setup", "file": None, "prompt": self_setup_prompt(key, t), "reason": None}
    path = wiring_io.resolve(t)
    try:
        if zip_path:
            backup.add_file(zip_path, path)
        return {"state": wiring_io.apply(t, _user(key, slot)), "file": str(path), "prompt": None, "reason": None}
    except SkillsWikiError as exc:
        return {"state": "self_setup", "file": str(path), "prompt": self_setup_prompt(key, t), "reason": str(exc)}


def _claude_style_mcp(t: Target) -> dict:
    try:
        known = wiring_io.load_json(Path.home() / t.detail["check_file"]).get(t.detail["check_key"], {})
    except SkillsWikiError:
        known = {}
    if MCP_NAME in known:
        return {"state": "present"}
    exe = _which(t.detail["add"][0])
    if exe is None:
        return {"state": "none", "reason": f"{t.detail['add'][0]} is not on PATH; run: {' '.join(t.detail['add'])}"}
    try:
        proc = subprocess.run([exe, *t.detail["add"][1:]], capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=CLI_TIMEOUT_S, stdin=subprocess.DEVNULL)
    except (subprocess.SubprocessError, OSError) as exc:
        return {"state": "none", "reason": f"{type(exc).__name__}; run it yourself: {' '.join(t.detail['add'])}"}
    return {"state": "done"} if proc.returncode == 0 else {"state": "none", "reason": proc.stderr.strip()[:300]}


def register_mcp(key: str, zip_path: Path | None = None) -> dict:
    t = by_key(key).mcp
    if t is None:
        return {"state": "none"}
    if t.kind == "cli":
        return _claude_style_mcp(t)
    res = _apply_target(key, "mcp", t, zip_path)
    state = {"written": "done", "shared": "done"}.get(res["state"], res["state"])
    return {"state": state, **({"prompt": res["prompt"]} if res["prompt"] else {})}


def _save_row(key: str, method: str, self_setup: bool, mcp: str, test: str) -> None:
    with store.connect() as conn:
        conn.execute("INSERT INTO wiring (agent, method, self_setup, mcp, test, updated_at) VALUES (?, ?, ?, ?, ?, ?) "
                     "ON CONFLICT(agent) DO UPDATE SET method = excluded.method, self_setup = excluded.self_setup, "
                     "mcp = excluded.mcp, test = excluded.test, updated_at = excluded.updated_at",
                     (key, method, int(self_setup), mcp, test, store.now()))


def get_row(key: str) -> dict | None:
    with store.connect() as conn:
        row = conn.execute("SELECT * FROM wiring WHERE agent = ?", (key,)).fetchone()
    return dict(row) if row else None


def rows() -> list[dict]:
    with store.connect() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM wiring ORDER BY agent")]


def apply(key: str, method: str, zip_path: Path | None = None) -> dict:
    w = by_key(key)
    previous = get_row(key)
    result = {"agent": key, "method": method, "state": "advice", "file": None, "prompt": None, "reason": None,
              "notes": w.notes, "advice": advice(key) if method == "advice" else None}
    t = _target(key, method)
    if t is not None:
        result.update(_apply_target(key, method, t, zip_path))
    mcp = register_mcp(key, zip_path) if not previous or previous["mcp"] in ("none",) else {"state": previous["mcp"]}
    result["mcp"] = mcp
    _save_row(key, method, result["state"] == "self_setup", mcp["state"], "untested")
    return result


def undo(key: str, method: str) -> dict:
    t, row = _target(key, method), get_row(key)
    if t is None:
        return {"state": "none", "prompt": None}
    if t.kind == "self_setup" or (row and row["self_setup"] and row["method"] == method):
        return {"state": "self_setup", "prompt": self_removal_prompt(key, t)}
    return _remove_file_part(key, method, t)


def _remove_file_part(key: str, slot: str, t: Target) -> dict:
    """Remove our part from a file; if the file can no longer be read or written, hand the user a removal prompt."""
    try:
        return {"state": wiring_io.remove(t, _user(key, slot)), "prompt": None}
    except (SkillsWikiError, OSError):
        wiring_io.forget(t, _user(key, slot))
        return {"state": "self_setup", "prompt": self_removal_prompt(key, t)}


def _remove_mcp(key: str, state: str) -> dict:
    t = by_key(key).mcp
    if t is None or state in ("none", "present"):
        return {"state": state}
    if state == "self_setup":
        return {"state": "self_setup", "prompt": self_removal_prompt(key, t)}
    if t.kind == "cli":
        exe = _which(t.detail["remove"][0])
        if exe is None:
            return {"state": "self_setup", "prompt": f"Run: {' '.join(t.detail['remove'])}"}
        try:
            subprocess.run([exe, *t.detail["remove"][1:]], capture_output=True, timeout=CLI_TIMEOUT_S,
                           stdin=subprocess.DEVNULL)
        except (subprocess.SubprocessError, OSError):
            return {"state": "self_setup", "prompt": f"Run: {' '.join(t.detail['remove'])}"}
        return {"state": "removed"}
    return _remove_file_part(key, "mcp", t)


def remove_all(key: str) -> dict:
    row = get_row(key)
    if row is None:
        return {"agent": key, "method": {"state": "none", "prompt": None}, "mcp": {"state": "none"}}
    result = {"agent": key, "method": undo(key, row["method"]), "mcp": _remove_mcp(key, row["mcp"])}
    with store.connect() as conn:
        conn.execute("DELETE FROM wiring WHERE agent = ?", (key,))
    return result


def set_test(key: str, result: str) -> None:
    """Record the user's own test result; it replaces any earlier 'tested by Skills Wiki' mark."""
    with store.connect() as conn:
        conn.execute("UPDATE wiring SET test = ?, vouched = 0, updated_at = ? WHERE agent = ?",
                     (result, store.now(), key))


def set_vouched(key: str) -> None:
    """The user skipped the test because Skills Wiki tested this agent itself (wiring_data tested)."""
    with store.connect() as conn:
        conn.execute("UPDATE wiring SET test = 'untested', vouched = 1, updated_at = ? WHERE agent = ?",
                     (store.now(), key))
