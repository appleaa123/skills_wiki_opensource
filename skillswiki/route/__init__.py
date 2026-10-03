"""Routing entry point. The CLI, MCP server and hook all call suggest()."""
from skillswiki.route.keyword import suggest_keyword


def suggest(request: str) -> dict:
    return suggest_keyword(request)
