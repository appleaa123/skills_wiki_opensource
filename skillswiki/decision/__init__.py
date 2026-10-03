"""Decision layer (JEV by TypeSafe). Nothing outside this package may call a decision vendor. Callers get a
backend from get_decision_backend() and treat DecisionUnavailable as "behave as without JEV".

JEV is on whenever the user's own TYPESAFE_API_KEY is set (setting it is the user's consent; Skills Wiki never
holds a key of its own). SKILLSWIKI_JEV=off turns it off even with a key. Env is read per call, so tests can
monkeypatch it.
"""
import os

from skillswiki.decision.base import DecisionBackend, DecisionUnavailable
from skillswiki.decision.null import NullBackend

KEY_ENV = "TYPESAFE_API_KEY"
SWITCH_ENV = "SKILLSWIKI_JEV"
MODEL_ENV = "JEV_MODEL"
_OFF = frozenset({"off", "false", "0", "no"})

__all__ = ["DecisionBackend", "DecisionUnavailable", "get_decision_backend", "jev_enabled"]


def jev_enabled() -> bool:
    return bool(os.getenv(KEY_ENV, "").strip()) and os.getenv(SWITCH_ENV, "").strip().lower() not in _OFF


def get_decision_backend(deadline: float | None = None) -> DecisionBackend:
    """The JEV backend when jev_enabled(), else NullBackend (every call raises DecisionUnavailable).
    `deadline` is a time.monotonic() value no call or retry may run past (then DecisionUnavailable)."""
    if not jev_enabled():
        return NullBackend()
    from skillswiki.decision.jev import DEFAULT_MODEL, JevBackend  # vendor code loads only when enabled

    return JevBackend(os.getenv(KEY_ENV, "").strip(), model=os.getenv(MODEL_ENV, DEFAULT_MODEL), deadline=deadline)
