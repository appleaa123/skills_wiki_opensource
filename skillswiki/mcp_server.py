"""MCP server over stdio for agents without a shell (Claude Desktop, IDE chat panes).

A small FIXED tool set — never one tool per skill — so the agent's tool list stays the same size whether the
user has 5 skills or 500.
"""
from fastmcp import FastMCP

from skillswiki import learnings, loader, paths, store
from skillswiki.route import suggest

INSTRUCTIONS = (
    "Skills Wiki manages the AI skills this user installed. Before a task that may need a specialised, documented "
    "procedure (writing in a set style, a domain workflow, a file-processing routine), call suggest_skill with the "
    "user's request. If it returns a skill, load it with load_skill and follow it. If it returns a shortlist, pick "
    "one that clearly fits and load it, or proceed without a skill. load_skill returns the skill's folder on disk: "
    "read or run its files from there. If the user corrects how a skill is applied, or asks you to remember "
    "something specific to a skill, record it with learning_record (call learning_list first). If Learning Mode is "
    "off, tell the user they can turn it on with: skillswiki config set learning on"
)

mcp = FastMCP("skills-wiki", instructions=INSTRUCTIONS)


def _guard(fn, *args, **kwargs) -> dict:
    try:
        return fn(*args, **kwargs)
    except ValueError as exc:
        return {"error": str(exc)}


@mcp.tool
def suggest_skill(request: str) -> dict:
    """Find the user's installed skill that fits this request. Pass the user's request in their own words.

    Returns {"skill": slug or null, "shortlist": [...], "note": ...}. With a single skill, load it. With only a
    shortlist, pick one that clearly fits or proceed without a skill."""
    return _guard(suggest, request)


@mcp.tool
def load_skill(slug: str) -> dict:
    """Load an adopted skill: its full SKILL.md, the list of its files, the absolute folder path (read or run
    files from there; scripts run with the user's permissions), and the user's learnings for it."""
    return _guard(loader.load, slug)


@mcp.tool
def list_skills() -> dict:
    """List the user's adopted skills (slug, description, whether a routing card exists)."""
    with store.connect() as conn:
        rows = conn.execute("SELECT s.slug, s.description, c.slug IS NOT NULL AS has_card FROM skills s "
                            "LEFT JOIN cards c ON c.slug = s.slug WHERE s.status = 'adopted' ORDER BY s.slug")
        return {"skills": [{**dict(r), "has_card": bool(r["has_card"])} for r in rows]}


@mcp.tool
def learning_record(slug: str, body: str, supersedes: list[int] | None = None) -> dict:
    """Record a learning for one of the user's skills (Learning Mode).

    Call this when a skill's guidance led you astray or the user corrected how a skill should be applied for
    them. Also call it immediately when the user explicitly asks you to remember a correction specific to this
    skill for next time ("remember this", "from now on") — that is a request, not a mistake; record it before
    continuing. A general preference unrelated to this skill (e.g. how you format every response) is not a
    Skills Wiki learning — that belongs in your own persistent memory, if you have one. `body` is one paragraph
    of guidance for next time (600 characters max). Call learning_list first to avoid a near-duplicate, and pass
    the ids of earlier learnings this one replaces in `supersedes`.

    Args:
        slug: The skill's slug (as returned by suggest_skill / list_skills).
        body: The learning, written as an instruction for next time.
        supersedes: Optional ids (from learning_list or [#id] markers in a loaded skill) this one replaces.
    """
    if not learnings.enabled():
        return {"error": learnings.LEARNING_OFF_MESSAGE}
    problem = _guard(learnings.require_adopted, slug)
    if problem:
        return problem
    result = _guard(learnings.record, slug, body, supersedes)
    return result if isinstance(result, dict) else {"status": "ok", "id": result}


@mcp.tool
def learning_list(slug: str) -> dict:
    """List the user's live learnings for one skill — call before learning_record to avoid near-duplicates and
    to find ids to supersede. Returns {"learnings": [{id, slug, body, created_at}]} or {"error": str}."""
    if not learnings.enabled():
        return {"error": learnings.LEARNING_OFF_MESSAGE}
    rows = learnings.list_live(slug)
    return {"learnings": [{k: r[k] for k in ("id", "slug", "body", "created_at")} for r in rows]}


def run() -> None:
    paths.load_env()
    mcp.run(show_banner=False)
