"""Reversible edits to agent config files. Writes are atomic, the pre-setup bytes are kept, and each file records
which wiring parts use it (table wiring_files), so removing the last part puts the file back exactly."""
import copy
import hashlib
import json
import os
import re
import tomllib
from pathlib import Path

from skillswiki import paths, store
from skillswiki.errors import SkillsWikiError
from skillswiki.wiring_data import MCP_NAME, RULES_TEXT, Target

ORIGINALS_DIR = "wiring-originals"
TMP_SUFFIX = ".skillswiki-tmp"
MD_BEGIN, MD_END = "<!-- skillswiki:begin -->", "<!-- skillswiki:end -->"
TOML_BEGIN, TOML_END = "# skillswiki:begin", "# skillswiki:end"
MD_BLOCK = f"{MD_BEGIN}\n{RULES_TEXT}\n{MD_END}\n"
TOML_BLOCK = f'{TOML_BEGIN}\n[mcp_servers.{MCP_NAME}]\ncommand = "skillswiki"\nargs = ["serve-mcp"]\n{TOML_END}\n'
MCP_ENTRIES = {"std": {"command": "skillswiki", "args": ["serve-mcp"]},
               "opencode": {"type": "local", "command": ["skillswiki", "serve-mcp"]},
               "copilot": {"type": "local", "command": "skillswiki", "args": ["serve-mcp"], "tools": ["*"]}}


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + TMP_SUFFIX)
    tmp.write_bytes(data)
    try:
        os.replace(tmp, path)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise


def resolve(t: Target) -> Path:
    return Path.home() / t.path


# --- bookkeeping -------------------------------------------------------------------------------------------------

def _record(path: Path) -> dict | None:
    with store.connect() as conn:
        row = conn.execute("SELECT * FROM wiring_files WHERE path = ?", (str(path),)).fetchone()
    return {**dict(row), "parts": json.loads(row["parts"])} if row else None


def _save(path: Path, rec: dict, written_sha: str, parts: list[dict]) -> None:
    with store.connect() as conn:
        if not parts:
            conn.execute("DELETE FROM wiring_files WHERE path = ?", (str(path),))
            return
        conn.execute("INSERT INTO wiring_files (path, created, original, written_sha, parts) VALUES (?, ?, ?, ?, ?) "
                     "ON CONFLICT(path) DO UPDATE SET written_sha = excluded.written_sha, parts = excluded.parts",
                     (str(path), rec["created"], rec["original"], written_sha, json.dumps(parts)))


def _keep_original(path: Path) -> str:
    kept = paths.home() / ORIGINALS_DIR / (_sha(str(path).encode("utf-8"))[:16] + ".orig")
    kept.parent.mkdir(parents=True, exist_ok=True)
    kept.write_bytes(path.read_bytes())
    return str(kept)


def _write(path: Path, data: bytes, user: str, part: str) -> None:
    rec = _record(path)
    if rec is None:
        created = not path.exists()
        rec = {"created": int(created), "original": None if created else _keep_original(path), "parts": []}
    wrote = not path.exists() or path.read_bytes() != data
    if wrote:
        atomic_write(path, data)
    parts = [p for p in rec["parts"] if p["user"] != user] + [{"user": user, "part": part}]
    # Only a real write changes what we own: re-registering must not adopt the user's later edits as ours.
    _save(path, rec, _sha(data) if wrote or "written_sha" not in rec else rec["written_sha"], parts)


def _put_back(path: Path, rec: dict) -> str:
    if rec["created"]:
        path.unlink(missing_ok=True)
        return "deleted"
    kept = Path(rec["original"])
    atomic_write(path, kept.read_bytes())
    kept.unlink(missing_ok=True)
    return "restored"


# --- per-kind content ------------------------------------------------------------------------------------------

def _text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8") if path.exists() else ""
    except UnicodeDecodeError as exc:
        raise SkillsWikiError("INVALID_INPUT", f"{path} is not UTF-8 text; not editing it", path=str(path)) from exc


def _loads(text: str, path: Path) -> dict:
    if not text.strip():
        return {}
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise SkillsWikiError("INVALID_INPUT", f"{path} is not plain JSON ({exc}); not editing it",
                              path=str(path)) from exc
    if not isinstance(data, dict):
        raise SkillsWikiError("INVALID_INPUT", f"{path} is not a JSON object; not editing it", path=str(path))
    return data


def load_json(path: Path) -> dict:
    return _loads(_text(path), path)


