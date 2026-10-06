"""A realistic request for the guided test. The skill's own description is useless as a test, because any search
matches it word for word (Phase 12 test run), so setup asks the user's own AI CLI for a paraphrase (a few of the
user's tokens) and checks that it really uses other words."""
import shutil

from skillswiki.evals.backends import BackendUnavailable, get_backend
from skillswiki.route import keyword

CLI_ORDER = (("claude", ("claude",)), ("codex", ("codex",)), ("gemini", ("agy", "gemini")))
TIMEOUT_S = 60
MAX_CHARS = 300
MAX_OVERLAP = 0.5  # share of the request's words that may also appear in the description
PROMPT = ("Here is the description of an AI skill:\n\n{description}\n\nWrite ONE short request, in the same language "
          "as the description, that a user might type to an AI assistant and that this skill should handle. Use your "
          "own everyday words and do not reuse the description's distinctive terms. Reply with the request only.")


def available_backend() -> str | None:
    """The first AI CLI on PATH, as a backend name (claude, codex, gemini)."""
    for name, executables in CLI_ORDER:
        if any(shutil.which(exe) for exe in executables):
            return name
    return None


def overlap(request: str, description: str) -> float:
    words = set(keyword.tokenize(request))
    return len(words & set(keyword.tokenize(description))) / len(words) if words else 1.0


def _clean(reply: str) -> str | None:
    lines = [line.strip() for line in (reply or "").strip().splitlines() if line.strip()]
    text = lines[0].strip("\"'“”「」") if lines else ""
    return text if 0 < len(text) <= MAX_CHARS else None


def from_ai(description: str, backend: str) -> str | None:
    """A paraphrased request from the user's AI CLI, or None when it fails or mostly repeats the description."""
    try:
        reply = get_backend(backend).judge(PROMPT.format(description=description), None, timeout=TIMEOUT_S)["text"]
    except (BackendUnavailable, OSError, ValueError, KeyError):
        return None
    text = _clean(reply)
    return text if text and overlap(text, description) <= MAX_OVERLAP else None
