"""Load an adopted skill for the agent: the full SKILL.md, the file list, the absolute folder (so scripts run
where they are), and the user's learnings."""
from pathlib import Path

from skillswiki import discovery, frontmatter, learnings, store, usage

MAX_FILES_LISTED = 200
NOTE = ("Files are on disk at {path}. Read or run them from there. Scripts run with your permissions.")


def load(slug: str) -> dict:
    with store.connect() as conn:
        row = conn.execute("SELECT * FROM skills WHERE slug = ?", (slug,)).fetchone()
    if row is None:
        raise ValueError(f"skill '{slug}' not found — run: skillswiki scan")
    if row["status"] != "adopted":
        raise ValueError(f"skill '{slug}' is not adopted; the agent loads it natively")
    folder = Path(row["path"])
    skill_md = (folder / frontmatter.SKILL_FILE).read_text(encoding="utf-8", errors="replace")
    files = sorted(f.relative_to(folder).as_posix() for f in folder.rglob("*")
                   if f.is_file() and "__pycache__" not in f.parts and f.name != ".DS_Store")
    block = learnings.block_for(slug)
    usage.log(slug, "load", row["context_tokens_est"] + len(block) // discovery.CHARS_PER_TOKEN)
    return {"slug": slug, "path": str(folder), "skill_md": skill_md, "files": files[:MAX_FILES_LISTED],
            "has_scripts": bool(row["has_scripts"]), "learnings": block, "note": NOTE.format(path=folder)}
