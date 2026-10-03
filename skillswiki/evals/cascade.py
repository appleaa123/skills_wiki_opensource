"""Cascade judge for evals/runner.py and rejudge.py (JEV Phase 2, P2.3).

JEV grades every criterion of an output in one call; `core.decision.policy`
decides per criterion whether that grade is final; the user's own LLM judge
(the run's existing judge backend, on the user's tokens) grades only the rest,
in one call over a sub-rubric. Pairwise comparisons are SHADOW only (human
decision 2026-09-26): the LLM judge decides, JEV's answer is recorded.

Opt-in with `skillswiki eval run --cascade` (needs the user's own TYPESAFE_API_KEY). runner.py imports this
module lazily. Skill-agnostic: behaviour comes from rubric properties and the calibration store, never from skill
or criterion names. The store starts empty: every criterion is graded by the LLM judge until the user's own runs
show JEV agrees with that judge on it.
"""
import hashlib
import json
from collections import Counter

from skillswiki.decision import get_decision_backend
from skillswiki.decision import policy
from skillswiki.decision.null import NullBackend
from skillswiki.evals import calibration
from skillswiki.evals import runner
from skillswiki.evals import suite
from skillswiki.evals.backends import BackendUnavailable

GROUPS = ("dimensions", "items", "applied")
REASON_OVERSIZE, REASON_UNAVAILABLE, REASON_EMPTY = "oversize", "unavailable", "empty"
_FINGERPRINT_KEYS = ("fail_only_kinds", "baseline_disagreement", "exact_baseline", "lean_limit", "min_per_arm",
                     "p_audit", "state_char_limit", "pairwise")


def judge_key(judge_name: str, judge_model: str | None) -> str:
    return f"{judge_name}:{judge_model}" if judge_model else judge_name


