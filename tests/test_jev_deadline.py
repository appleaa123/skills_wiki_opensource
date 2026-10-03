"""core/decision/jev.py deadline + failure reasons (JEV Phase 4, P4.9a). No network: httpx.MockTransport and a fake
clock whose sleep advances it. Without a deadline the backend must behave exactly as before
(tests/test_decision_backend.py stays untouched and green)."""
import sys
from pathlib import Path

import httpx
import pytest


from skillswiki.decision.base import DecisionUnavailable, NoulQ  # noqa: E402
from skillswiki.decision.jev import JevBackend  # noqa: E402

OK = {"model": "jev-1.13.0", "answers": {"q": {"type": "noul", "noul": 0.9}}, "usage": {"input_tokens": 10}}
QUESTIONS = {"q": NoulQ("The request is a greeting.")}


class Clock:
    def __init__(self):
        self.now = 100.0
        self.slept = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


def _backend(responses, clock, deadline_in=None, seen=None):
    """responses: list of status codes / exceptions / callables consumed one per request."""
    queue = list(responses)

    def handler(request):
        if seen is not None:
            seen.append(request.extensions.get("timeout"))
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        if callable(item):
            return item(request)
        return httpx.Response(item, json=OK if item == 200 else {"detail": {"message": "x"}})

    deadline = None if deadline_in is None else clock.now + deadline_in
    return JevBackend("k", transport=httpx.MockTransport(handler), sleep=clock.sleep, deadline=deadline, clock=clock)


def test_reason_defaults_to_unavailable():
    assert DecisionUnavailable("x").reason == "unavailable"
    assert DecisionUnavailable("x", reason="timeout").reason == "timeout"


def test_per_call_timeout_is_the_time_left():
    clock, seen = Clock(), []
    result = _backend([200], clock, deadline_in=1.5, seen=seen).decide({"request": "hi"}, QUESTIONS)
    assert result.model == "jev-1.13.0"
    assert all(v == pytest.approx(1.5) for v in seen[0].values())


def test_deadline_already_passed_sends_nothing():
    clock, seen = Clock(), []
    backend = _backend([200], clock, deadline_in=0, seen=seen)
    with pytest.raises(DecisionUnavailable) as e:
        backend.decide({"request": "hi"}, QUESTIONS)
    assert e.value.reason == "timeout" and seen == []


@pytest.mark.parametrize("status,reason", [(429, "rate_limited"), (529, "overloaded")])
def test_retryable_failures_keep_their_reason(status, reason):
    clock = Clock()
    with pytest.raises(DecisionUnavailable) as e:
        _backend([status, status, status], clock).decide({"request": "hi"}, QUESTIONS)
    assert e.value.reason == reason and "after 3 attempts" in str(e.value)


@pytest.mark.parametrize("status,reason", [(401, "key_rejected"), (403, "key_rejected"), (400, "unavailable")])
def test_rejections_fail_fast_with_a_reason(status, reason):
    clock, seen = Clock(), []
    with pytest.raises(DecisionUnavailable) as e:
        _backend([status], clock, seen=seen).decide({"request": "hi"}, QUESTIONS)
    assert e.value.reason == reason and len(seen) == 1


def test_transport_timeout_is_a_timeout():
    clock = Clock()
    timeout = httpx.ReadTimeout("slow")
    with pytest.raises(DecisionUnavailable) as e:
        _backend([timeout, timeout, timeout], clock).decide({"request": "hi"}, QUESTIONS)
    assert e.value.reason == "timeout"


def test_other_transport_errors_are_unavailable():
    clock = Clock()
    err = httpx.ConnectError("down")
    with pytest.raises(DecisionUnavailable) as e:
        _backend([err, err, err], clock).decide({"request": "hi"}, QUESTIONS)
    assert e.value.reason == "unavailable"


def test_never_sleeps_past_the_deadline():
    clock = Clock()
    backend = _backend([429, 429, 429], clock, deadline_in=0.7)
    with pytest.raises(DecisionUnavailable) as e:
        backend.decide({"request": "hi"}, QUESTIONS)
    assert sum(clock.slept) <= 0.7 and e.value.reason == "rate_limited"
    assert clock.slept == [0.5]  # the 1.0 s backoff would pass the deadline, so it stops instead


def test_retry_succeeds_inside_the_deadline():
    clock = Clock()
    result = _backend([529, 200], clock, deadline_in=2.0).decide({"request": "hi"}, QUESTIONS)
    assert result.input_tokens == 10 and clock.slept == [0.5]


def test_no_deadline_behaves_as_before():
    clock, seen = Clock(), []
    with pytest.raises(DecisionUnavailable, match="after 3 attempts"):
        _backend([529, 529, 529], clock, seen=seen).decide({"request": "hi"}, QUESTIONS)
    assert clock.slept == [0.5, 1.0] and len(seen) == 3
    assert all(v == pytest.approx(10.0) for v in seen[0].values())
