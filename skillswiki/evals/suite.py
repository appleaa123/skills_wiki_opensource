"""Eval suites on disk: ~/.skillswiki/suites/<slug>/{tasks.jsonl, rubric.json, verify.py?, suite.json}.

One adopted skill = one suite. Every task's "skill" field must equal the slug. suite.json carries the review
status: "draft" (generated, not yet checked) or "checked".
"""
import importlib.util
import json
from pathlib import Path

from skillswiki import frontmatter, paths, store

STATUSES = ("draft", "checked")
_SAFE_SLUG_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.")


def suite_dir(slug: str) -> Path:
    if not slug or not set(slug) <= _SAFE_SLUG_CHARS or slug in (".", ".."):
        raise ValueError(f"invalid skill slug {slug!r}")
    return paths.suites_dir() / slug


def load_tasks(slug: str) -> list[dict]:
    path = suite_dir(slug) / "tasks.jsonl"
    if not path.exists():
        raise FileNotFoundError(f"no tasks.jsonl for {slug!r} (looked at {path}) — run: skillswiki eval generate {slug}")
    tasks = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    if not tasks:
        raise ValueError(f"tasks.jsonl for {slug!r} is empty")
    return tasks


def load_rubric(slug: str) -> dict:
    path = suite_dir(slug) / "rubric.json"
    if not path.exists():
        raise FileNotFoundError(f"no rubric.json for {slug!r} (looked at {path})")
    return json.loads(path.read_text())


def load_verifier(slug: str):
    """verify(task, output) from the suite's verify.py, or None when the suite has none."""
    path = suite_dir(slug) / "verify.py"
    if not path.exists():
        return None
    spec = importlib.util.spec_from_file_location(f"_verify_{slug.replace('-', '_')}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.verify


def skill_body(slug: str) -> str:
    """The adopted skill's SKILL.md text."""
    with store.connect() as conn:
        row = conn.execute("SELECT status, path FROM skills WHERE slug = ?", (slug,)).fetchone()
    if row is None or row["status"] != "adopted":
        raise RuntimeError(f"skill {slug} is not adopted — run: skillswiki adopt {slug}")
    return (Path(row["path"]) / frontmatter.SKILL_FILE).read_text(encoding="utf-8", errors="replace")


def status(slug: str) -> dict:
    path = suite_dir(slug) / "suite.json"
    return json.loads(path.read_text()) if path.exists() else {}


def set_status(slug: str, value: str, **extra) -> dict:
    if value not in STATUSES:
        raise ValueError(f"suite status must be one of {STATUSES}")
    data = {**status(slug), **extra, "status": value, "updated_at": store.now()}
    folder = suite_dir(slug)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "suite.json").write_text(json.dumps(data, indent=2))
    return data


def write(slug: str, tasks: list[dict], rubric: dict) -> Path:
    folder = suite_dir(slug)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "tasks.jsonl").write_text("".join(json.dumps(t) + "\n" for t in tasks))
    (folder / "rubric.json").write_text(json.dumps(rubric, indent=2))
    return folder


def exists(slug: str) -> bool:
    return (suite_dir(slug) / "tasks.jsonl").exists()
