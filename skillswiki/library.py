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

from skillswiki import backup, discovery, paths, store
from skillswiki.errors import SkillsWikiError

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
        raise SkillsWikiError("NOT_FOUND", f"skill '{slug}' not found — run: skillswiki scan", slug=slug)
    return dict(row)


def _is_link(path: Path) -> bool:
    """A symlink, or a Windows directory junction (Python 3.12+ can tell; older versions see a plain folder)."""
    return path.is_symlink() or bool(getattr(path, "is_junction", lambda: False)())


def _exists(path: Path) -> bool:
    return path.exists() or _is_link(path)


FOREIGN_LINK_CODE = "FOREIGN_LINK"
FOREIGN_LINK_MESSAGE = ("{path} is a link into {target}, placed by another tool (for example skills-manager). "
                        "That tool may put it back after you adopt; undeploy it there first, or adopt anyway.")


def _under(path: Path, roots: list[Path]) -> bool:
    for root in roots:
        try:
            path.relative_to(root.resolve())
            return True
        except ValueError:
            continue
    return False


def foreign_link_warnings(folders: list[Path]) -> list[dict]:
    """A warning for each folder that is a link whose target is not under any scan root or the library:
    another tool deployed it there and may deploy it again."""
    known = [*paths.scan_roots(), paths.library_dir()]
    warnings = []
    for folder in folders:
        if not _is_link(folder):
            continue
        target = folder.resolve()
        if not _under(target, known):
            warnings.append({"code": FOREIGN_LINK_CODE, "path": str(folder), "target": str(target),
                             "message": FOREIGN_LINK_MESSAGE.format(path=folder, target=target)})
    return warnings


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


def plan_adopt(slug: str) -> dict:
    """Everything adopt() checks and decides, without touching a file or a row. Raises exactly what adopt()
    raises, so a dry run fails the same way the real move would."""
    row = _row(slug)
    if row["status"] == "plugin":
        raise SkillsWikiError("PLUGIN_MANAGED", f"'{slug}' is plugin-managed; disable it in your agent's plugin "
                              "settings instead", slug=slug)
    if row["status"] == "adopted":
        raise SkillsWikiError("ALREADY_ADOPTED", f"'{slug}' is already adopted", slug=slug)
    if slug == paths.SHIPPED_SKILL_SLUG:
        raise SkillsWikiError("RESERVED", f"'{slug}' is the Skills Wiki skill itself; adopting it would hide it "
                              "from your agent", slug=slug)
    source = Path(row["path"])
    target = paths.library_dir() / slug
    if not _exists(source):
        raise SkillsWikiError("SOURCE_MISSING", f"'{slug}' is no longer at {source} — run: skillswiki scan",
                              path=str(source))
    if _exists(target):
        raise SkillsWikiError("TARGET_EXISTS", f"{target} already exists; move or remove it first", paths=[str(target)])
    if _is_link(source) and not os.path.isabs(os.readlink(source)):
        raise SkillsWikiError("RELATIVE_SYMLINK", f"{source} is a relative symlink; moving it would break it. "
                              f"Make the link absolute, or adopt the folder it points to ({source.resolve()})",
                              path=str(source), target=os.readlink(source))
    digest = fingerprint(source)  # an unreadable file stops us here, with nothing moved
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
        raise SkillsWikiError("TARGET_EXISTS", f"{_copies_dir(slug)} already exists; move or remove it first",
                              paths=[str(_copies_dir(slug))])
    return {"slug": slug, "from": str(source), "to": str(target), "fingerprint": digest,
            "moved_copies": [str(o) for o in identical], "other_copies": [str(o) for o in differing + linked],
            "differing_copies": [str(o) for o in differing], "linked_copies": [str(o) for o in linked],
            "warnings": foreign_link_warnings([source, *identical])}


def adopt(slug: str) -> dict:
    """Move the skill into the library, following plan_adopt(). Identical copies in other agents' folders move
    too (so no agent keeps triggering it natively); a differing copy is left in place and reported.
    All-or-nothing: any failure puts every folder back."""
    plan = plan_adopt(slug)
    source, target = Path(plan["from"]), Path(plan["to"])
    moves: list[tuple[Path, Path]] = []
    try:
        shutil.move(str(source), str(target))
        moves.append((source, target))
        for i, other in enumerate(plan["moved_copies"], start=1):
            stored = _copies_dir(slug) / str(i)
            stored.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(other, str(stored))
            moves.append((Path(other), stored))
        copies = [{"origin": str(o), "stored": str(s)} for o, s in moves[1:]]
        with store.connect() as conn:
            conn.execute("UPDATE skills SET status = 'adopted', path = ?, origin_path = ?, fingerprint = ?, "
                         "adopted_at = ?, updated_at = ?, copies = ? WHERE slug = ?",
                         (str(target), str(source), plan["fingerprint"], store.now(), store.now(),
                          json.dumps(copies) if copies else None, slug))
    except Exception:
        _move_back(moves)  # keep adopt all-or-nothing
        _remove_empty_holding_dir(slug)
        raise
    _refresh_manifest()
    return plan


