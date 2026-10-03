"""Check a suite before spending tokens on it.

Without a key: schema validation (blocking), the rubric linter, affirmative-wording and `implicit`-count rules,
and a deterministic restatement heuristic (a task prompt sharing >= RESTATE_SHARE of its words with one sentence of
the skill). Optional `llm=True` asks the user's own AI CLI whether each prompt restates the skill (user's tokens).
With the user's TypeSafe key, JEV also checks every prompt and criterion in one batched call (advisory and
uncalibrated: the flags show JEV's probability). Only schema errors block; everything else is a warning. A suite
with no blocking issue is marked "checked".
"""
import json
import re

from skillswiki import paths
from skillswiki.decision import DecisionUnavailable, get_decision_backend, jev_enabled
from skillswiki.decision.base import NoulQ
from skillswiki.evals import rubric_lint, suite
from skillswiki.evals.backends import get_backend
from skillswiki.evals.generator import _criteria, validate
from skillswiki.route.keyword import tokenize
from skillswiki.textjson import extract_object

RESTATE_SHARE = 0.4
RESTATE_MIN_TOKENS = 3
MIN_IMPLICIT = 2
LLM_TIMEOUT_S = 300
_SENTENCE = re.compile(r"[.!?\n]+")
JEV_SKILL_CHARS = 12000
JEV_FLAG_AT = 0.5
ADVISORY = "(advisory, uncalibrated)"
# (question key prefix, instructions, flag when P is above (True) or below (False) JEV_FLAG_AT, issue label)
JEV_TASK_QUESTIONS = (
    ("restates", "The prompt in state.tasks[{tid}] restates or instructs rules that appear in state.skill, instead of "
                 "asking for something the way a normal user would.", True, "jev_restatement"),
    ("realistic", "The prompt in state.tasks[{tid}] is a realistic request a user of this skill would type.", False,
     "jev_unrealistic"),
)
JEV_GRADEABLE = "A grader could decide this criterion from the output text alone: {desc}"

LLM_PROMPT = """Below is an AI skill and the test prompts of its evaluation suite. For each prompt, decide whether it
restates, paraphrases or hints at the skill's own rules, steps or style guidance (instead of asking for something
the way a normal user would). Return ONLY a JSON object: {"<task id>": {"restates": true|false, "reason": "<= 20
words"}, ...} with every task id.

SKILL:
<<SKILL>>

PROMPTS:
<<PROMPTS>>"""


def _issue(level: str, where: str, message: str) -> dict:
    return {"level": level, "where": where, "message": message}


def restatement_flags(tasks: list[dict], skill_text: str) -> list[dict]:
    sentences = [set(tokenize(s)) for s in _SENTENCE.split(skill_text)]
    issues = []
    for t in tasks:
        words = set(tokenize(t.get("prompt", "")))
        if len(words) < RESTATE_MIN_TOKENS:
            continue
        best = max((len(words & s) / len(words) for s in sentences if s), default=0.0)
        if best >= RESTATE_SHARE:
            issues.append(_issue("warn", t.get("id", "?"), f"possible_restatement: {best:.0%} of the prompt's words "
                                 "appear in one sentence of the skill — a no-skill answer may score as well"))
    return issues


def _llm_flags(tasks: list[dict], skill_text: str, backend: str) -> list[dict]:
    prompts = "\n".join(f"{t['id']}: {t['prompt']}" for t in tasks)
    reply = get_backend(backend).judge(LLM_PROMPT.replace("<<SKILL>>", skill_text[:20000]).replace(
        "<<PROMPTS>>", prompts), None, timeout=LLM_TIMEOUT_S)["text"]
    data = extract_object(reply) or {}
    return [_issue("warn", tid, f"llm_restatement: {(v or {}).get('reason', '')}")
            for tid, v in data.items() if isinstance(v, dict) and v.get("restates")]


