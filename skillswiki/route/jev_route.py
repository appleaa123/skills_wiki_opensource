"""Routing with JEV (the user's own TypeSafe key). A keyword prefilter keeps the JEV Choice small (far under its
255-option limit, and cheap); the ported router asks three gate questions, picks among the candidates and checks
the top ones' fit. A single skill is suggested only at fit >= SUGGEST_BAR; otherwise the shortlist goes back to the
agent. Any JEV failure falls back to keyword routing.
"""
import json
from pathlib import Path

from skillswiki import frontmatter, store
from skillswiki.decision import get_decision_backend
from skillswiki.decision.router import DEFAULTS, route
from skillswiki.route import keyword

# Measured on Skills Wiki's own catalog and frozen task set (2026-10-01): at fit >= 0.85 JEV's pick was right
# 98.5% of the time and it suggested on ~65% of requests. A user's library differs; treat it as a strong guide.
SUGGEST_BAR = 0.85
PREFILTER_K = 40
CARD_EXAMPLES_IN_LINE = 3
DESCRIBE_CHARS = DEFAULTS["detail_chars"]

NOTE_SUGGESTED = "JEV picked this skill with high confidence. Load it with load_skill and follow it."
NOTE_BELOW_BAR = ("No confident pick. If shortlist[0] fits the request, offer it to the user or load it; otherwise "
                  "proceed without a skill.")
NOTE_NONE = "JEV found no adopted skill that this request needs. Proceed without a skill."


def _adopted() -> dict[str, dict]:
    with store.connect() as conn:
        rows = conn.execute("SELECT s.slug, s.name, s.description, s.path, c.examples FROM skills s "
                            "LEFT JOIN cards c ON c.slug = s.slug WHERE s.status = 'adopted'").fetchall()
    return {r["slug"]: dict(r) for r in rows}


def _recently_used(slugs: list[str], k: int) -> list[str]:
    with store.connect() as conn:
        rows = conn.execute("SELECT slug, MAX(created_at) AS ts FROM usage GROUP BY slug").fetchall()
    last = {r["slug"]: r["ts"] for r in rows}
    return sorted(slugs, key=lambda s: last.get(s) or "", reverse=True)[:k]


def candidates(request: str, skills: dict[str, dict]) -> list[str]:
    ranked = [slug for slug, _ in keyword.shortlist(request, k=PREFILTER_K)]
    if ranked:
        return ranked
    slugs = sorted(skills)
    return slugs if len(slugs) <= PREFILTER_K else _recently_used(slugs, PREFILTER_K)


def _line(skill: dict) -> str:
    examples = json.loads(skill.get("examples") or "[]")[:CARD_EXAMPLES_IN_LINE]
    line = skill["description"] or skill["name"]
    return f"{line} Examples: {'; '.join(examples)}" if examples else line


def catalog(slugs: list[str], skills: dict[str, dict]) -> dict:
    return {"tree": [{"id": "all", "line": "All skills", "skills": slugs}],
            "skills": {s: {"line": _line(skills[s])} for s in slugs}, "overrides": {}}


def _describe(skills: dict[str, dict]):
    def describe(slug: str) -> str:
        try:
            return (Path(skills[slug]["path"]) / frontmatter.SKILL_FILE).read_text(errors="replace")[:DESCRIBE_CHARS]
        except OSError:
            return ""
    return describe


def suggest_jev(request: str) -> dict:
    skills = _adopted()
    slugs = candidates(request, skills)
    if not slugs:
        return keyword.suggest_keyword(request)
    s = route(request, catalog(slugs, skills), get_decision_backend(), DEFAULTS, describe=_describe(skills))
    if s.reason == "unavailable":
        return {**keyword.suggest_keyword(request), "fallback": "keyword"}
    shortlist = [{"skill": slug, "fit": round(fit, 3)} for slug, fit in s.shortlist]
    confident = s.reason == "suggested" and s.confidence >= SUGGEST_BAR
    reason = "suggested" if confident else ("below_bar" if s.reason == "suggested" else s.reason)
    skill = s.skill if confident else None
    keyword.log_routing(request, "jev", skill, shortlist, round(s.confidence, 3) if s.confidence else None, reason)
    note = NOTE_SUGGESTED if confident else (NOTE_BELOW_BAR if shortlist else NOTE_NONE)
    return {"mode": "jev", "skill": skill, "confidence": round(s.confidence, 3), "shortlist": shortlist,
            "reason": reason, "note": note, "jev_input_tokens": s.input_tokens}
