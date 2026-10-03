"""Verifies JEV Phase 2 P2.1 (revised 2026-09-26, single-judge design):
core/decision/policy.py — the pure, pack-agnostic rule deciding per criterion
whether JEV's grade is final or the user's own LLM judge decides.
Trust is earned first: with no calibrated evidence against THIS judge, the LLM
decides. On fact-fidelity and "no X" criteria JEV may only fail an output,
never pass it (its measured weakness is leniency there)."""
import sys
from pathlib import Path

import pytest


from skillswiki.decision import policy  # noqa: E402

CONFIG = {"p_audit": 0.10, "fail_only_kinds": ["factual", "constraint"], "untagged_kind": "factual"}
TRUSTED = {"status": "calibrated", "threshold": 0.8}


def _decide(props=None, entry=TRUSTED, certainty=0.99, jev_passes=False, audit_u=0.5, verify_ok=None,
            strict=False, config=CONFIG):
    return policy.decide(props or {}, entry, certainty, jev_passes, audit_u, verify_ok, strict, config)


@pytest.mark.parametrize("kwargs, final_by, reason, level", [
    ({"props": {"kind": "count"}, "verify_ok": True, "strict": True}, "verify", "rule:count", 2),
    ({"props": {"kind": "count"}, "verify_ok": False, "strict": True}, "verify", "rule:count", 0),
    ({"props": {"kind": "count"}, "verify_ok": True, "strict": False}, "llm", "rule:count", None),
    ({"props": {"kind": "count"}}, "llm", "rule:count", None),
    ({"props": {"kind": "legal"}}, "llm", "rule:risk", None),
    ({"props": {"kind": "style", "risk": "high"}}, "llm", "rule:risk", None),
    ({"props": {"kind": "factual"}, "verify_ok": False, "strict": True}, "verify", "verify:fail", 0),
    ({"props": {"kind": "factual"}, "verify_ok": False, "strict": False}, "llm", "verify:flag", None),
    ({"entry": None}, "llm", "no_evidence", None),
    ({"entry": {"status": "cold", "threshold": None}}, "llm", "no_evidence", None),
    ({"entry": {"status": "escalate", "threshold": None}}, "llm", "status:escalate", None),
    ({"certainty": 0.79}, "llm", "uncertain", None),
    ({"props": {"kind": "factual"}, "jev_passes": True}, "llm", "confirm_pass", None),
    ({"props": {"kind": "constraint"}, "jev_passes": True}, "llm", "confirm_pass", None),
    ({"audit_u": 0.05}, "llm", "audit", None),
    ({}, "jev", "jev", None),
    ({"props": {"kind": "style"}, "jev_passes": True}, "jev", "jev", None),
])
def test_policy_table(kwargs, final_by, reason, level):
    d = _decide(**kwargs)
    assert (d.final_by, d.reason, d.level) == (final_by, reason, level)


def test_untagged_criterion_gets_the_cautious_treatment():
    assert _decide(props={}, jev_passes=True).reason == "confirm_pass"  # untagged = factual by default
    assert _decide(props={}, jev_passes=False).final_by == "jev"
    assert policy.effective_kind({}, CONFIG) == "factual"
    assert policy.effective_kind({"kind": "style"}, CONFIG) == "style"


def test_fact_criteria_jev_may_fail_but_not_pass():
    assert _decide(props={"kind": "factual"}, jev_passes=False).final_by == "jev"
    assert _decide(props={"kind": "factual"}, jev_passes=True).final_by == "llm"
    assert _decide(props={"kind": "style"}, jev_passes=True).final_by == "jev"  # style: either way


def test_verify_pass_on_non_count_proves_nothing():
    assert _decide(props={"kind": "style"}, verify_ok=True, strict=True, jev_passes=True).final_by == "jev"


def test_high_risk_never_jev_even_if_store_trusts_it():
    assert _decide(props={"kind": "legal"}, entry={"status": "calibrated", "threshold": 0.6}).final_by == "llm"


def test_certainty_and_audit_draw():
    assert policy.certainty(0.9) == pytest.approx(0.9)
    assert policy.certainty(0.1) == pytest.approx(0.9)
    u = policy.audit_draw("t01|0|crit")
    assert u == policy.audit_draw("t01|0|crit") and 0 <= u < 1
    draws = [policy.audit_draw(f"t{i}|0|c") for i in range(2000)]
    assert 0.07 < sum(d < 0.10 for d in draws) / len(draws) < 0.13
