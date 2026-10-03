"""Verifies the JEV grading cascade: skillswiki/evals/cascade.py and its runner touch
points. JEV grades every criterion; policy decides per criterion whether its
grade is final; the user's LLM judge grades only the rest; pairwise is shadow
only; flag off (no cascade) is byte-for-byte today's shape. Fakes only: no JEV
or LLM calls."""
import json
import subprocess
import sys
from pathlib import Path

import pytest


from skillswiki.evals import cascade as cz  # noqa: E402
from skillswiki.evals import runner  # noqa: E402
from skillswiki.evals.backends import BackendUnavailable  # noqa: E402

CONFIG = {"ladder": [0.95, 0.9, 0.8, 0.7, 0.6], "fail_only_kinds": ["factual", "constraint"],
          "baseline_disagreement": 0.21, "exact_baseline": 0.334, "lean_limit": 0.1, "min_per_arm": 8,
          "p_audit": 0.0, "state_char_limit": 5000, "pairwise": "shadow", "usd_per_million_jev_tokens": 0.042}
RUBRIC = {"dimensions": [{"id": "sure", "desc": "Output is complete."}],
          "items": [{"id": "unsure", "desc": "Reads naturally.", "weight": 1},
                    {"id": "legal", "desc": "Describes the property only.", "kind": "legal", "weight": 1}],
          "applied": [{"id": "len", "desc": "Headline under 100 characters.", "kind": "count", "verify": "len"}]}
EMPTY_STORE = {"pack": "demo", "updated": None, "runs": [], "judges": {}, "rows": {}}
# Evidence has earned JEV trust at 0.8 on every criterion for judge "claude" (rules still win).
TRUSTED = {"status": "calibrated", "threshold": 0.8}
STORE = {**EMPTY_STORE, "judges": {"claude": {c: TRUSTED for c in ("sure", "unsure", "legal", "len", "a", "b", "r")}}}


class FakeJev:
    structured = True
    name = "jev"

    def __init__(self, p2=None, fail=False):
        self.p2 = p2 or {"sure": 0.99, "unsure": 0.6, "legal": 0.99, "len": 0.99}
        self.fail = fail
        self.calls = 0
        self.pair_calls = 0

    def judge_rubric(self, prompt, deliverable, rubric):
        self.calls += 1
        if self.fail:
            raise BackendUnavailable("JEV down")
        detail = {cid: {"level": 2 if p >= 0.5 else 0, "p2": p} for cid, p in self.p2.items()}
        return {"scale": {c: d["level"] for c, d in detail.items()}, "detail": detail, "usage": {"input_tokens": 100}}

    def judge_pair(self, prompt, a, b, rubric):
        self.pair_calls += 1
        if self.fail:
            raise BackendUnavailable("JEV down")
        return {"winner": "A", "detail": {"p_a": 0.8, "p_b": 0.2, "confidence": 0.8}, "usage": {"input_tokens": 50}}


class FakeLlm:
    name = "claude"

    def __init__(self, level=1):
        self.level = level
        self.prompts = []

    def judge(self, prompt, model, timeout=60, profile=None):
        self.prompts.append(prompt)
        if '"winner"' in prompt or "Response A" in prompt:
            return {"text": json.dumps({"winner": "B"}), "usage": {"input_tokens": 10}}
        ids = ["sure", "unsure", "legal", "len"]
        return {"text": json.dumps({i: self.level for i in ids}), "usage": {"input_tokens": 10}}


def _cascade(jev=None, config=CONFIG, store=STORE, strict=frozenset({"len"})):
    return cz.Cascade(jev or FakeJev(), "claude", store, config, strict)


def _grade(cascade, llm, deliverable="A short listing.", verify_result=None):
    return cascade.grade(llm, "Write a listing.", deliverable, RUBRIC, verify_result or {"len": True},
                         seed="t1|0", judge_model=None, timeout=5, profile=None, votes=1)


