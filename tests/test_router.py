"""Verifies JEV Phase 3 P3.3: core/decision/router.py — theme descent with a
beam, skill choice over the beam's leaves (entitlement-filtered), then a verify
pass with no-skill gates. Pure and vendor-neutral; a fake backend scripts the
probabilities. No JEV calls."""
import sys
from pathlib import Path

import pytest


from skillswiki.decision import router  # noqa: E402
from skillswiki.decision.base import ChoiceA, ChoiceQ, DecisionResult, DecisionUnavailable, NoulA, NoulQ  # noqa: E402


class FakeBackend:
    """Choice probabilities come from `weights` keyed by option TEXT (routing/theme line);
    Nouls: gates -> gate_p, candidate fit -> fit[skill line] (default 0.9)."""

    def __init__(self, weights, gate_p=0.9, fit=None, fail=False):
        self.weights, self.gate_p, self.fit, self.fail = weights, gate_p, fit or {}, fail
        self.calls = []

    def decide(self, state, questions):
        if self.fail:
            raise DecisionUnavailable("down")
        self.calls.append((state, questions))
        answers = {}
        for key, q in questions.items():
            if isinstance(q, ChoiceQ):
                raw = {opt: self.weights.get(text, 0.01) for opt, text in q.criteria.items()}
                total = sum(raw.values())
                probs = {k: v / total for k, v in raw.items()}
                best = max(probs, key=probs.get)
                answers[key] = ChoiceA(choice=best, probabilities=probs, confidence=probs[best])
            elif isinstance(q, NoulQ):
                if key.startswith("gate_"):
                    answers[key] = NoulA(noul=self.gate_p)
                else:
                    text = state["candidates"][key.removeprefix("fit_")]
                    answers[key] = NoulA(noul=self.fit.get(text.split("\n")[0], 0.9))
        return DecisionResult(answers=answers, input_tokens=100, model="fake")


def _catalog(tree=None):
    skills = {
        "p1/write-ad": {"line": "Write ad copy.", "nodes": ["mkt"], "lang": "en"},
        "p1/email": {"line": "Write marketing emails.", "nodes": ["mkt"], "lang": "en"},
        "p2/fix-bug": {"line": "Fix a code bug.", "nodes": ["eng"], "lang": "en"},
        "p3/map": {"line": "Make a map from GIS data.", "nodes": ["geo", "eng"], "lang": "en"},
    }
    tree = tree or [
        {"id": "mkt", "line": "Marketing work.", "examples": [], "children": None, "skills": ["p1/write-ad", "p1/email"]},
        {"id": "eng", "line": "Software engineering.", "examples": [], "children": None, "skills": ["p2/fix-bug", "p3/map"]},
        {"id": "geo", "line": "Maps and geography.", "examples": [], "children": None, "skills": ["p3/map"]},
    ]
    return {"version": 1, "tree": tree, "skills": skills, "overrides": {}}


def _options(backend, index):
    """Option texts of the Choice question in call `index`."""
    q = next(q for q in backend.calls[index][1].values() if isinstance(q, ChoiceQ))
    return set(q.criteria.values())


def test_suggests_the_best_skill_through_the_beam():
    b = FakeBackend({"Marketing work.": 0.7, "Software engineering.": 0.2, "Maps and geography.": 0.1,
                     "Write ad copy.": 0.8, "Write marketing emails.": 0.2})
    s = router.route("Write me a Facebook ad for my bakery.", _catalog(), b, router.DEFAULTS)
    assert (s.skill, s.reason) == ("p1/write-ad", "suggested")
    assert _options(b, 1) == {"Write ad copy.", "Write marketing emails."}  # 0.2/0.7 < beam_ratio: one leaf
    assert s.shortlist[0][0] == "p1/write-ad" and s.input_tokens == 300 and s.separation == pytest.approx(4.0)


def test_beam_keeps_a_close_second_theme_and_dedupes_multi_homed_skills():
    b = FakeBackend({"Software engineering.": 0.5, "Maps and geography.": 0.45, "Marketing work.": 0.05,
                     "Make a map from GIS data.": 0.9})
    s = router.route("Plot these GPS points on a map.", _catalog(), b, router.DEFAULTS)
    assert _options(b, 1) == {"Fix a code bug.", "Make a map from GIS data."}  # eng + geo, p3/map once
    assert s.skill == "p3/map" and len(s.path) == 2


