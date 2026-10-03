"""Find the skills installed on this machine and mirror them into the store.

Sources, in priority order (the first one to claim a slug wins; later ones are reported as conflicts):
  1. adopted  — folders in the Skills Wiki library (paths.library_dir())
  2. native   — direct child folders with a SKILL.md in each paths.scan_roots() directory
  3. plugin   — skills inside installed Claude Code plugins (read-only; never adopted)
"""
import json
import os
from pathlib import Path

from skillswiki import frontmatter, paths, store

SCRIPT_SUFFIXES = frozenset({".py", ".sh", ".js", ".ts", ".rb", ".pl", ".ps1", ".bash", ".zsh"})
MAX_FILES_INSPECTED = 2000
CHARS_PER_TOKEN = 4
PLUGIN_MANIFEST = Path(".claude") / "plugins" / "installed_plugins.json"


def _skill_dirs(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    return [c for c in sorted(root.iterdir()) if c.is_dir() and (c / frontmatter.SKILL_FILE).is_file()]


def has_scripts(folder: Path) -> bool:
    """True if any file other than SKILL.md looks runnable (script suffix or executable bit)."""
    for i, f in enumerate(Path(folder).rglob("*")):
        if i >= MAX_FILES_INSPECTED:
            break
        if not f.is_file() or f.name == frontmatter.SKILL_FILE:
            continue
        if f.suffix.lower() in SCRIPT_SUFFIXES or os.access(f, os.X_OK):
            return True
    return False


def _entry(folder: Path, status: str, slug: str | None = None) -> dict:
    parsed = frontmatter.parse_skill(folder)
    text = (folder / frontmatter.SKILL_FILE).read_text(encoding="utf-8", errors="replace")
    return {"slug": slug or parsed["slug"], "name": parsed["name"], "description": parsed["description"],
            "status": status, "path": str(folder), "has_scripts": has_scripts(folder),
            "context_tokens_est": len(text) // CHARS_PER_TOKEN}


def _plugin_skills() -> list[tuple[str, Path]]:
    """(slug "<plugin>:<skill>", folder) for installed Claude Code plugins. The manifest is Claude Code's
    internal file, so any parse problem means "no plugin skills", never an error."""
    manifest = Path.home() / PLUGIN_MANIFEST
    try:
        plugins = json.loads(manifest.read_text()).get("plugins") or {}
        found = []
        for key, installs in sorted(plugins.items()):
            plugin = key.split("@", 1)[0]
            for install in installs or []:
                base = Path(install.get("installPath") or "")
                for skill_md in sorted(base.rglob(frontmatter.SKILL_FILE)) if base.is_dir() else []:
                    found.append((f"{plugin}:{skill_md.parent.name}", skill_md.parent))
        return found
    except (OSError, ValueError, AttributeError, TypeError):
        return []


def scan() -> dict:
    """{"skills": [entry], "conflicts": [{"slug", "path", "kept"}]}."""
    candidates = [(d, "adopted", None) for d in _skill_dirs(paths.library_dir())]
    for root in paths.scan_roots():
        candidates += [(d, "native", None) for d in _skill_dirs(root)]
    candidates += [(d, "plugin", slug) for slug, d in _plugin_skills()]

    skills, conflicts, seen = [], [], {}
    for folder, status, slug in candidates:
        entry = _entry(folder, status, slug)
        if entry["slug"] in seen:
            conflicts.append({"slug": entry["slug"], "path": entry["path"], "kept": seen[entry["slug"]]})
            continue
        seen[entry["slug"]] = entry["path"]
        skills.append(entry)
    return {"skills": skills, "conflicts": conflicts}


def sync_db() -> dict:
    """Mirror scan() into the skills table. Adopted rows keep origin/fingerprint/adopted_at; a vanished
    adopted folder is reported as missing (its row is kept so release/repair stays possible)."""
    result = scan()
    found = {s["slug"]: s for s in result["skills"]}
    report = {"added": [], "updated": [], "removed": [], "missing": [], "conflicts": result["conflicts"],
              "total": len(found)}
    ts = store.now()
    with store.connect() as conn:
        existing = {r["slug"]: dict(r) for r in conn.execute("SELECT * FROM skills")}
        for slug, s in found.items():
            report["updated" if slug in existing else "added"].append(slug)
            conn.execute(
                "INSERT INTO skills (slug, name, description, status, path, has_scripts, context_tokens_est, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(slug) DO UPDATE SET name = excluded.name,"
                " description = excluded.description, status = excluded.status, path = excluded.path,"
                " has_scripts = excluded.has_scripts, context_tokens_est = excluded.context_tokens_est,"
                " updated_at = excluded.updated_at",
                (slug, s["name"], s["description"], s["status"], s["path"], int(s["has_scripts"]),
                 s["context_tokens_est"], ts))
            if s["status"] != "adopted":
                conn.execute("UPDATE skills SET origin_path = NULL, fingerprint = NULL, adopted_at = NULL "
                             "WHERE slug = ?", (slug,))
        for slug, row in existing.items():
            if slug in found:
                continue
            if row["status"] == "adopted":
                report["missing"].append(slug)
            else:
                conn.execute("DELETE FROM skills WHERE slug = ?", (slug,))
                report["removed"].append(slug)
    return report
