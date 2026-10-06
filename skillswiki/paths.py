"""Where Skills Wiki keeps its data and where it looks for installed skills.

Everything is read from the environment on every call (never cached at import), so tests and users can
redirect it with SKILLSWIKI_HOME / SKILLSWIKI_SCAN_ROOTS.
"""
import os
from pathlib import Path

from skillswiki import agents

HOME_ENV = "SKILLSWIKI_HOME"
SCAN_ROOTS_ENV = "SKILLSWIKI_SCAN_ROOTS"
DEFAULT_HOME_NAME = ".skillswiki"
DB_NAME = "skillswiki.db"
ENV_FILE_NAME = ".env"

def home() -> Path:
    raw = os.getenv(HOME_ENV, "").strip()
    path = Path(raw).expanduser() if raw else Path.home() / DEFAULT_HOME_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def _subdir(name: str) -> Path:
    path = home() / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def library_dir() -> Path:
    return _subdir("library")


def suites_dir() -> Path:
    return _subdir("suites")


def results_dir() -> Path:
    return _subdir("results")


def calibration_dir() -> Path:
    return _subdir("calibration")


def db_path() -> Path:
    return home() / DB_NAME


def package_dir() -> Path:
    return Path(__file__).resolve().parent


SHIPPED_SKILL_SLUG = "skills-wiki"


def shipped_skill_dir() -> Path:
    """The skill that teaches an agent to ask Skills Wiki; shipped inside the package, installed by `setup`."""
    return package_dir() / "assets" / "skills" / SHIPPED_SKILL_SLUG


def scan_roots() -> list[Path]:
    """Native skill roots: every registry agent's user-level folders (agents.AGENTS, in table order), then the
    project-level folders (agents.PROJECT_SKILL_DIRS) under the working directory, then SKILLSWIKI_SCAN_ROOTS extras
    (separated by ':' on macOS/Linux, ';' on Windows — os.pathsep). Missing directories are kept (callers skip
    them); duplicates (by resolved path) are dropped."""
    candidates = [Path.home() / d for d in agents.user_skill_dirs()]
    candidates += [Path.cwd() / d for d in agents.PROJECT_SKILL_DIRS]
    extra = os.getenv(SCAN_ROOTS_ENV, "")
    candidates += [Path(p).expanduser().absolute() for p in extra.split(os.pathsep) if p.strip()]
    roots, seen = [], set()
    for path in candidates:
        key = path.resolve()
        if key not in seen:
            seen.add(key)
            roots.append(path)
    return roots


def load_env() -> None:
    """Read KEY=VALUE lines from <home>/.env into os.environ, never overriding a variable already set.
    Stdlib only (no python-dotenv)."""
    env_file = home() / ENV_FILE_NAME
    if not env_file.is_file():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = (part.strip() for part in line.split("=", 1))
        os.environ.setdefault(key, value.strip("'\""))
