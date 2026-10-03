"""Learning Mode: the user's own corrections for a skill, appended to that skill every time it is loaded.

Ported from Skills Wiki's hosted product (per-pack there, per-skill here). On by default locally because the
data never leaves the machine; `skillswiki config set learning off` turns it off.
"""
from skillswiki import store

LEARNING_MAX_LIVE_PER_SKILL = 8
LEARNING_MAX_BODY_CHARS = 600
LEARNINGS_BLOCK_MAX_CHARS = 6000
LEARNING_SETTING = "learning"
LEARNING_DEFAULT = "on"
_LIST_LIMIT = 200

# Scoped to this skill: a general preference unrelated to the skill belongs in the agent's own memory.
LEARNING_REMINDER = (
    "Learning Mode is on: if the user corrects how this skill is applied, "
    "or asks you to remember something specific to this skill for next "
    "time, record it with learning_record (call learning_list first) — a "
    "general preference unrelated to this skill belongs in your own "
    "persistent memory instead, if you have one."
)
LEARNING_OFF_MESSAGE = "Learning Mode is off — run: skillswiki config set learning on"


def enabled() -> bool:
    return store.get_setting(LEARNING_SETTING, LEARNING_DEFAULT) == "on"


def build_learnings_block(slug: str, rows: list[dict]) -> str:
    """rows = the live set, newest first. Each row is included whole or dropped — never cut mid-line —
    stopping once the block would exceed LEARNINGS_BLOCK_MAX_CHARS. "" when rows is empty."""
    if not rows:
        return ""
    body = (
        f"This user's recorded learnings for {slug} (Learning Mode). "
        "Apply them; they are the user's own past corrections."
    )
    for row in rows:
        line = f"- [#{row['id']} · {row.get('slug') or slug}] {row['body']}"
        candidate = f"{body}\n{line}"
        if len(candidate) > LEARNINGS_BLOCK_MAX_CHARS:
            break
        body = candidate
    return f"<learnings>\n{body}\n</learnings>"


def _rows(slug: str, live_only: bool) -> list[dict]:
    sql = "SELECT * FROM learnings WHERE slug = ?"
    if live_only:
        sql += " AND superseded_by IS NULL AND retired_at IS NULL"
    with store.connect() as conn:
        rows = conn.execute(sql + " ORDER BY created_at DESC, id DESC LIMIT ?", (slug, _LIST_LIMIT)).fetchall()
    return [dict(r) for r in rows]


def list_live(slug: str) -> list[dict]:
    return _rows(slug, live_only=True)


def list_all(slug: str) -> list[dict]:
    return _rows(slug, live_only=False)


def record(slug: str, body: str, supersedes: list[int] | None = None) -> int:
    """Insert one learning; each id in `supersedes` (this skill's own live learnings) points at the new row.

    Raises ValueError: Learning Mode off, empty/oversized body, or the skill is at the live cap and
    `supersedes` does not free room."""
    if not enabled():
        raise ValueError(LEARNING_OFF_MESSAGE)
    body = (body or "").strip()
    if not body:
        raise ValueError("body is empty")
    if len(body) > LEARNING_MAX_BODY_CHARS:
        raise ValueError(f"body is {len(body)} chars; max {LEARNING_MAX_BODY_CHARS}")
    supersedes = [int(i) for i in (supersedes or [])]
    live = [r for r in list_live(slug) if r["id"] not in supersedes]
    if len(live) >= LEARNING_MAX_LIVE_PER_SKILL:
        raise ValueError(
            f"{slug} already has {LEARNING_MAX_LIVE_PER_SKILL} live learnings — supersede or retire one first")
    with store.connect() as conn:
        new_id = conn.execute("INSERT INTO learnings (slug, body, created_at) VALUES (?, ?, ?)",
                              (slug, body, store.now())).lastrowid
        if supersedes:
            marks = ",".join("?" * len(supersedes))
            conn.execute(f"UPDATE learnings SET superseded_by = ? WHERE id IN ({marks}) AND slug = ? "
                         "AND superseded_by IS NULL", (new_id, *supersedes, slug))
    return int(new_id)


def _get(learning_id: int) -> dict:
    with store.connect() as conn:
        row = conn.execute("SELECT * FROM learnings WHERE id = ?", (learning_id,)).fetchone()
    if row is None:
        raise ValueError(f"learning #{learning_id} not found")
    return dict(row)


def edit(learning_id: int, body: str) -> int:
    """Edit = supersede with a new row, so the history is kept."""
    return record(_get(learning_id)["slug"], body, supersedes=[learning_id])


def retire(learning_id: int) -> None:
    _get(learning_id)
    with store.connect() as conn:
        conn.execute("UPDATE learnings SET retired_at = ? WHERE id = ? AND retired_at IS NULL",
                     (store.now(), learning_id))


def block_for(slug: str) -> str:
    """The <learnings> block plus the reminder, or "" when Learning Mode is off. Never raises: it rides
    inside every skill load, so a store problem degrades to no block rather than a failed load."""
    try:
        if not enabled():
            return ""
        block = build_learnings_block(slug, list_live(slug))
        return "\n\n".join(part for part in (block, LEARNING_REMINDER) if part)
    except Exception:
        return ""
