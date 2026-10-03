"""Vendor-neutral decision-layer interface.

Callers depend on these dataclasses and on DecisionUnavailable only — never on a
vendor. Any backend failure surfaces as DecisionUnavailable, and callers must
then behave exactly as the product behaves without a decision layer.
"""
from dataclasses import dataclass
from typing import Any


class DecisionUnavailable(Exception):
    """Raised on any backend failure. Callers must fall back to today's behavior. `reason` is a short label for
    telemetry (timeout | key_rejected | rate_limited | overloaded | unavailable), never shown to users raw."""

    def __init__(self, message: str = "", reason: str = "unavailable"):
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class ChoiceQ:
    instructions: str
    criteria: dict[str, str]  # option -> description


@dataclass(frozen=True)
class ScoreQ:
    instructions: str
    criteria: list[Any]  # levels low -> high (str or {"what", "examples"})


@dataclass(frozen=True)
class NoulQ:
    instructions: str
    criteria: dict[str, str] | None = None


@dataclass(frozen=True)
class ChoiceA:
    choice: str
    probabilities: dict[str, float]
    confidence: float


@dataclass(frozen=True)
class ScoreA:
    score: float
    probabilities: dict[str, float]
    confidence: float
    legend: dict[str, str]


@dataclass(frozen=True)
class NoulA:
    noul: float


Question = ChoiceQ | ScoreQ | NoulQ
Answer = ChoiceA | ScoreA | NoulA


@dataclass(frozen=True)
class DecisionResult:
    answers: dict[str, Answer]
    input_tokens: int
    model: str


class DecisionBackend:
    name: str = "base"

    def decide(self, state: dict | str, questions: dict[str, Question]) -> DecisionResult:
        raise NotImplementedError
