"""`skillswiki doctor`: what is installed and configured, for bug reports. Reads only; reports whether a key is
set, never its value."""
import json
import os
import platform
import shutil
import zipfile
from datetime import datetime
from pathlib import Path

import skillswiki
from skillswiki import agents, library, paths, store, usage
from skillswiki.decision import jev_enabled

CLIS = ("claude", "codex", "gemini", "agy")
HOOK_MARK = "skillswiki hook"
MCP_MARK = "skillswiki"
EXPORT_PREFIX = "skillswiki-doctor-"


def _tables() -> dict:
    with store.connect() as conn:
        names = [r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' "
                                                   "AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        quoted = {n: '"' + n.replace('"', '""') + '"' for n in names}
        return {n: conn.execute(f"SELECT COUNT(*) AS n FROM {quoted[n]}").fetchone()["n"] for n in names}


def _adopted() -> tuple[list[str], list[str]]:
    with store.connect() as conn:
        rows = conn.execute("SELECT slug, path FROM skills WHERE status = 'adopted' ORDER BY slug").fetchall()
    adopted = [r["slug"] for r in rows]
    missing = [r["slug"] for r in rows if not Path(r["path"]).exists()]
    return adopted, missing


def _json_file(path: Path):
    """The parsed file, None when absent, "unreadable" when present but not readable JSON."""
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
    except (OSError, ValueError):
        return "unreadable"


def _hook_registered(settings) -> str:
    if settings is None:
        return "no"
    try:
        for group in (settings.get("hooks") or {}).get("UserPromptSubmit") or []:
            for hook in group.get("hooks") or []:
                if HOOK_MARK in str(hook.get("command", "")):
                    return "yes"
    except (AttributeError, TypeError):  # "unreadable", or valid JSON of a shape we don't know
        return "unreadable"
    return "no"


def _mcp_registered(config) -> str:
    if config is None:
        return "no"
    try:
        for server in (config.get("mcpServers") or {}).values():
            text = " ".join([str(server.get("command", "")), *(str(a) for a in server.get("args") or [])])
            if MCP_MARK in text:
                return "yes"
    except (AttributeError, TypeError):  # "unreadable", or valid JSON of a shape we don't know
        return "unreadable"
    return "no"


def _local_scope(config, cwd: Path):
    """`claude mcp add` without --scope stores the server under projects[<cwd>] in ~/.claude.json (local scope)."""
    try:
        return config.get("projects", {}).get(str(cwd)) if isinstance(config, dict) else config
    except AttributeError:
        return "unreadable"


def claude_code_status() -> dict:
    home, cwd = Path.home(), Path.cwd()
    claude_json = _json_file(home / ".claude.json")
    return {"hook_user": _hook_registered(_json_file(home / ".claude" / "settings.json")),
            "hook_project": _hook_registered(_json_file(cwd / ".claude" / "settings.json")),
            "mcp_user": _mcp_registered(claude_json),
            "mcp_local": _mcp_registered(_local_scope(claude_json, cwd)),
            "mcp_project": _mcp_registered(_json_file(cwd / ".mcp.json"))}


def _wiring() -> list[dict]:
    from skillswiki import wiring
    return wiring.rows()


def report() -> dict:
    db_present = paths.db_path().is_file()  # checked before any store.connect(), which would create it
    adopted, missing = _adopted() if db_present else ([], [])
    return {"version": skillswiki.__version__, "python": platform.python_version(), "platform": platform.platform(),
            "home": str(paths.home()),
            "db": {"present": db_present, "tables": _tables() if db_present else {}},
            "library": {"path": str(paths.library_dir()),
                        "restore_json": (paths.library_dir() / library.MANIFEST_NAME).is_file(),
                        "adopted": adopted, "missing": missing},
            "agents": agents.rows(),
            "clis": {name: bool(shutil.which(name)) for name in CLIS},
            "typesafe_key_set": bool(os.getenv("TYPESAFE_API_KEY")), "jev": "on" if jev_enabled() else "off",
            "claude_code": claude_code_status(), "wiring": _wiring() if db_present else []}


def human(rep: dict) -> str:
    tables = ", ".join(f"{k} {v}" for k, v in rep["db"]["tables"].items())
    lib = rep["library"]
    detected = [f"{a['key']} ({a['skills']})" for a in rep["agents"] if a["detected"]]
    lines = [f"skillswiki version {rep['version']}  python {rep['python']}  {rep['platform']}",
             f"home: {rep['home']}",
             f"database: {'present' if rep['db']['present'] else 'absent (run: skillswiki scan)'}"
             + (f" — {tables}" if tables else ""),
             f"library: {lib['path']}  RESTORE.json: {'yes' if lib['restore_json'] else 'no'}"
             f"  adopted: {len(lib['adopted'])}"
             + (f"  MISSING: {', '.join(lib['missing'])}" if lib["missing"] else ""),
             "agents detected: " + (", ".join(detected) or "none"),
             "AI CLIs on PATH: " + (", ".join(k for k, v in rep["clis"].items() if v) or "none"),
             f"TypeSafe key set: {'yes' if rep['typesafe_key_set'] else 'no'}  JEV: {rep['jev']}",
             "Agent wiring: " + (", ".join(f"{w['agent']} {w['method']} {w['test']}" for w in rep["wiring"])
                                 or "none"),
             "Claude Code: " + "  ".join(f"{k} {v}" for k, v in rep["claude_code"].items())]
    return "\n".join(lines)


def _env_keys(env_file: Path) -> list[str]:
    """Names of the keys set in ~/.skillswiki/.env, never their values."""
    if not env_file.is_file():
        return []
    keys = []
    for line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip().removeprefix("export ").strip()
        if line and not line.startswith("#") and "=" in line:
            keys.append(line.split("=", 1)[0].strip())
    return sorted(keys)


def export(out_dir: Path) -> Path:
    """Zip the report and the non-secret state (no prompts, no learnings, no skill files) for a bug report."""
    rep = report()
    files: dict[str, object] = {"report.json": rep, "env_keys.json": _env_keys(paths.home() / paths.ENV_FILE_NAME)}
    if rep["db"]["present"]:
        with store.connect() as conn:
            files["skills.json"] = [dict(r) for r in conn.execute("SELECT * FROM skills ORDER BY slug")]
            files["settings.json"] = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM settings")}
        files["usage_summary.json"] = usage.counts("")  # every event; counts only, no text
    stem = f"{EXPORT_PREFIX}{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    path, n = Path(out_dir) / f"{stem}.zip", 1
    while path.exists():  # never overwrite an earlier export
        n += 1
        path = Path(out_dir) / f"{stem}-{n}.zip"
    manifest = paths.library_dir() / library.MANIFEST_NAME
    with zipfile.ZipFile(path, "x", zipfile.ZIP_DEFLATED) as zf:  # exclusive: fails rather than overwrites
        for name, data in files.items():
            zf.writestr(name, json.dumps(data, indent=2, default=str))
        if manifest.is_file():
            zf.write(manifest, library.MANIFEST_NAME)
    return path
