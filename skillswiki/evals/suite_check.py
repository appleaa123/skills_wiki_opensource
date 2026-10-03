"""Check a suite before spending tokens on it.

Without a key: schema validation (blocking), the rubric linter, affirmative-wording and `implicit`-count rules,
and a deterministic restatement heuristic (a task prompt sharing >= RESTATE_SHARE of its words with one sentence of
the skill). Optional `llm=True` asks the user's own AI CLI whether each prompt restates the skill (user's tokens).
Only schema errors block; everything else is a warning. A suite with no blocking issue is marked "checked".
"""
import re

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


def check(slug: str, llm: bool = False, backend: str = "claude") -> dict:
    folder = suite.suite_dir(slug)
    try:
        tasks = suite.load_tasks(slug)
        rubric = suite.load_rubric(slug)
    except (FileNotFoundError, ValueError) as exc:
        return {"ok": False, "issues": [_issue("error", "suite", str(exc))]}
    issues = [_issue("error", "schema", e) for e in validate(slug, tasks, rubric)]
    if not issues:
        issues += [_issue(i.level, i.criterion, i.message) for i in rubric_lint.lint_suite(slug)]
        issues += [_issue("warn", c["id"], "negative wording: phrase it as what a good output does (judges grade "
                          "negations more harshly)") for c in _criteria(rubric) if rubric_lint._NEGATED.search(c["desc"])]
        implicit = sum("implicit" in (t.get("tags") or []) for t in tasks)
        if implicit < MIN_IMPLICIT:
            issues.append(_issue("warn", "tasks", f"only {implicit} task(s) tagged implicit; at least {MIN_IMPLICIT} "
                                 "measure the skill's default behaviour"))
        if not (folder / "verify.py").exists() and any(t.get("verifier") != "rubric" for t in tasks):
            issues.append(_issue("warn", "tasks", "some tasks ask for deterministic checks but there is no verify.py"))
        skill_text = suite.skill_body(slug)
        issues += restatement_flags(tasks, skill_text)
        if llm:
            issues += _llm_flags(tasks, skill_text, backend)
    ok = not any(i["level"] == "error" for i in issues)
    if ok:
        suite.set_status(slug, "checked")
    return {"ok": ok, "issues": issues}


def show(slug: str) -> str:
    tasks = suite.load_tasks(slug)
    rubric = suite.load_rubric(slug)
    lines = [f"Suite for {slug} ({suite.status(slug).get('status', 'unknown')}) at {suite.suite_dir(slug)}", "", "Tasks:"]
    lines += [f"  {t['id']} [{', '.join(t.get('tags') or [])}] {t['prompt']}" for t in tasks]
    lines += ["", "Rubric:"] + [f"  {c['id']}: {c['desc']}" for c in _criteria(rubric)]
    return "\n".join(lines)