def plan_release(slug: str) -> dict:
    """Everything release() checks and decides, without touching a file or a row."""
    row = _row(slug)
    if row["status"] != "adopted" or not row["origin_path"]:
        raise SkillsWikiError("NOT_ADOPTED", f"'{slug}' is not adopted", slug=slug)
    if not _exists(Path(row["path"])):
        raise SkillsWikiError("SOURCE_MISSING", f"'{slug}' is no longer in the library ({row['path']}); nothing to "
                              "release — run: skillswiki scan", path=row["path"])
    copies = json.loads(row["copies"] or "[]")
    missing = [c["origin"] for c in copies if not _exists(Path(c["stored"]))]
    moves = [(Path(row["origin_path"]), Path(row["path"]))]
    moves += [(Path(c["origin"]), Path(c["stored"])) for c in copies if c["origin"] not in missing]
    for origin, _stored in moves:  # check every destination before moving anything
        if _exists(origin):
            raise SkillsWikiError("TARGET_EXISTS", f"{origin} already exists; move or remove it first",
                                  paths=[str(origin)])
    return {"slug": slug, "from": str(moves[0][1]), "to": str(moves[0][0]),
            "restored_copies": [str(o) for o, _s in moves[1:]], "missing_copies": missing,
            "moves": [[str(o), str(s)] for o, s in moves]}


def release(slug: str) -> dict:
    """Move the skill, and every copy adopted with it, back where they came from (see plan_release)."""
    plan = plan_release(slug)
    moves = [(Path(o), Path(s)) for o, s in plan["moves"]]
    done: list[tuple[Path, Path]] = []
    try:
        for origin, stored in moves:
            origin.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(stored), str(origin))
            done.append((origin, stored))
        with store.connect() as conn:
            conn.execute("UPDATE skills SET status = 'native', path = ?, origin_path = NULL, fingerprint = NULL, "
                         "adopted_at = NULL, copies = NULL, updated_at = ? WHERE slug = ?",
                         (plan["to"], store.now(), slug))
    except Exception:
        for origin, stored in reversed(done):
            shutil.move(str(origin), str(stored))
        raise
    if plan["restored_copies"] or plan["missing_copies"]:
        _remove_empty_holding_dir(slug)
    _refresh_manifest()
    return plan


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


def release_all(dry_run: bool = False) -> dict:
    """Release every adopted skill (or, with dry_run, only plan it). Keeps going past a failure and reports it."""
    with store.connect() as conn:
        slugs = [r["slug"] for r in conn.execute("SELECT slug FROM skills WHERE status = 'adopted' ORDER BY slug")]
    act = plan_release if dry_run else release
    released, failed = [], []
    for slug in slugs:
        try:
            released.append(act(slug))
        except (ValueError, OSError) as exc:
            failed.append({"slug": slug, "error": str(exc)})
    return {"released": released, "failed": failed}


def changed(slug: str) -> bool:
    """True when an adopted skill's files differ from what was adopted."""
    row = _row(slug)
    if row["status"] != "adopted" or not row["fingerprint"]:
        return False
    return fingerprint(Path(row["path"])) != row["fingerprint"]


def _back_up(zip_path: Path, plan: dict) -> None:
    for folder in [plan["from"], *plan["moved_copies"]]:
        backup.add_skill(zip_path, Path(folder))


def adopt_all(dry_run: bool = False, zip_path: Path | None = None) -> dict:
    """Adopt every native skill (or, with dry_run, only plan it). Keeps going past a failure and reports it.
    With zip_path, each folder is added to that backup before it moves."""
    with store.connect() as conn:
        slugs = [r["slug"] for r in conn.execute("SELECT slug FROM skills WHERE status = 'native' ORDER BY slug")]
    adopted, failed = [], []
    for slug in slugs:
        if slug == paths.SHIPPED_SKILL_SLUG:
            continue
        try:
            plan = plan_adopt(slug)
            if not dry_run:
                if zip_path:
                    _back_up(zip_path, plan)
                plan = adopt(slug)
            adopted.append(plan)
        except (ValueError, OSError) as exc:
            failed.append({"slug": slug, "code": getattr(exc, "code", "INVALID_INPUT"), "error": str(exc)})
    return {"adopted": adopted, "failed": failed}
