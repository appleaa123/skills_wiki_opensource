"""Where Skills Wiki keeps its data and where it looks for installed skills.

Everything is read from the environment on every call (never cached at import), so tests and users can
redirect it with SKILLSWIKI_HOME / SKILLSWIKI_SCAN_ROOTS.
"""
import os
from pathlib import Path

HOME_ENV = "SKILLSWIKI_HOME"
SCAN_ROOTS_ENV = "SKILLSWIKI_SCAN_ROOTS"
DEFAULT_HOME_NAME = ".skillswiki"
DB_NAME = "skillswiki.db"
ENV_FILE_NAME = ".env"

# Skill folders agents read natively (Claude Code; Codex, legacy and current; Gemini CLI). User-level first,
# then the same names under the current working directory (project-level).
AGENT_SKILL_DIRS = (".claude/skills", ".codex/skills", ".agents/skills", ".gemini/skills")


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


def scan_roots() -> list[Path]:
    """Native skill roots, user-level then project-level then SKILLSWIKI_SCAN_ROOTS extras. Missing
    directories are kept (callers skip them); duplicates (by resolved path) are dropped."""
    candidates = [Path.home() / d for d in AGENT_SKILL_DIRS]
    candidates += [Path.cwd() / d for d in AGENT_SKILL_DIRS]
    extra = os.getenv(SCAN_ROOTS_ENV, "")
    candidates += [Path(p).expanduser().absolute() for p in extra.split(":") if p.strip()]
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
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = (part.strip() for part in line.split("=", 1))
        os.environ.setdefault(key, value.strip("'\""))
