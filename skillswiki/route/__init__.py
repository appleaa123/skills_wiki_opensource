"""Routing entry point. The CLI, MCP server and hook all call suggest(): JEV when the user has a TypeSafe key,
keyword BM25 otherwise."""
from skillswiki.decision import jev_enabled
from skillswiki.route.keyword import suggest_keyword


def suggest(request: str, budget_s: float | None = None) -> dict:
    """`budget_s` caps the time JEV may take (default jev_route.DEFAULT_BUDGET_S)."""
    if jev_enabled():
        from skillswiki.route.jev_route import DEFAULT_BUDGET_S, suggest_jev
        return suggest_jev(request, budget_s or DEFAULT_BUDGET_S)
    return suggest_keyword(request)
