"""Local usage log: which skills were loaded, suggested or evaluated, with a token estimate."""
from skillswiki import store

EVENTS = frozenset({"load", "suggest", "eval"})


def log(slug: str, event: str, tokens_est: int = 0) -> None:
    if event not in EVENTS:
        raise ValueError(f"unknown usage event {event!r}")
    with store.connect() as conn:
        conn.execute("INSERT INTO usage (slug, event, tokens_est, created_at) VALUES (?, ?, ?, ?)",
                     (slug, event, int(tokens_est), store.now()))


def last_used(slug: str) -> str | None:
    """Most recent load or suggest of this skill (evals are not 'use')."""
    with store.connect() as conn:
        row = conn.execute("SELECT MAX(created_at) AS ts FROM usage WHERE slug = ? AND event IN ('load','suggest')",
                           (slug,)).fetchone()
    return row["ts"]


def counts(since_iso: str) -> dict[str, dict[str, int]]:
    """{slug: {event: count}} for events at or after since_iso."""
    with store.connect() as conn:
        rows = conn.execute("SELECT slug, event, COUNT(*) AS n FROM usage WHERE created_at >= ? "
                            "GROUP BY slug, event", (since_iso,)).fetchall()
    result: dict[str, dict[str, int]] = {}
    for r in rows:
        result.setdefault(r["slug"], {})[r["event"]] = r["n"]
    return result