def test_descends_into_child_themes():
    tree = [{"id": "work", "line": "Work tasks.", "examples": [], "skills": None, "children": [
                {"id": "mkt", "line": "Marketing work.", "examples": [], "children": None, "skills": ["p1/write-ad"]},
                {"id": "eng", "line": "Software engineering.", "examples": [], "children": None, "skills": ["p2/fix-bug"]}]},
            {"id": "geo", "line": "Maps and geography.", "examples": [], "children": None, "skills": ["p3/map"]}]
    b = FakeBackend({"Work tasks.": 0.9, "Maps and geography.": 0.1, "Software engineering.": 0.9, "Marketing work.": 0.1,
                     "Fix a code bug.": 1.0})
    s = router.route("My Python script crashes.", _catalog(tree), b, router.DEFAULTS)
    assert s.skill == "p2/fix-bug" and s.path[0] == ["work", "eng"]


def test_entitlement_filter_limits_candidates():
    b = FakeBackend({"Marketing work.": 0.9, "Write ad copy.": 0.9, "Write marketing emails.": 0.1})
    s = router.route("Write an email campaign.", _catalog(), b, router.DEFAULTS, allowed={"p2", "p3"})
    assert (s.skill, s.reason) == (None, "no_candidates")


def test_low_gates_mean_no_skill():
    b = FakeBackend({"Marketing work.": 0.9, "Write ad copy.": 0.9}, gate_p=0.1)
    s = router.route("What is 2 + 2?", _catalog(), b, router.DEFAULTS)
    assert (s.skill, s.reason) == (None, "gated") and s.gates and len(b.calls) == 1  # no further JEV calls


def test_low_fit_means_no_skill_but_keeps_the_shortlist():
    b = FakeBackend({"Marketing work.": 0.9, "Write ad copy.": 0.9}, fit={"Write ad copy.": 0.1, "Write marketing emails.": 0.2})
    s = router.route("Design a logo.", _catalog(), b, router.DEFAULTS)
    assert (s.skill, s.reason) == (None, "low_fit") and [sid for sid, _ in s.shortlist] == ["p1/write-ad", "p1/email"]


def test_override_line_is_what_jev_sees():
    cat = _catalog()
    cat["overrides"]["p1/write-ad"] = "Write paid social ads."
    b = FakeBackend({"Marketing work.": 0.9, "Write paid social ads.": 0.9})
    router.route("An Instagram ad please.", cat, b, router.DEFAULTS)
    assert "Write paid social ads." in _options(b, 1)


def test_too_many_options_is_a_catalog_error():
    many = {f"p/s{i}": {"line": f"Skill {i}.", "nodes": ["big"], "lang": "en"} for i in range(300)}
    tree = [{"id": "big", "line": "Everything.", "examples": [], "children": None, "skills": list(many)}]
    with pytest.raises(router.RoutingError):
        router.route("x", {"version": 1, "tree": tree, "skills": many, "overrides": {}}, FakeBackend({}), router.DEFAULTS)


def test_unavailable_backend_returns_no_suggestion():
    s = router.route("x", _catalog(), FakeBackend({}, fail=True), router.DEFAULTS)
    assert (s.skill, s.reason) == (None, "unavailable")


def test_describe_feeds_the_verify_pass():
    b = FakeBackend({"Marketing work.": 0.9, "Write ad copy.": 0.9})
    router.route("An ad.", _catalog(), b, router.DEFAULTS, describe=lambda sid: f"FULL TEXT OF {sid}")
    verify_state = b.calls[-1][0]
    assert any("FULL TEXT OF p1/write-ad" in text for text in verify_state["candidates"].values())


def test_identical_lines_are_one_option_and_come_back_as_equivalents():
    cat = _catalog()
    cat["skills"]["p9/ad-writer"] = {"line": "Write ad copy.", "nodes": ["mkt"], "lang": "en"}  # same line, other pack
    cat["tree"][0]["skills"].append("p9/ad-writer")
    b = FakeBackend({"Marketing work.": 0.9, "Write ad copy.": 0.9})
    s = router.route("Write an ad.", cat, b, router.DEFAULTS)
    q = next(q for q in b.calls[1][1].values() if isinstance(q, ChoiceQ))
    assert list(q.criteria.values()).count("Write ad copy.") == 1          # JEV sees one option
    assert s.skill == "p1/write-ad" and s.equivalents == ["p9/ad-writer"]  # the rest are equivalent, not hidden


