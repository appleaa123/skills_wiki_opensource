"""TypeSafe JEV backend over raw HTTPS (no SDK).

This is the ONLY file that knows TypeSafe exists. Wire field names below were
confirmed against the live API (https://docs.typesafe.ai). Keep every vendor
string in this block. Calls use the user's own TYPESAFE_API_KEY.
"""
import logging
import time
from typing import Callable

import httpx

from skillswiki.decision.base import (
    ChoiceA,
    ChoiceQ,
    DecisionBackend,
    DecisionResult,
    DecisionUnavailable,
    NoulA,
    NoulQ,
    ScoreA,
    ScoreQ,
)

JEV_URL = "https://api.typesafe.ai/v1/systemone"
DEFAULT_MODEL = "jev-latest"
TYPE_CHOICE, TYPE_SCORE, TYPE_NOUL = "choice", "score", "noul"
RETRYABLE_STATUS = frozenset({429, 529})
MAX_ATTEMPTS = 3
BACKOFF_BASE_S = 0.5  # sleeps 0.5s, then 1.0s between attempts
TIMEOUT_S = 10.0
ERROR_BODY_CHARS = 300
REASON_BY_STATUS = {401: "key_rejected", 403: "key_rejected", 429: "rate_limited", 529: "overloaded"}

logger = logging.getLogger(__name__)


def _question_payload(q) -> dict:
    if isinstance(q, ChoiceQ):
        return {"type": TYPE_CHOICE, "instructions": q.instructions, "criteria": dict(q.criteria)}
    if isinstance(q, ScoreQ):
        return {"type": TYPE_SCORE, "instructions": q.instructions, "criteria": list(q.criteria)}
    if isinstance(q, NoulQ):
        payload = {"type": TYPE_NOUL, "instructions": q.instructions}
        if q.criteria is not None:
            payload["criteria"] = dict(q.criteria)
        return payload
    raise TypeError(f"unsupported question type: {type(q).__name__}")


def _floats(d: dict) -> dict[str, float]:
    return {str(k): float(v) for k, v in d.items()}


def _parse_answer(raw: dict):
    kind = raw["type"]
    if kind == TYPE_CHOICE:
        return ChoiceA(
            choice=str(raw["choice"]),
            probabilities=_floats(raw["probabilities"]),
            confidence=float(raw["confidence"]),
        )
    if kind == TYPE_SCORE:
        return ScoreA(
            score=float(raw["score"]),
            probabilities=_floats(raw["probabilities"]),
            confidence=float(raw["confidence"]),
            legend={str(k): str(v) for k, v in raw["legend"].items()},
        )
    if kind == TYPE_NOUL:
        return NoulA(noul=float(raw["noul"]))
    raise ValueError(f"unknown answer type: {kind!r}")


def _parse_response(data: dict) -> DecisionResult:
    return DecisionResult(
        answers={key: _parse_answer(raw) for key, raw in data["answers"].items()},
        input_tokens=int(data["usage"]["input_tokens"]),
        model=str(data["model"]),
    )


class JevBackend(DecisionBackend):
    name = "jev"

    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_MODEL,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        deadline: float | None = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        # transport/sleep/clock are injectable so tests run on httpx.MockTransport with no waits.
        # deadline (a `clock` time): no call or retry sleep may run past it (the gateway's 2 s budget, Phase 4).
        self._api_key = api_key
        self._model = model
        self._transport = transport
        self._sleep = sleep
        self._deadline = deadline
        self._clock = clock

    def _left(self) -> float | None:
        return None if self._deadline is None else self._deadline - self._clock()

    def decide(self, state, questions) -> DecisionResult:
        body = {
            "model": self._model,
            "state": state,
            "questions": {key: _question_payload(q) for key, q in questions.items()},
        }
        response = self._post_with_retry(body, list(questions))
        try:
            result = _parse_response(response.json())
        except (KeyError, TypeError, ValueError, AttributeError) as e:
            logger.warning("JEV response unparseable for %s: %r", list(questions), e)
            raise DecisionUnavailable(f"JEV response unparseable: {e!r}") from e
        missing = sorted(set(questions) - set(result.answers))
        if missing:
            raise DecisionUnavailable(f"JEV response missing answers for {missing}")
        return result

    def _post_with_retry(self, body: dict, keys: list[str]) -> httpx.Response:
        headers = {"Authorization": f"Bearer {self._api_key}"}
        last_error, last_reason = "no attempt made", "unavailable"
        with httpx.Client(transport=self._transport, timeout=TIMEOUT_S) as client:
            for attempt in range(MAX_ATTEMPTS):
                if attempt:
                    pause = BACKOFF_BASE_S * 2 ** (attempt - 1)
                    left = self._left()
                    if left is not None and left <= pause:  # sleeping would pass the deadline
                        raise DecisionUnavailable(
                            f"JEV deadline reached after {attempt} attempt(s), {last_error}", reason=last_reason)
                    self._sleep(pause)
                left = self._left()
                if left is not None and left <= 0:
                    raise DecisionUnavailable("JEV deadline passed", reason="timeout")
                timeout = TIMEOUT_S if left is None else min(TIMEOUT_S, left)
                try:
                    response = client.post(JEV_URL, headers=headers, json=body, timeout=timeout)
                except httpx.TimeoutException as e:
                    last_error, last_reason = f"timeout: {e!r}", "timeout"
                    logger.warning("JEV %s (attempt %d) for %s", last_error, attempt + 1, keys)
                    continue
                except httpx.TransportError as e:
                    last_error, last_reason = f"transport error: {e!r}", "unavailable"
                    logger.warning("JEV %s (attempt %d) for %s", last_error, attempt + 1, keys)
                    continue
                if response.is_success:
                    return response
                detail = f"HTTP {response.status_code}: {response.text[:ERROR_BODY_CHARS]}"
                reason = REASON_BY_STATUS.get(response.status_code, "unavailable")
                if response.status_code not in RETRYABLE_STATUS:
                    raise DecisionUnavailable(f"JEV request rejected, {detail}", reason=reason)
                last_error, last_reason = detail, reason
                logger.warning("JEV %s (attempt %d) for %s", detail, attempt + 1, keys)
        raise DecisionUnavailable(f"JEV unavailable after {MAX_ATTEMPTS} attempts, {last_error}", reason=last_reason)
