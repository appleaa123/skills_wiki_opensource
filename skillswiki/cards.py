"""Routing cards: optional extra routing text per skill (example requests, keywords, what it is NOT for).

Stored only in Skills Wiki's database, never in the user's skill files. Written by hand (`set_manual`) or by the
user's own AI CLI on request (`enrich`, which spends the user's tokens). Nothing generates a card automatically.
"""
import json

from skillswiki import frontmatter, store
from skillswiki.evals.backends import get_backend
from skillswiki.textjson import extract_object

FIELDS = ("examples", "keywords", "not_for")
BACKENDS = ("claude", "codex", "gemini")
MAX_ITEMS = 15
MAX_ITEM_CHARS = 200
SKILL_TEXT_MAX_CHARS = 12000
ENRICH_TIMEOUT_S = 180

ENRICH_PROMPT = """You are writing a routing card for an AI skill. Read the skill below and return ONLY a JSON object:
{{"examples": [5-10 requests a real user would type when this skill is the right one, varied wording, no skill
jargon], "keywords": [5-15 words or short phrases users would use], "not_for": [2-5 nearby requests this skill
should NOT handle]}}.
SKILL:
{skill}"""
RETRY_SUFFIX = "\n\nReturn only the JSON object."


def _skill_row(slug: str) -> dict:
    with store.connect() as conn:
        row = conn.execute("SELECT slug, path FROM skills WHERE slug = ?", (slug,)).fetchone()
    if row is None:
        raise ValueError(f"skill '{slug}' not found — run: skillswiki scan")
    return dict(row)


def _validate(card: dict) -> dict:
    clean = {}
    for field in FIELDS:
        items = card.get(field, [])
        if not isinstance(items, list) or not all(isinstance(i, str) for i in items):
            raise ValueError(f"{field} must be a list of strings")
        if len(items) > MAX_ITEMS:
            raise ValueError(f"{field} has {len(items)} items; max {MAX_ITEMS}")
        if any(len(i) > MAX_ITEM_CHARS for i in items):
            raise ValueError(f"{field} items must be at most {MAX_ITEM_CHARS} characters")
        clean[field] = [i.strip() for i in items if i.strip()]
    return clean


def _save(slug: str, card: dict, source: str) -> dict:
    _skill_row(slug)
    clean = _validate(card)
    with store.connect() as conn:
        conn.execute(
            "INSERT INTO cards (slug, examples, keywords, not_for, source, updated_at) VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(slug) DO UPDATE SET examples = excluded.examples, keywords = excluded.keywords, "
            "not_for = excluded.not_for, source = excluded.source, updated_at = excluded.updated_at",
            (slug, *(json.dumps(clean[f]) for f in FIELDS), source, store.now()))
    return get(slug)


def get(slug: str) -> dict | None:
    with store.connect() as conn:
        row = conn.execute("SELECT * FROM cards WHERE slug = ?", (slug,)).fetchone()
    if row is None:
        return None
    return {"slug": slug, **{f: json.loads(row[f]) for f in FIELDS}, "source": row["source"],
            "updated_at": row["updated_at"]}


def set_manual(slug: str, examples: list[str], keywords: list[str], not_for: list[str]) -> dict:
    return _save(slug, {"examples": examples, "keywords": keywords, "not_for": not_for}, "manual")


def delete(slug: str) -> None:
    with store.connect() as conn:
        conn.execute("DELETE FROM cards WHERE slug = ?", (slug,))


def enrich(slug: str, backend: str = "claude") -> dict:
    """Ask the user's AI CLI to draft a card from SKILL.md (user's tokens). Raises BackendUnavailable if the
    CLI fails, ValueError if it twice returns something that is not a valid card."""
    from pathlib import Path

    if backend not in BACKENDS:
        raise ValueError(f"backend must be one of {', '.join(BACKENDS)}")
    row = _skill_row(slug)
    skill_text = (Path(row["path"]) / frontmatter.SKILL_FILE).read_text(encoding="utf-8", errors="replace")
    prompt = ENRICH_PROMPT.format(skill=skill_text[:SKILL_TEXT_MAX_CHARS])
    cli = get_backend(backend)
    reply = ""
    for attempt_prompt in (prompt, prompt + RETRY_SUFFIX):
        reply = cli.judge(attempt_prompt, None, timeout=ENRICH_TIMEOUT_S)["text"]
        data = extract_object(reply)
        if data is not None:
            try:
                return _save(slug, data, "enrich")
            except ValueError:
                continue
    raise ValueError(f"{backend} did not return a valid routing card; reply began: {reply[:200]!r}")