def test_policy_per_criterion_and_llm_sees_only_escalated_criteria():
    llm = FakeLlm(level=1)
    scale, llm_usage, detail, meta, jev_usage = _grade(_cascade(), llm)
    assert {c: m["final_by"] for c, m in meta.items()} == {"sure": "jev", "unsure": "llm", "legal": "llm", "len": "verify"}
    assert meta["unsure"]["reason"] == "uncertain" and meta["legal"]["reason"] == "rule:risk"
    assert scale == {"sure": 2, "unsure": 1, "legal": 1, "len": 2}
    assert len(llm.prompts) == 1
    criteria = json.loads(llm.prompts[0].split("```json")[-1].split("```")[0]) if "```json" in llm.prompts[0] else None
    sent = llm.prompts[0]
    assert '"unsure"' in sent and '"legal"' in sent and '"sure"' not in sent and '"len"' not in sent
    assert criteria is None or {c["id"] for c in criteria} == {"unsure", "legal"}
    assert llm_usage == {"input_tokens": 10} and jev_usage == {"input_tokens": 100}
    assert detail["sure"]["p2"] == 0.99


def test_no_llm_call_when_every_criterion_is_decided_without_it():
    rubric = {"dimensions": [{"id": "sure", "desc": "Output is complete."}]}
    llm = FakeLlm()
    scale, llm_usage, _d, meta, _j = _cascade().grade(llm, "p", "text", rubric, {}, seed="t1|0", judge_model=None,
                                                       timeout=5, profile=None, votes=1)
    assert llm.prompts == [] and llm_usage is None and scale == {"sure": 2} and meta["sure"]["final_by"] == "jev"


def test_jev_unavailable_falls_back_to_llm_for_everything():
    llm = FakeLlm(level=2)
    scale, _u, detail, meta, _j = _grade(_cascade(FakeJev(fail=True)), llm)
    assert detail is None and {m["reason"] for m in meta.values()} == {"unavailable"}
    assert scale == {"sure": 2, "unsure": 2, "legal": 2, "len": 2} and len(llm.prompts) == 1


def test_oversize_state_skips_jev():
    jev = FakeJev()
    _s, _u, _d, meta, _j = _grade(_cascade(jev), FakeLlm(), deliverable="x" * 6000)
    assert jev.calls == 0 and {m["reason"] for m in meta.values()} == {"oversize"}


def test_empty_deliverable_scores_zero_without_calls():
    jev, llm = FakeJev(), FakeLlm()
    scale, _u, _d, _m, _j = _grade(_cascade(jev), llm, deliverable="")
    assert scale == {"sure": 0, "unsure": 0, "legal": 0, "len": 0} and jev.calls == 0 and llm.prompts == []


def test_audit_slice_sends_confident_verdicts_to_llm():
    _s, _u, _d, meta, _j = _grade(_cascade(config={**CONFIG, "p_audit": 1.0}), FakeLlm())
    assert meta["sure"] == {"final_by": "llm", "reason": "audit", "certainty": pytest.approx(0.99)}


def test_store_threshold_is_used_per_judge():
    store = {**EMPTY_STORE, "judges": {"claude": {"unsure": {"status": "calibrated", "threshold": 0.6}}}}
    _s, _u, _d, meta, _j = _grade(_cascade(store=store), FakeLlm())
    assert meta["unsure"]["final_by"] == "jev"
    assert meta["sure"]["reason"] == "no_evidence"  # no entry for this judge: the user's LLM decides


def test_new_judge_or_pack_starts_with_the_llm_and_jev_in_the_background():
    llm = FakeLlm(level=1)
    scale, _u, detail, meta, _j = _grade(_cascade(store=EMPTY_STORE), llm)
    assert {m["final_by"] for c, m in meta.items() if c != "len"} == {"llm"}
    assert detail is not None  # JEV still graded: every verdict becomes evidence


def test_fact_criterion_jev_may_fail_but_not_pass():
    rubric = {"items": [{"id": "facts", "desc": "Traces to inputs.", "kind": "factual"}]}
    store = {**EMPTY_STORE, "judges": {"claude": {"facts": TRUSTED}}}
    for p2, final_by in ((0.01, "jev"), (0.99, "llm")):
        _s, _u, _d, meta, _j = _cascade(FakeJev(p2={"facts": p2}), store=store).grade(
            FakeLlm(), "p", "text", rubric, {}, seed="t1|0", judge_model=None, timeout=5, profile=None, votes=1)
        assert meta["facts"]["final_by"] == final_by


# ── runner touch points ──────────────────────────────────────────────


TASK = {"id": "t1", "prompt": "Write a listing.", "verifier": "rubric"}


