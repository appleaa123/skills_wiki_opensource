"""Read-only views over the local store for the management page. Pure functions; no HTTP here."""
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from itertools import combinations
from pathlib import Path

from skillswiki import cards, learnings, library, store

UNUSED_DAYS = 30
COST_WINDOW_DAYS = 30
OVERLAP_MIN_COUNT = 3


def _since(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def _changed(row: dict) -> bool:
    if row["status"] != "adopted" or not row["fingerprint"] or not Path(row["path"]).exists():
        return False
    return library.fingerprint(Path(row["path"])) != row["fingerprint"]


def skills_overview() -> list[dict]:
    since = _since(COST_WINDOW_DAYS)
    with store.connect() as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM skills ORDER BY status, slug")]
        carded = {r["slug"] for r in conn.execute("SELECT slug FROM cards")}
        last_used = {r["slug"]: r["ts"] for r in conn.execute(
            "SELECT slug, MAX(created_at) AS ts FROM usage WHERE event IN ('load','suggest') GROUP BY slug")}
        loads = {r["slug"]: r["n"] for r in conn.execute(
            "SELECT slug, COUNT(*) AS n FROM usage WHERE event = 'load' AND created_at >= ? GROUP BY slug", (since,))}
        eval_tokens = {r["slug"]: r["n"] or 0 for r in conn.execute(
            "SELECT slug, SUM(tokens_total) AS n FROM evals GROUP BY slug")}
        verdicts = {r["slug"]: r["verdict"] for r in conn.execute(
            "SELECT e.slug, e.verdict FROM evals e JOIN (SELECT slug, MAX(id) AS id FROM evals GROUP BY slug) m "
            "ON e.id = m.id")}
        live = {r["slug"]: r["n"] for r in conn.execute(
            "SELECT slug, COUNT(*) AS n FROM learnings WHERE superseded_by IS NULL AND retired_at IS NULL "
            "GROUP BY slug")}
    out = []
    for r in rows:
        slug = r["slug"]
        out.append({
            "slug": slug, "name": r["name"], "description": r["description"], "status": r["status"],
            "path": r["path"], "has_card": slug in carded, "has_scripts": bool(r["has_scripts"]),
            "changed": _changed(r), "last_used": last_used.get(slug), "learnings_live": live.get(slug, 0),
            "learnings_cap": learnings.LEARNING_MAX_LIVE_PER_SKILL, "latest_verdict": verdicts.get(slug),
            "context_tokens_est": r["context_tokens_est"], "loads_30d": loads.get(slug, 0),
            "context_cost_30d": r["context_tokens_est"] * loads.get(slug, 0),
            "eval_tokens_total": eval_tokens.get(slug, 0),
        })
    return out


def routing_log(limit: int = 100) -> list[dict]:
    with store.connect() as conn:
        rows = conn.execute("SELECT * FROM routing_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [{**dict(r), "shortlist": json.loads(r["shortlist"])} for r in rows]


def no_match(limit: int = 50) -> list[dict]:
    with store.connect() as conn:
        rows = conn.execute("SELECT request_excerpt, mode, reason, created_at FROM routing_log WHERE suggested IS NULL "
                            "AND shortlist = '[]' ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def unused(days: int = UNUSED_DAYS) -> list[dict]:
    """Adopted for at least `days` and not loaded or suggested in that time."""
    since = _since(days)
    with store.connect() as conn:
        rows = conn.execute(
            "SELECT s.slug, s.adopted_at FROM skills s WHERE s.status = 'adopted' AND s.adopted_at < ? AND NOT EXISTS "
            "(SELECT 1 FROM usage u WHERE u.slug = s.slug AND u.event IN ('load','suggest') AND u.created_at >= ?) "
            "ORDER BY s.slug", (since, since)).fetchall()
    return [dict(r) for r in rows]


def overlaps(min_count: int = OVERLAP_MIN_COUNT) -> list[dict]:
    """Pairs of skills that keep appearing in the same shortlist: they compete for the same requests."""
    with store.connect() as conn:
        lists = [json.loads(r["shortlist"]) for r in conn.execute("SELECT shortlist FROM routing_log")]
    pairs = Counter()
    for shortlist in lists:
        slugs = sorted({item["skill"] for item in shortlist})
        pairs.update(combinations(slugs, 2))
    return [{"a": a, "b": b, "count": n} for (a, b), n in pairs.most_common() if n >= min_count]


def skill_learnings(slug: str) -> dict:
    return {"live": learnings.list_live(slug), "all": learnings.list_all(slug),
            "cap": learnings.LEARNING_MAX_LIVE_PER_SKILL, "enabled": learnings.enabled()}


def skill_evals(slug: str) -> list[dict]:
    from skillswiki.evals import ratchet_local
    return ratchet_local.report(slug)


def skill_card(slug: str) -> dict | None:
    return cards.get(slug)