def _dump(data: dict) -> bytes:
    return (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def hook_entry(d: dict) -> dict:
    if d["group"]:
        return {"hooks": [{"type": "command", "command": d["command"]}]}
    return {"type": "command", "command": d["command"], **({"timeout": d["timeout"]} if d.get("timeout") else {})}


def _commands(entry) -> list[str]:
    inner = entry.get("hooks", [entry]) if isinstance(entry, dict) else []
    return [h.get("command", "") for h in inner if isinstance(h, dict)]


def _node(data: dict, keys: list[str], path: Path, create: bool):
    node = data
    for key in keys:
        if key not in node:
            if not create:
                return None
            node[key] = {} if key != keys[-1] else []
        node = node[key]
        expected = list if key == keys[-1] else dict
        if not isinstance(node, expected):
            raise SkillsWikiError("INVALID_INPUT", f"{path}: '{key}' has an unexpected shape; not editing it",
                                  path=str(path))
    return node


def _hook_add(text: str, d: dict, path: Path) -> bytes | None:
    data = _loads(text, path)
    entries = _node(data, d["keys"], path, create=False) or []
    if any(c == d["command"] or c in d.get("legacy", []) for e in entries for c in _commands(e)):
        return None
    new = copy.deepcopy(data)
    _node(new, d["keys"], path, create=True).append(hook_entry(d))
    return _dump(new)


def _prune(data: dict, keys: list[str]) -> None:
    for depth in range(len(keys), 0, -1):
        parent = data
        for key in keys[:depth - 1]:
            parent = parent[key]
        if parent.get(keys[depth - 1]) in ({}, []):
            del parent[keys[depth - 1]]
        else:
            return


def _hook_strip(text: str, d: dict, path: Path) -> bytes:
    new = copy.deepcopy(_loads(text, path))
    entries = _node(new, d["keys"], path, create=False)
    if entries is None:
        return _dump(new)
    kept = []
    for e in entries:
        if isinstance(e, dict) and "hooks" in e:
            inner = [h for h in e["hooks"] if not (isinstance(h, dict) and h.get("command") == d["command"])]
            kept += [{**e, "hooks": inner}] if inner else []
        elif d["command"] not in _commands(e):
            kept.append(e)
    entries[:] = kept
    _prune(new, d["keys"])
    return _dump(new)


def _mcp_add(text: str, d: dict, path: Path) -> bytes | None:
    data = _loads(text, path)
    container = data.get(d["key"], {})
    if not isinstance(container, dict):
        raise SkillsWikiError("INVALID_INPUT", f"{path}: '{d['key']}' is not an object", path=str(path))
    if MCP_NAME in container:
        return None
    return _dump({**data, d["key"]: {**container, MCP_NAME: MCP_ENTRIES[d["entry"]]}})


def _mcp_strip(text: str, d: dict, path: Path) -> bytes:
    data = _loads(text, path)
    container = {k: v for k, v in data.get(d["key"], {}).items() if k != MCP_NAME}
    rest = {k: v for k, v in data.items() if k != d["key"]}
    return _dump({**rest, d["key"]: container} if container else rest)


def _block_add(text: str, block: str, begin: str) -> str | None:
    if begin in text:
        return None
    sep = "" if not text or text.endswith("\n") else "\n"
    return text + sep + ("\n" if text.strip() else "") + block


def _block_strip(text: str, begin: str, end: str) -> str:
    return re.sub(rf"\n?{re.escape(begin)}\n.*?{re.escape(end)}\n?", "", text, flags=re.S)


def _toml_add(text: str, path: Path) -> bytes | None:
    try:
        if MCP_NAME in tomllib.loads(text).get("mcp_servers", {}):
            return None
        new = _block_add(text, TOML_BLOCK, TOML_BEGIN)
        tomllib.loads(new or text)
    except (tomllib.TOMLDecodeError, AttributeError) as exc:
        raise SkillsWikiError("INVALID_INPUT", f"{path}: cannot add the Skills Wiki server ({exc}); not editing it",
                              path=str(path)) from exc
    return new.encode("utf-8") if new else None


def _planned(t: Target, path: Path) -> tuple[bytes | None, str]:
    """(new file bytes, or None when an equal part is already there; the part id)."""
    text, d = _text(path), t.detail
    if t.kind == "json_hook":
        return _hook_add(text, d, path), "hook:" + d["command"]
    if t.kind == "json_mcp":
        return _mcp_add(text, d, path), "mcp:" + d["key"]
    if t.kind == "toml_block":
        return _toml_add(text, path), "toml:mcp"
    if t.kind == "md_block":
        new = _block_add(text, MD_BLOCK, MD_BEGIN)
        return (new.encode("utf-8") if new else None), "md"
    raise SkillsWikiError("INVALID_INPUT", f"{t.kind} targets are not files", kind=t.kind)


def _stripped(t: Target, current: bytes, path: Path) -> bytes:
    text = current.decode("utf-8", errors="replace")
    if t.kind == "json_hook":
        return _hook_strip(text, t.detail, path)
    if t.kind == "json_mcp":
        return _mcp_strip(text, t.detail, path)
    if t.kind == "toml_block":
        return _block_strip(text, TOML_BEGIN, TOML_END).encode("utf-8")
    return _block_strip(text, MD_BEGIN, MD_END).encode("utf-8")  # md_block


# --- public ------------------------------------------------------------------------------------------------------

def apply(t: Target, user: str) -> str:
    path = resolve(t)
    new, part = _planned(t, path)
    rec = _record(path)
    ours = rec is not None and any(p["part"] == part for p in rec["parts"])
    if new is None and not ours:
        return "present"
    _write(path, new if new is not None else path.read_bytes(), user, part)
    return "written" if new is not None else "shared"


def forget(t: Target, user: str) -> None:
    """Drop `user` from the file's record without touching the file (its part could not be removed)."""
    path = resolve(t)
    rec = _record(path)
    if rec:
        _save(path, rec, rec["written_sha"], [p for p in rec["parts"] if p["user"] != user])


def remove(t: Target, user: str) -> str:
    path = resolve(t)
    rec = _record(path)
    mine = [p for p in (rec["parts"] if rec else []) if p["user"] == user]
    if not mine:
        return "kept"
    rest = [p for p in rec["parts"] if p["user"] != user]
    if not path.exists():
        _save(path, rec, rec["written_sha"], [])
        return "gone"
    if any(p["part"] == mine[0]["part"] for p in rest):
        _save(path, rec, rec["written_sha"], rest)
        return "kept"
    current = path.read_bytes()
    original_kept = rec["created"] or Path(rec["original"]).exists()
    if not rest and _sha(current) == rec["written_sha"] and original_kept:
        _save(path, rec, rec["written_sha"], [])
        return _put_back(path, rec)
    new = _stripped(t, current, path)
    if not rest and rec["created"] and not new.strip():
        path.unlink()
        _save(path, rec, rec["written_sha"], [])
        return "deleted"
    atomic_write(path, new)
    _save(path, rec, _sha(new), rest)
    return "edited"
