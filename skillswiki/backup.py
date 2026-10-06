"""One zip per setup or uninstall run in ~/.skillswiki/backups: each skill folder before it moves and each agent
config file before it is edited. Never Skills Wiki's own data (.env, database)."""
import os
import zipfile
from datetime import datetime
from pathlib import Path

from skillswiki import paths
from skillswiki.errors import SkillsWikiError

BACKUPS_DIR = "backups"
LINK_SUFFIX = ".symlink"  # a linked skill is stored as a small text entry holding its target


def _dir() -> Path:
    return paths.home() / BACKUPS_DIR


def start(kind: str = "setup") -> Path:
    folder = _dir()
    folder.mkdir(parents=True, exist_ok=True)
    stem = f"{kind}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    path, n = folder / f"{stem}.zip", 1
    while path.exists():
        n += 1
        path = folder / f"{stem}-{n}.zip"
    with zipfile.ZipFile(path, "x"):
        pass
    return path


def _arcname(prefix: str, path: Path) -> str:
    path = Path(os.path.abspath(path))
    try:
        rel = path.relative_to(Path.home())
    except ValueError:
        rel = Path(*path.parts[1:])  # outside home: the absolute path without its root or drive
    return f"{prefix}/{rel.as_posix()}"


def add_skill(zip_path: Path, folder: Path) -> None:
    folder, base = Path(folder), _arcname("skills", folder)
    with zipfile.ZipFile(zip_path, "a", zipfile.ZIP_DEFLATED) as zf:
        if folder.is_symlink():
            zf.writestr(base + LINK_SUFFIX, os.readlink(folder))
            return
        for root, dirs, files in os.walk(folder, followlinks=False):
            for name in sorted(files) + [d for d in dirs if Path(root, d).is_symlink()]:
                full = Path(root, name)
                arc = f"{base}/{full.relative_to(folder).as_posix()}"
                if full.is_symlink():
                    zf.writestr(arc + LINK_SUFFIX, os.readlink(full))
                else:
                    zf.write(full, arc)


def add_file(zip_path: Path, path: Path) -> bool:
    path = Path(path)
    if Path(os.path.abspath(path)).is_relative_to(Path(os.path.abspath(paths.home()))):
        raise SkillsWikiError("INVALID_INPUT", "Skills Wiki's own data is never put in a setup backup", path=str(path))
    if not path.is_file():
        return False
    arc = _arcname("config", path)
    with zipfile.ZipFile(zip_path, "a", zipfile.ZIP_DEFLATED) as zf:
        if arc in zf.namelist():
            return False
        zf.write(path, arc)
    return True


def list_all() -> list[Path]:
    folder = _dir()
    return sorted(folder.glob("*.zip")) if folder.is_dir() else []


def delete_all() -> int:
    found = list_all()
    for path in found:
        path.unlink()
    return len(found)
