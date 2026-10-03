"""JEV structured judge.

Unlike the CLI backends this is not a prompt-in/text-out judge: JEV answers
typed questions with calibrated probabilities, and the study needs those
per criterion. runner.py dispatches on `structured = True` to
`judge_rubric` / `judge_pair` instead of `judge()`.

All vendor traffic goes through skillswiki/decision; this file only speaks
the vendor-neutral DecisionBackend dataclasses. The question wording below
is the wording Skills Wiki's agreement study measured; rubric text is
always used verbatim.
"""
from skillswiki.decision import DecisionUnavailable, get_decision_backend
from skillswiki.decision.base import ChoiceA, ChoiceQ, ScoreA, ScoreQ
from skillswiki.decision.null import NullBackend

from . import Backend, BackendUnavailable

INSTRUCTIONS = "How well does the deliverable satisfy this criterion? Criterion: {desc}"
# Mirrors the LLM judge's own 0/1/2 scale (runner._JUDGE_PROMPT_TEMPLATE).
LEVELS = [
    "The deliverable does not satisfy the criterion.",
    "The deliverable satisfies the criterion only in part.",
    "The deliverable fully satisfies the criterion.",
]
MAX_LEVEL = len(LEVELS) - 1
PAIR_KEY = "better_response"
PAIR_INSTRUCTIONS = (
    "Which response better serves the person who asked, judged only on the deliverable text "
    "against these criteria?\n{criteria}\nDo not reward length or formatting."
)
PAIR_OPTIONS = {"A": "Response A is the better deliverable.", "B": "Response B is the better deliverable."}


def _criteria(rubric: dict, applied: bool = True) -> list[dict]:
    groups = rubric.get("dimensions", []) + rubric.get("items", []) + (rubric.get("applied", []) if applied else [])
    return [{"id": c["id"], "desc": c.get("desc", ""), **({"levels": c["levels"]} if c.get("levels") else {})}
            for c in groups]


P2_PASS = 0.5  # human-approved pass rule, 2026-09-25


def _nearest_level(score: float) -> int:
    # int(x + 0.5), not round(): round() is half-to-even (round(0.5) == 0).
    return max(0, min(MAX_LEVEL, int(score + 0.5)))


def _pass_rule_level(probs: dict[str, float]) -> int:
    """Level 2 iff P(level 2) >= 0.5; otherwise the likelier of 0/1 (tie -> 0,
    fail-closed). Unlike nearest-level, a 0/2 split such as {0: .3, 2: .7}
    (expected score 1.4) counts as the pass JEV actually leans towards."""
    p = {int(k): v for k, v in probs.items()}
    if p.get(MAX_LEVEL, 0.0) >= P2_PASS:
        return MAX_LEVEL
    return 1 if p.get(1, 0.0) > p.get(0, 0.0) else 0


def _detail(answer: ScoreA) -> dict:
    return {
        "score": answer.score,
        "level": _pass_rule_level(answer.probabilities),
        "nearest": _nearest_level(answer.score),
        "argmax": int(max(answer.probabilities, key=answer.probabilities.get)),
        "p2": float(answer.probabilities.get(str(MAX_LEVEL), 0.0)),
        "confidence": answer.confidence,
        "probabilities": dict(answer.probabilities),
    }


class JevJudge(Backend):
    name = "jev"
    structured = True

    def __init__(self, decision_backend=None):
        # Never checks env or raises: the decision backend is resolved on first use.
        self._decision = decision_backend

    @staticmethod
    def _levels(criterion: dict) -> list:
        """The criterion's own 0/1/2 wording (rubric `levels`, evals/SPEC.md rule 6), else the generic scale."""
        return list(criterion.get("levels") or LEVELS)

    def _backend(self):
        backend = self._decision or get_decision_backend()
        if isinstance(backend, NullBackend):
            raise BackendUnavailable(
                "JEV judge disabled: set your own TYPESAFE_API_KEY (see .env.example)"
            )
        return backend

    def _decide(self, state: dict, questions: dict):
        try:
            return self._backend().decide(state, questions)
        except DecisionUnavailable as exc:
            raise BackendUnavailable(f"JEV unavailable: {exc}") from exc

    def run(self, prompt, mcp_config, model, timeout=180, profile=None) -> dict:
        raise BackendUnavailable("jev is a judge only; it cannot run an executor arm")

    def judge(self, prompt, model, timeout=60, profile=None) -> dict:
        raise BackendUnavailable("jev is a structured judge; runner dispatches to judge_rubric/judge_pair")

    def judge_rubric(self, task_prompt: str, deliverable: str, rubric: dict) -> dict:
        """One decide() per output: one 3-level ScoreQ per dimension, item and
        applied rule. Returns {"scale", "detail", "usage"}; an empty
        deliverable scores all zeros without a call (mirrors runner._judge)."""
        criteria = _criteria(rubric)
        if not deliverable:
            return {"scale": {c["id"]: 0 for c in criteria}, "detail": None, "usage": None}
        questions = {
            c["id"]: ScoreQ(instructions=INSTRUCTIONS.format(desc=c["desc"]), criteria=self._levels(c))
            for c in criteria
        }
        result = self._decide({"task_prompt": task_prompt, "deliverable": deliverable}, questions)
        detail = {}
        for cid in questions:
            answer = result.answers.get(cid)
            if not isinstance(answer, ScoreA):
                raise BackendUnavailable(f"JEV returned no score answer for criterion {cid!r}")
            detail[cid] = _detail(answer)
        return {
            "scale": {cid: d["level"] for cid, d in detail.items()},
            "detail": detail,
            "usage": {"input_tokens": result.input_tokens},
        }

    def judge_pair(self, task_prompt: str, response_a: str, response_b: str, rubric: dict) -> dict:
        """One ChoiceQ in one fixed order; runner._judge_pairwise owns the
        order swap. `applied` rules never go to pairwise (runner parity)."""
        bullets = "\n".join(f"- {c['desc']}" for c in _criteria(rubric, applied=False))
        question = ChoiceQ(instructions=PAIR_INSTRUCTIONS.format(criteria=bullets), criteria=dict(PAIR_OPTIONS))
        state = {"task_prompt": task_prompt, "response_a": response_a, "response_b": response_b}
        result = self._decide(state, {PAIR_KEY: question})
        answer = result.answers.get(PAIR_KEY)
        if not isinstance(answer, ChoiceA) or answer.choice not in PAIR_OPTIONS:
            raise BackendUnavailable(f"JEV returned no A/B choice: {answer!r}")
        return {
            "winner": answer.choice,
            "detail": {
                "p_a": answer.probabilities.get("A", 0.0),
                "p_b": answer.probabilities.get("B", 0.0),
                "confidence": answer.confidence,
            },
            "usage": {"input_tokens": result.input_tokens},
        }
