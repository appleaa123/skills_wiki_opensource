"""Disabled/fallback backend: every call is an immediate DecisionUnavailable."""
from skillswiki.decision.base import DecisionBackend, DecisionResult, DecisionUnavailable


class NullBackend(DecisionBackend):
    name = "null"

    def decide(self, state, questions) -> DecisionResult:
        raise DecisionUnavailable("decision backend disabled")
