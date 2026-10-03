"""A fake AI CLI backend + a ready-made suite, so eval tests spend no tokens."""
import json
import re

from skillswiki.evals import suite

RUBRIC = {
    "dimensions": [
        {"id": "failure_mechanism", "desc": "Output keeps every fact from the draft and adds none."},
        {"id": "actionable_specificity", "desc": "Output is a finished email, ready to send."},
        {"id": "high_risk_blacklist", "desc": "Output uses plain words and no filler openers."},
    ],
    "items": [{"id": "has_next_step", "desc": "Ends with one clear next step.", "weight": 1}],
    "applied": [{"id": "short_sentences", "desc": "Prefers short sentences."}],
}
TASKS = [
    {"id": "t01", "skill": "email-polisher", "prompt": "Can you tidy this up before I send it?",
     "inputs": {"draft": "hi, so basically the report is late, sorry"}, "expected": None, "verifier": "rubric",
     "tags": ["implicit"]},
    {"id": "t02", "skill": "email-polisher", "prompt": "Make this reply to my landlord sound better.",
     "inputs": {"draft": "the heater broke again on monday"}, "expected": None, "verifier": "rubric",
     "tags": ["implicit"]},
]


def write_suite(slug: str = "email-polisher") -> None:
    suite.write(slug, [{**t, "skill": slug} for t in TASKS], RUBRIC)
    suite.set_status(slug, "checked")


class FakeBackend:
    """Executor answers echo whether the skill was in the prompt; the judge gives 2 to skill-arm outputs and 1
    otherwise; pairwise prefers the skill output."""
    name = "fake"

    def __init__(self):
        self.calls = 0

    def run(self, prompt, mcp_config, model, timeout=180, profile=None):
        self.calls += 1
        text = "SKILLED: Hi Sam, the report will be late. Next step: I send it Friday." \
            if "# Email polisher" in prompt else "plain: report late sorry"
        return {"text": text, "usage": {"input_tokens": 100, "output_tokens": 20}}

    def judge(self, prompt, model, timeout=60, profile=None):
        self.calls += 1
        if "Response A:" in prompt:
            a = prompt.split("<<<A", 1)[1].split("A>>>", 1)[0]
            winner = "A" if "SKILLED" in a else "B"
            return {"text": json.dumps({"winner": winner, "reason": "clearer"}), "usage": {"input_tokens": 50}}
        criteria = json.loads(re.search(r"\n(\[.*\])\n", prompt, re.DOTALL).group(1))
        level = 2 if "SKILLED" in prompt else 1
        return {"text": json.dumps({c["id"]: level for c in criteria}), "usage": {"input_tokens": 50}}