class Cascade:
    def __init__(self, jev, judge_key_: str, store: dict, config: dict, strict: frozenset, criterion_checks=None):
        self.jev = jev
        self.criterion_checks = criterion_checks
        self.judge_key = judge_key_
        self.store = store
        self.config = config
        self.strict = frozenset(strict)

    @classmethod
    def build(cls, pack: str, judge_backend, judge_name: str, judge_model: str | None, config: dict) -> "Cascade":
        """Refuses clearly instead of silently grading all-LLM: the user asked for the cascade."""
        if getattr(judge_backend, "structured", False):
            raise ValueError("--cascade needs an LLM judge (claude/gemini/codex) to escalate to, not --judge jev")
        decision = get_decision_backend()
        if isinstance(decision, NullBackend):
            raise ValueError("--cascade needs your own TYPESAFE_API_KEY (see .env.example)")
        from skillswiki.evals.backends.jev import JevJudge
        from skillswiki.evals.rubric_lint import load_checks
        _verify, criterion_checks, strict = load_checks(suite.suite_dir(pack) / "verify.py")
        key = judge_key(judge_name, judge_model)
        return cls(JevJudge(decision), key, calibration.load(pack), config["cascade"], frozenset(strict),
                   criterion_checks)

    def extra_checks(self, task: dict, deliverable: str) -> dict:
        """The pack's criterion-only checks (never pass gates); {} when it defines none."""
        return self.criterion_checks(task, deliverable) if self.criterion_checks else {}

    def fingerprint(self) -> dict:
        """Enters grading.hash: a cascade run never compares against an all-LLM run (or another judge)."""
        cfg = {k: self.config.get(k) for k in _FINGERPRINT_KEYS}
        digest = hashlib.sha256(json.dumps(cfg, sort_keys=True).encode()).hexdigest()[:12]
        return {"judge_key": self.judge_key, "config": digest}

    # ── pointwise ────────────────────────────────────────────────────

    def _decide(self, crit: dict, detail: dict | None, fallback: str | None, verify_result: dict | None,
                seed: str) -> policy.Decision:
        if detail is None:
            return policy.Decision(policy.LLM, fallback)
        check = crit.get("verify")
        verify_ok = (verify_result or {}).get(check) if check else None
        d = detail[crit["id"]]
        return policy.decide(crit, calibration.entry(self.store, self.judge_key, crit["id"]),
                             policy.certainty(d["p2"]), d["level"] >= policy.PASS_LEVEL,
                             policy.audit_draw(f"{seed}|{crit['id']}"), verify_ok, check in self.strict, self.config)

    def grade(self, llm_backend, prompt: str, deliverable: str, rubric: dict, verify_result: dict | None,
              seed: str, judge_model, timeout: int, profile, votes: int):
        """Returns (scale, llm_usage, jev_detail, meta, jev_usage) for every rubric and applied id."""
        crits = [c for g in GROUPS for c in rubric.get(g, [])]
        if not deliverable:
            meta = {c["id"]: {"final_by": "none", "reason": REASON_EMPTY} for c in crits}
            return {c["id"]: 0 for c in crits}, None, None, meta, None

        detail, jev_usage, fallback = None, None, None
        if len(prompt) + len(deliverable) > self.config["state_char_limit"]:
            fallback = REASON_OVERSIZE  # never truncate: that would grade a different deliverable
        else:
            try:
                result = self.jev.judge_rubric(prompt, deliverable, rubric)
                detail, jev_usage = result["detail"], result["usage"]
            except BackendUnavailable:
                fallback = REASON_UNAVAILABLE  # today's behaviour: the LLM judge grades everything

        decisions = {c["id"]: self._decide(c, detail, fallback, verify_result, seed) for c in crits}
        llm_ids = {cid for cid, d in decisions.items() if d.final_by == policy.LLM}
        llm_scale, llm_usage = {}, None
        if llm_ids:
            sub = {g: [c for c in rubric.get(g, []) if c["id"] in llm_ids] for g in GROUPS}
            llm_scale, llm_usage = runner._judge(llm_backend, prompt, deliverable, sub, judge_model,
                                                 timeout=timeout, profile=profile, seed=seed, votes=votes)

        scale, meta = {}, {}
        for cid, d in decisions.items():
            if d.final_by == policy.JEV:
                scale[cid] = detail[cid]["level"]
            elif d.final_by == policy.VERIFY:
                scale[cid] = d.level
            else:
                scale[cid] = llm_scale.get(cid, 0)
            meta[cid] = {"final_by": d.final_by, "reason": d.reason,
                         **({"certainty": policy.certainty(detail[cid]["p2"])} if detail else {})}
        return scale, llm_usage, detail, meta, jev_usage

    # ── pairwise (shadow) ────────────────────────────────────────────

    def shadow_pair(self, prompt: str, out_x: str, out_y: str, rubric: dict) -> list[dict] | None:
        """JEV's answer in both orders, recorded only. None when JEV is unavailable or a side is empty."""
        if not out_x or not out_y:
            return None
        try:
            return [{"order": "ab", **self.jev.judge_pair(prompt, out_x, out_y, rubric)},
                    {"order": "ba", **self.jev.judge_pair(prompt, out_y, out_x, rubric)}]
        except BackendUnavailable:
            return None

    # ── summary ──────────────────────────────────────────────────────

    def summary(self, records: list[dict]) -> dict:
        metas = [m for r in records for m in (r.get("cascade") or {}).values()]
        final_by = Counter(m["final_by"] for m in metas)
        tokens = sum(runner._sum_usage(r.get("jev_usage")) for r in records)
        return {
            "judge_key": self.judge_key,
            "verdicts": len(metas),
            "final_by": dict(final_by),
            "reasons": dict(Counter(m["reason"] for m in metas)),
            "escalation_rate": final_by.get(policy.LLM, 0) / len(metas) if metas else None,
            "llm_judge_calls": sum(1 for r in records if any(m["final_by"] == policy.LLM
                                                            for m in (r.get("cascade") or {}).values())),
            "jev_input_tokens": tokens,
            "jev_cost_usd": round(tokens * self.config["usd_per_million_jev_tokens"] / 1_000_000, 6),
            "pairwise": self.config.get("pairwise"),
        }
