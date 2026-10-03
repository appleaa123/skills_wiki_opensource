"""Adopt / release: move a skill folder between an agent's native skills directory and the Skills Wiki
library. Adopting stops the agent's own trigger from firing; Skills Wiki then routes and loads the skill.

Safety: every move is reversible, never overwrites an existing path, and touches only the library and the
recorded origin. A symlinked skill is moved as a link (its target is never touched).
"""
import hashlib
import shutil
from pathlib import Path

from skillswiki import discovery, paths, store

FINGERPRINT_CHARS = 16
_SKIP_NAMES = frozenset({"__pycache__", ".DS_Store"})


def fingerprint(folder: Path) -> str:
    """sha256 over sorted (relative path, bytes) of every file, skipping caches. First 16 hex chars."""
    root = Path(folder)
    digest = hashlib.sha256()
    files = sorted(f for f in root.rglob("*")
                   if f.is_file() and not _SKIP_NAMES.intersection(f.relative_to(root).parts))
    for f in files:
        digest.update(f.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        digest.update(f.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()[:FINGERPRINT_CHARS]


def _row(slug: str) -> dict:
    with store.connect() as conn:
        row = conn.execute("SELECT * FROM skills WHERE slug = ?", (slug,)).fetchone()
    if row is None:
        raise ValueError(f"skill '{slug}' not found — run: skillswiki scan")
    return dict(row)


def _exists(path: Path) -> bool:
    return path.exists() or path.is_symlink()


def adopt(slug: str) -> dict:
    row = _row(slug)
    if row["status"] == "plugin":
        raise ValueError(f"'{slug}' is plugin-managed; disable it in your agent's plugin settings instead")
    if row["status"] == "adopted":
        raise ValueError(f"'{slug}' is already adopted")
    source = Path(row["path"])
    target = paths.library_dir() / slug
    if not _exists(source):
        raise ValueError(f"'{slug}' is no longer at {source} — run: skillswiki scan")
    if _exists(target):
        raise ValueError(f"{target} already exists; move or remove it first")
    shutil.move(str(source), str(target))
    with store.connect() as conn:
        conn.execute("UPDATE skills SET status = 'adopted', path = ?, origin_path = ?, fingerprint = ?, "
                     "adopted_at = ?, updated_at = ? WHERE slug = ?",
                     (str(target), str(source), fingerprint(target), store.now(), store.now(), slug))
    others = [c["path"] for c in discovery.scan()["conflicts"] if c["slug"] == slug]
    return {"slug": slug, "from": str(source), "to": str(target), "other_copies": others}


def release(slug: str) -> dict:
    row = _row(slug)
    if row["status"] != "adopted" or not row["origin_path"]:
        raise ValueError(f"'{slug}' is not adopted")
    source = Path(row["path"])
    origin = Path(row["origin_path"])
    if _exists(origin):
        raise ValueError(f"{origin} already exists; move or remove it first")
    origin.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(origin))
    with store.connect() as conn:
        conn.execute("UPDATE skills SET status = 'native', path = ?, origin_path = NULL, fingerprint = NULL, "
                     "adopted_at = NULL, updated_at = ? WHERE slug = ?", (str(origin), store.now(), slug))
    return {"slug": slug, "from": str(source), "to": str(origin)}


def changed(slug: str) -> bool:
    """True when an adopted skill's files differ from what was adopted."""
    row = _row(slug)
    if row["status"] != "adopted" or not row["fingerprint"]:
        return False
    return fingerprint(Path(row["path"])) != row["fingerprint"]