def test_option_limit_counts_distinct_lines():
    same = {f"p/s{i}": {"line": "Same line.", "nodes": ["big"], "lang": "en"} for i in range(300)}
    tree = [{"id": "big", "line": "Everything.", "examples": [], "children": None, "skills": list(same)}]
    b = FakeBackend({"Everything.": 1.0, "Same line.": 1.0})
    s = router.route("x", {"version": 1, "tree": tree, "skills": same, "overrides": {}}, b, router.DEFAULTS)
    assert s.skill == "p/s0" and len(s.equivalents) == 299                  # 1 distinct option: no RoutingError


# ── scopes (B, 2026-09-28): tool/place-only skills need the request to involve their tool ──


class ScopeBackend(FakeBackend):
    """Scope Nouls ("scope_c<i>") answer from `involves` keyed by the scope name found in the question."""

    def __init__(self, weights, involves, **kw):
        super().__init__(weights, **kw)
        self.involves = involves

    def decide(self, state, questions):
        plain = {k: q for k, q in questions.items() if not k.startswith("scope_")}
        result = super().decide(state, plain)
        self.calls[-1] = (state, questions)  # record the full request, scope questions included
        for k, q in questions.items():
            if k.startswith("scope_"):
                tool = next(t for t in self.involves if t in q.instructions)
                result.answers[k] = NoulA(noul=self.involves[tool])
        return result


def _scoped():
    cat = _catalog()
    cat["scopes"] = {"p1/write-ad": "AcmeAds", "p1/email": None, "p2/fix-bug": None, "p3/map": None}
    return cat


WEIGHTS = {"Marketing work.": 0.8, "Software engineering.": 0.1, "Maps and geography.": 0.1,
           "For AcmeAds only: Write ad copy.": 0.7, "Write ad copy.": 0.7, "Write marketing emails.": 0.3}


def test_scopes_are_off_by_default():
    b = ScopeBackend(WEIGHTS, {"AcmeAds": 0.05})
    s = router.route("Write an ad for my bakery.", _scoped(), b, router.DEFAULTS)
    assert s.skill == "p1/write-ad"
    assert not any(k.startswith("scope_") for _state, qs in b.calls for k in qs)


def test_a_scoped_skill_loses_when_the_request_does_not_involve_its_tool():
    config = {**router.DEFAULTS, "use_scopes": True}
    b = ScopeBackend(WEIGHTS, {"AcmeAds": 0.05})
    s = router.route("Write an ad for my bakery.", _scoped(), b, config)
    assert "For AcmeAds only: Write ad copy." in _options(b, 1)
    assert s.skill == "p1/email"                      # the general skill wins
    assert dict(s.shortlist)["p1/write-ad"] == 0.05   # fit capped by the scope answer
    assert "named or clearly implied" in next(q.instructions for k, q in b.calls[2][1].items() if k.startswith("scope_"))


def test_a_scoped_skill_wins_when_the_request_involves_its_tool():
    config = {**router.DEFAULTS, "use_scopes": True}
    b = ScopeBackend(WEIGHTS, {"AcmeAds": 0.95})
    assert router.route("Write an AcmeAds ad for my bakery.", _scoped(), b, config).skill == "p1/write-ad"


# ── flat local library (Skills Wiki self-hosted) ──────────────────────


def _flat_catalog():
    return {"tree": [{"id": "all", "line": "All skills", "skills": ["p1/write-ad", "p1/email", "p2/fix-bug"]}],
            "skills": _catalog()["skills"], "overrides": {}}


def test_flat_tree_skips_theme_choice_but_asks_gates():
    b = FakeBackend({"Write ad copy.": 0.8, "Write marketing emails.": 0.1, "Fix a code bug.": 0.1})
    s = router.route("Write me a Facebook ad for my bakery.", _flat_catalog(), b, router.DEFAULTS)
    assert (s.skill, s.reason) == ("p1/write-ad", "suggested")
    first_call = b.calls[0][1]
    assert set(first_call) == set(router.GATES) and not any(isinstance(q, ChoiceQ) for q in first_call.values())
    assert s.path == [["all"]] and s.input_tokens == 300


def test_flat_tree_gates_can_still_say_no_skill():
    b = FakeBackend({}, gate_p=0.1)
    s = router.route("hi", _flat_catalog(), b, router.DEFAULTS)
    assert (s.skill, s.reason) == (None, "gated") and len(b.calls) == 1
