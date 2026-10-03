"""Cascade policy: per criterion, is the decision layer's grade final, or does the
user's own LLM judge decide?

Pure and vendor-neutral. It knows criterion PROPERTIES (kind, risk, a verify
check result) and a calibration entry — never a pack or criterion name, so the
same rule serves every pack and every judge.
"""
import hashlib
from dataclasses import dataclass

JEV, LLM, VERIFY = "jev", "llm", "verify"
PASS_LEVEL, FAIL_LEVEL = 2, 0
DEFAULT_KIND = "style"
_HASH_SPAN = 16 ** 12


@dataclass(frozen=True)
class Decision:
    final_by: str  # JEV | LLM | VERIFY
    reason: str
    level: int | None = None  # set when VERIFY decides


def certainty(p2: float) -> float:
    """Pass/fail certainty max(P2, 1 - P2): what Phase 1 showed predicts error."""
    return max(p2, 1 - p2)


def audit_draw(key: str) -> float:
    """Deterministic uniform draw in [0, 1) for `key`, so an audit slice is reproducible."""
    return int(hashlib.sha256(key.encode()).hexdigest()[:12], 16) / _HASH_SPAN


def effective_kind(props: dict, config: dict) -> str:
    """The criterion's declared kind; an untagged criterion gets the cautious kind (config `untagged_kind`)."""
    return props.get("kind") or config.get("untagged_kind") or DEFAULT_KIND


def decide(props: dict, entry: dict | None, certainty_: float, jev_passes: bool, audit_u: float,
           verify_ok: bool | None, strict: bool, config: dict) -> Decision:
    """First matching rule wins. `entry` is this criterion's calibration against THIS judge (None = no
    evidence yet: the judge decides; trust is earned, never assumed). `verify_ok` is None when no check
    covers the criterion; `strict` means the check's FAIL is trusted without a judge. `config` is
    evals/config.json's "cascade" block."""
    kind = effective_kind(props, config)
    if kind == "count":
        if verify_ok is not None and strict:
            return Decision(VERIFY, "rule:count", PASS_LEVEL if verify_ok else FAIL_LEVEL)
        return Decision(LLM, "rule:count")
    if kind == "legal" or props.get("risk") == "high":
        return Decision(LLM, "rule:risk")
    if verify_ok is False:
        return Decision(VERIFY, "verify:fail", FAIL_LEVEL) if strict else Decision(LLM, "verify:flag")
    if not entry or entry.get("status") == "cold" or entry.get("threshold") is None:
        status = (entry or {}).get("status")
        return Decision(LLM, f"status:{status}" if status and status != "cold" else "no_evidence")
    if certainty_ < entry["threshold"]:
        return Decision(LLM, "uncertain")
    if jev_passes and kind in config["fail_only_kinds"]:
        return Decision(LLM, "confirm_pass")  # JEV's measured weakness: leniency on fact / "no X" rules
    if audit_u < config["p_audit"]:
        return Decision(LLM, "audit")
    return Decision(JEV, "jev")
