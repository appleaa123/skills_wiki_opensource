"""Adopt / release: move a skill folder between an agent's native skills directory and the Skills Wiki
library. Adopting stops the agent's own trigger from firing; Skills Wiki then routes and loads the skill.

Safety: every move is reversible, never overwrites an existing path, and touches only the library and the
recorded origins. Identical copies of the skill in other agents' folders move with it and come back with it. A symlinked skill is moved as a link (its target is never touched).
"""
import hashlib
import json
import os
import shutil
from pathlib import Path

from skillswiki import discovery, paths, store

FINGERPRINT_CHARS = 16
COPIES_DIR = ".copies"  # library/.copies/<slug>/<n>: identical copies adopted along with a skill
MANIFEST_NAME = "RESTORE.json"  # library/RESTORE.json: where every adopted folder came from, readable without us
RESTORE_HELP = ("Skills Wiki moved these skill folders here when you adopted them. To put everything back, run: "
                "skillswiki release --all. Without Skills Wiki installed, move each folder back by hand: "
                "'library_path' goes to 'origin', and each copy's 'stored' path goes to its 'origin'.")
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


def _is_link(path: Path) -> bool:
    """A symlink, or a Windows directory junction (Python 3.12+ can tell; older versions see a plain folder)."""
    return path.is_symlink() or bool(getattr(path, "is_junction", lambda: False)())


def _exists(path: Path) -> bool:
    return path.exists() or _is_link(path)


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


def _remove_empty_holding_dir(slug: str) -> None:
    """Remove library/.copies/<slug> (and .copies) only if empty: never deletes anything a user put there."""
    for folder in (_copies_dir(slug), _copies_dir(slug).parent):
        try:
            os.rmdir(folder)
        except OSError:
            return


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
    if _is_link(source) and not os.path.isabs(os.readlink(source)):
        raise ValueError(f"{source} is a relative symlink; moving it would break it. Make the link absolute, or "
                         f"adopt the folder it points to ({source.resolve()})")
    digest = fingerprint(source)  # before any move: an unreadable file stops us with nothing moved
    identical, differing, linked = [], [], []
    others = _other_native_copies(slug, source)
    # A real folder that a symlinked copy (or the skill itself) points at stays put: moving it would leave the link
    # dangling while adopted. Moving the links themselves is safe.
    link_targets = {p.resolve() for p in [source, *others] if _is_link(p)}
    for other in others:
        if not _is_link(other) and other.resolve() in link_targets:
            linked.append(other)
            continue
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
        _remove_empty_holding_dir(slug)
        raise
    _refresh_manifest()
    return {"slug": slug, "from": str(source), "to": str(target), "moved_copies": [str(o) for o in identical],
            "other_copies": [str(o) for o in differing + linked], "differing_copies": [str(o) for o in differing],
            "linked_copies": [str(o) for o in linked]}


def release(slug: str) -> dict:
    """Move the skill, and every copy adopted with it, back where they came from."""
    row = _row(slug)
    if row["status"] != "adopted" or not row["origin_path"]:
        raise ValueError(f"'{slug}' is not adopted")
    if not _exists(Path(row["path"])):
        raise ValueError(f"'{slug}' is no longer in the library ({row['path']}); nothing to release — run: "
                         "skillswiki scan")
    copies = json.loads(row["copies"] or "[]")
    missing = [c["origin"] for c in copies if not _exists(Path(c["stored"]))]
    moves = [(Path(row["origin_path"]), Path(row["path"]))]
    moves += [(Path(c["origin"]), Path(c["stored"])) for c in copies if c["origin"] not in missing]
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
    if copies:
        _remove_empty_holding_dir(slug)
    _refresh_manifest()
    return {"slug": slug, "from": str(moves[0][1]), "to": str(moves[0][0]),
            "restored_copies": [str(o) for o, _s in moves[1:]], "missing_copies": missing}


def write_manifest() -> Path:
    """Rewrite library/RESTORE.json from the store: a plain record of every adopted folder's origin, so skills
    can be put back even after an uninstall or a lost database."""
    with store.connect() as conn:
        rows = conn.execute("SELECT slug, path, origin_path, copies FROM skills WHERE status = 'adopted' "
                            "ORDER BY slug").fetchall()
    manifest = {"how_to_restore_by_hand": RESTORE_HELP, "updated_at": store.now(),
                "skills": {r["slug"]: {"library_path": r["path"], "origin": r["origin_path"],
                                       "copies": json.loads(r["copies"] or "[]")} for r in rows}}
    path = paths.library_dir() / MANIFEST_NAME
    path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return path


def _refresh_manifest() -> None:
    try:
        write_manifest()
    except Exception:
        pass  # the store stays the source of truth; a failed manifest write must not undo a finished move


def release_all() -> dict:
    """Release every adopted skill. Keeps going past a failure and reports it."""
    with store.connect() as conn:
        slugs = [r["slug"] for r in conn.execute("SELECT slug FROM skills WHERE status = 'adopted' ORDER BY slug")]
    released, failed = [], []
    for slug in slugs:
        try:
            released.append(release(slug))
        except (ValueError, OSError) as exc:
            failed.append({"slug": slug, "error": str(exc)})
    return {"released": released, "failed": failed}


def changed(slug: str) -> bool:
    """True when an adopted skill's files differ from what was adopted."""
    row = _row(slug)
    if row["status"] != "adopted" or not row["fingerprint"]:
        return False
    return fingerprint(Path(row["path"])) != row["fingerprint"]
