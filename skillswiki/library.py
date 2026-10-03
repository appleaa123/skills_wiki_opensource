"""Adopt / release: move a skill folder between an agent's native skills directory and the Skills Wiki
library. Adopting stops the agent's own trigger from firing; Skills Wiki then routes and loads the skill.

Safety: every move is reversible, never overwrites an existing path, and touches only the library and the
recorded origins. Identical copies of the skill in other agents' folders move with it and come back with it. A symlinked skill is moved as a link (its target is never touched).
"""
import hashlib
import json
import shutil
from pathlib import Path

from skillswiki import discovery, paths, store

FINGERPRINT_CHARS = 16
COPIES_DIR = ".copies"  # library/.copies/<slug>/<n>: identical copies adopted along with a skill
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


def _copies_dir(slug: str) -> Path:
    return paths.library_dir() / COPIES_DIR / slug


def _other_native_copies(slug: str, source: Path) -> list[Path]:
    """Same-slug native folders found elsewhere (other agents' skill folders). Best-effort: never raises."""
    try:
        return [Path(c["path"]) for c in discovery.scan()["conflicts"]
                if c["slug"] == slug and Path(c["path"]) != source]
    except Exception:
        return []


def _move_back(moves: list[tuple[Path, Path]]) -> None:
    for origin, stored in reversed(moves):
        shutil.move(str(stored), str(origin))


def adopt(slug: str) -> dict:
    """Move the skill into the library. Other copies of the same skill in other agents' folders are moved too
    when they are byte-identical (so no agent keeps triggering it natively); a differing copy is left in place and
    reported. All-or-nothing: any failure puts every folder back."""
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
    digest = fingerprint(source)  # before any move: an unreadable file stops us with nothing moved
    identical, differing = [], []
    for other in _other_native_copies(slug, source):
        try:
            (identical if fingerprint(other) == digest else differing).append(other)
        except OSError:
            differing.append(other)
    if identical and _exists(_copies_dir(slug)):
        raise ValueError(f"{_copies_dir(slug)} already exists; move or remove it first")

    moves: list[tuple[Path, Path]] = []
    try:
        shutil.move(str(source), str(target))
        moves.append((source, target))
        for i, other in enumerate(identical, start=1):
            stored = _copies_dir(slug) / str(i)
            stored.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(other), str(stored))
            moves.append((other, stored))
        copies = [{"origin": str(o), "stored": str(s)} for o, s in moves[1:]]
        with store.connect() as conn:
            conn.execute("UPDATE skills SET status = 'adopted', path = ?, origin_path = ?, fingerprint = ?, "
                         "adopted_at = ?, updated_at = ?, copies = ? WHERE slug = ?",
                         (str(target), str(source), digest, store.now(), store.now(),
                          json.dumps(copies) if copies else None, slug))
    except Exception:
        _move_back(moves)  # keep adopt all-or-nothing
        raise
    return {"slug": slug, "from": str(source), "to": str(target), "moved_copies": [str(o) for o in identical],
            "other_copies": [str(o) for o in differing], "differing_copies": [str(o) for o in differing]}


def release(slug: str) -> dict:
    """Move the skill, and every copy adopted with it, back where they came from."""
    row = _row(slug)
    if row["status"] != "adopted" or not row["origin_path"]:
        raise ValueError(f"'{slug}' is not adopted")
    copies = json.loads(row["copies"] or "[]")
    moves = [(Path(row["origin_path"]), Path(row["path"]))]
    moves += [(Path(c["origin"]), Path(c["stored"])) for c in copies]
    for origin, _stored in moves:  # check every destination before moving anything
        if _exists(origin):
            raise ValueError(f"{origin} already exists; move or remove it first")
    done: list[tuple[Path, Path]] = []
    try:
        for origin, stored in moves:
            origin.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(stored), str(origin))
            done.append((origin, stored))
        with store.connect() as conn:
            conn.execute("UPDATE skills SET status = 'native', path = ?, origin_path = NULL, fingerprint = NULL, "
                         "adopted_at = NULL, copies = NULL, updated_at = ? WHERE slug = ?",
                         (str(moves[0][0]), store.now(), slug))
    except Exception:
        for origin, stored in reversed(done):
            shutil.move(str(origin), str(stored))
        raise
    if copies and _exists(_copies_dir(slug)):
        shutil.rmtree(_copies_dir(slug), ignore_errors=True)  # now-empty holding folder
    return {"slug": slug, "from": str(moves[0][1]), "to": str(moves[0][0]),
            "restored_copies": [c["origin"] for c in copies]}


def changed(slug: str) -> bool:
    """True when an adopted skill's files differ from what was adopted."""
    row = _row(slug)
    if row["status"] != "adopted" or not row["fingerprint"]:
        return False
    return fingerprint(Path(row["path"])) != row["fingerprint"]