def jev_flags(tasks: list[dict], rubric: dict, skill_text: str) -> tuple[list[dict], dict]:
    """(advisory issues, {"input_tokens", "usd"}) from one batched JEV call. Raises DecisionUnavailable."""
    questions, targets = {}, {}
    for t in tasks:
        for prefix, instructions, flag_high, label in JEV_TASK_QUESTIONS:
            key = f"{prefix}_{t['id']}"
            questions[key] = NoulQ(instructions.format(tid=t["id"]))
            targets[key] = (t["id"], flag_high, label)
    for c in _criteria(rubric):
        key = f"gradeable_{c['id']}"
        questions[key] = NoulQ(JEV_GRADEABLE.format(desc=c["desc"]))
        targets[key] = (c["id"], False, "jev_not_gradeable")
    state = {"skill": skill_text[:JEV_SKILL_CHARS], "tasks": {t["id"]: t["prompt"] for t in tasks}}
    result = get_decision_backend().decide(state, questions)
    issues = []
    for key, (where, flag_high, label) in targets.items():
        answer = result.answers.get(key)
        if answer is None:
            continue
        if (answer.noul > JEV_FLAG_AT) if flag_high else (answer.noul < JEV_FLAG_AT):
            issues.append(_issue("warn", where, f"{label}: P={answer.noul:.2f} {ADVISORY}"))
    config = json.loads((paths.package_dir() / "evals" / "config.json").read_text())
    usd = result.input_tokens * config["cascade"]["usd_per_million_jev_tokens"] / 1_000_000
    return issues, {"input_tokens": result.input_tokens, "usd": round(usd, 6)}


def check(slug: str, llm: bool = False, backend: str = "claude") -> dict:
    try:
        tasks = suite.load_tasks(slug)
        rubric = suite.load_rubric(slug)
    except (FileNotFoundError, ValueError) as exc:
        return {"ok": False, "issues": [_issue("error", "suite", str(exc))]}
    issues = [_issue("error", "schema", e) for e in validate(slug, tasks, rubric)]
    jev = None
    if not issues:
        issues += [_issue(i.level, i.criterion, i.message) for i in rubric_lint.lint_suite(slug)]
        issues += [_issue("warn", c["id"], "negative wording: phrase it as what a good output does (judges grade "
                          "negations more harshly)") for c in _criteria(rubric) if rubric_lint._NEGATED.search(c["desc"])]
        implicit = sum("implicit" in (t.get("tags") or []) for t in tasks)
        if implicit < MIN_IMPLICIT:
            issues.append(_issue("warn", "tasks", f"only {implicit} task(s) tagged implicit; at least {MIN_IMPLICIT} "
                                 "measure the skill's default behaviour"))
        if not (suite.suite_dir(slug) / "verify.py").exists() and any(t.get("verifier") != "rubric" for t in tasks):
            issues.append(_issue("warn", "tasks", "some tasks ask for deterministic checks but there is no verify.py"))
        skill_text = suite.skill_body(slug)
        issues += restatement_flags(tasks, skill_text)
        if llm:
            issues += _llm_flags(tasks, skill_text, backend)
        if jev_enabled():
            try:
                jev_issues, jev = jev_flags(tasks, rubric, skill_text)
                issues += jev_issues
            except DecisionUnavailable as exc:
                jev = {"unavailable": getattr(exc, "reason", "unavailable")}
    ok = not any(i["level"] == "error" for i in issues)
    if ok:
        suite.set_status(slug, "checked")
    result = {"ok": ok, "issues": issues}
    if jev is not None:
        result["jev"] = jev
    return result


def show(slug: str) -> str:
    tasks = suite.load_tasks(slug)
    rubric = suite.load_rubric(slug)
    lines = [f"Suite for {slug} ({suite.status(slug).get('status', 'unknown')}) at {suite.suite_dir(slug)}", "", "Tasks:"]
    lines += [f"  {t['id']} [{', '.join(t.get('tags') or [])}] {t['prompt']}" for t in tasks]
    lines += ["", "Rubric:"] + [f"  {c['id']} [{c.get('kind', 'untagged')}]: {c['desc']}" for c in _criteria(rubric)]
    return "\n".join(lines)