def _grade_record(cascade=None):
    return runner._grade("skill", TASK, 0, "A short listing.", None, lambda t, o: {"len": True}, RUBRIC, 0.8,
                         FakeLlm(level=1), None, None, 5, False, 1, cascade=cascade)[1]


def test_grade_without_cascade_keeps_todays_record_shape():
    rec = _grade_record()
    assert "cascade" not in rec and "jev_usage" not in rec and "judge_detail" not in rec


def test_grade_with_cascade_records_final_levels_and_meta():
    rec = _grade_record(_cascade())
    assert rec["rubric_scale"] == {"sure": 2, "unsure": 1, "legal": 1}
    assert rec["applied_scale"] == {"len": 2}
    assert set(rec["cascade"]) == {"sure", "unsure", "legal", "len"} and rec["jev_usage"] == {"input_tokens": 100}


def test_grading_hash_changes_only_with_cascade():
    config = {"judge_votes": 1, "pairwise": True}
    plain = runner._grading(config)
    assert runner._grading(config, None) == plain
    assert runner._grading(config, cascade_fp=_cascade().fingerprint())["hash"] != plain["hash"]
    assert runner._baseline_grading_hash(config, None) == runner._baseline_grading_hash(config)
    assert runner._baseline_grading_hash(config, _cascade().fingerprint()) != runner._baseline_grading_hash(config)


def _pair_records():
    base = {"task_id": "t1", "run_index": 0, "judge_prompt_base": "p"}
    return {"skill": [{**base, "arm": "skill", "deliverable": "x", "output": "x"}],
            "no_skill": [{**base, "arm": "no_skill", "deliverable": "y", "output": "y"}]}


def test_pairwise_shadow_records_jev_without_changing_the_winner():
    comps = (("skill", "no_skill"),)
    plain, _ = runner._run_pairwise(comps, _pair_records(), RUBRIC, FakeLlm(), None, None, 5, 1)
    jev = FakeJev()
    shadow, _ = runner._run_pairwise(comps, _pair_records(), RUBRIC, FakeLlm(), None, None, 5, 1, cascade=_cascade(jev))
    pp_plain, pp_shadow = plain["skill_vs_no_skill"]["per_pair"][0], shadow["skill_vs_no_skill"]["per_pair"][0]
    assert pp_shadow["winner"] == pp_plain["winner"] and "jev_shadow" not in pp_plain
    assert len(pp_shadow["jev_shadow"]) == 2 and jev.pair_calls == 2
    down, _ = runner._run_pairwise(comps, _pair_records(), RUBRIC, FakeLlm(), None, None, 5, 1,
                                   cascade=_cascade(FakeJev(fail=True)))
    assert down["skill_vs_no_skill"]["per_pair"][0]["jev_shadow"] is None


def test_summary_counts():
    records = [_grade_record(_cascade()), _grade_record(_cascade())]
    s = _cascade().summary(records)
    assert s["verdicts"] == 8 and s["final_by"] == {"jev": 2, "llm": 4, "verify": 2}
    assert s["reasons"]["uncertain"] == 2 and s["escalation_rate"] == pytest.approx(0.5)
    assert s["llm_judge_calls"] == 2 and s["jev_input_tokens"] == 200 and s["judge_key"] == "claude"
    assert "judge_measured" not in s
    assert s["pairwise"] == "shadow"


def test_criterion_checks_feed_the_cascade_but_never_the_pass_gate():
    rubric = {"items": [{"id": "len", "desc": "Headline under 100 characters.", "kind": "count", "verify": "len"}]}
    task = {"id": "t1", "prompt": "p", "verifier": "both"}
    gate = lambda t, o: {"banned": True}  # noqa: E731  (the pack's verify(): a pass gate)
    cascade = cz.Cascade(FakeJev(p2={"len": 0.99}), "claude", STORE, CONFIG, frozenset({"len"}),
                         criterion_checks=lambda t, o: {"len": False})
    _passed, rec = runner._grade("skill", task, 0, "x", None, gate, rubric, 0.8, FakeLlm(), None, None, 5, False, 1,
                                 cascade=cascade)
    assert rec["det"] == {"banned": True}  # det (the gate) never includes criterion-only checks
    assert rec["cascade"]["len"] == {"final_by": "verify", "reason": "rule:count", "certainty": pytest.approx(0.99)}
    assert rec["rubric_scale"] == {"len": 0}
