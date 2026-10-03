"""Routing entry point. The CLI, MCP server and hook all call suggest(): JEV when the user has a TypeSafe key,
keyword BM25 otherwise."""
from skillswiki.decision import jev_enabled
from skillswiki.route.keyword import suggest_keyword


def suggest(request: str) -> dict:
    if jev_enabled():
        from skillswiki.route.jev_route import suggest_jev
        return suggest_jev(request)
    return suggest_keyword(request)
