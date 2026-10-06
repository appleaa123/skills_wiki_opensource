"""One exception type for expected failures, carrying a stable code so scripts and agents can react to the code
instead of parsing the sentence.

Subclasses ValueError so every existing `except ValueError` (CLI, MCP, web, tests) keeps working.
"""

CODES = frozenset({"NOT_FOUND", "NOT_ADOPTED", "ALREADY_ADOPTED", "PLUGIN_MANAGED", "RESERVED", "SOURCE_MISSING",
                   "TARGET_EXISTS", "RELATIVE_SYMLINK", "LEARNING_OFF", "INVALID_INPUT", "BACKEND_UNAVAILABLE"})
DEFAULT_CODE = "INVALID_INPUT"


class SkillsWikiError(ValueError):
    def __init__(self, code: str, message: str, **details):
        if code not in CODES:  # a programming error, never a user error. Not a ValueError, so no CLI/MCP/web
            raise LookupError(f"unknown error code {code!r}")  # handler can report it as INVALID_INPUT
        super().__init__(message)
        self.code = code
        self.details = details


def as_payload(exc: BaseException) -> dict:
    """{"code", "message", "details"} for any exception; a bare ValueError is INVALID_INPUT with no details."""
    return {"code": getattr(exc, "code", DEFAULT_CODE), "message": str(exc), "details": getattr(exc, "details", {})}
