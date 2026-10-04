"""Draft an eval suite (tasks.jsonl + rubric.json) from an adopted skill's SKILL.md, using the user's own AI CLI.

The prompt (prompts/generate_suite.md) carries the grading-v2 lessons: prompts a real user would type, never
restating the skill's rules (a restating prompt lets the no-skill arm score like the skill arm, so the benchmark
measures nothing), at least two `implicit` tasks, affirmative criteria. The result is a DRAFT for the user to
review; no verify.py is generated (deterministic checks are hand-written if wanted).
"""
import re

from skillswiki import paths, usage
from skillswiki.evals import rubric_lint, suite
from skillswiki.evals.backends import get_backend
from skillswiki.textjson import extract_object

SKILL_TEXT_MAX_CHARS = 20000
GENERATE_TIMEOUT_S = 600
MANDATORY_DIMENSIONS = ("failure_mechanism", "actionable_specificity", "high_risk_blacklist")
VERIFIERS = frozenset({"rubric", "both", "deterministic"})
# Legal, medical, financial and safety words on top of the linter's vocabulary: a "style" tag on a criterion that
# mentions any of them is undone. A word list, so it can miss phrasings: review generated kinds before trusting them.
_EXTRA_RISK_WORDS = re.compile(
    r"\b(payments?|paid|pay|money|invoices?|refunds?|pric(e|es|ing)|billing|tax(es)?|loans?|credit|insurance|"
    r"salary|wages?|fees?|interest rates?|debt|mortgages?|invest(ment|ments|ing|ors?)?|lawyers?|attorneys?|contracts?|liabilit\w*|"
    r"warrant(y|ies)|lawsuits?|court|doctors?|physicians?|nurses?|drugs?|doses?|dosage|medication|prescriptions?|"
    r"allerg\w*|symptoms?|emergenc\w*|injur\w*|harms?|harmful|self-harm|suicid\w*|danger\w*|hazard\w*)\b",
    re.IGNORECASE)
RETRY_SUFFIX = "\n\nReturn only the JSON object."


def _prompt(slug: str, skill_text: str) -> str:
    template = (paths.package_dir() / "evals" / "prompts" / "generate_suite.md").read_text(encoding="utf-8")
    return template.replace("<<SLUG>>", slug).replace("<<SKILL>>", skill_text[:SKILL_TEXT_MAX_CHARS])


def _criteria(rubric: dict) -> list:
    return list(rubric.get("dimensions") or []) + list(rubric.get("items") or []) + list(rubric.get("applied") or [])


def validate(slug: str, tasks, rubric, require_kind: bool = False) -> list[str]:
    """Schema errors (empty list = valid). `require_kind`: every criterion must carry a known `kind` (generated
    suites); hand-written suites may leave it out (untagged = treated as factual)."""
    errors = []
    if not isinstance(tasks, list) or not tasks:
        return ["tasks must be a non-empty list"]
    seen = set()
    for i, t in enumerate(tasks):
        where = f"task {i + 1}"
        if not isinstance(t, dict):
            errors.append(f"{where}: not an object")
            continue
        tid = t.get("id")
        if not isinstance(tid, str) or not tid or tid in seen:
            errors.append(f"{where}: id missing or duplicated")
        seen.add(tid)
        if t.get("skill") != slug:
            errors.append(f"{where}: skill must be {slug!r}, got {t.get('skill')!r}")
        if not isinstance(t.get("prompt"), str) or not t["prompt"].strip():
            errors.append(f"{where}: prompt missing")
        if not isinstance(t.get("inputs", {}), dict):
            errors.append(f"{where}: inputs must be an object")
        if t.get("verifier") not in VERIFIERS:
            errors.append(f"{where}: verifier must be one of {sorted(VERIFIERS)}")
        if not isinstance(t.get("tags", []), list):
            errors.append(f"{where}: tags must be a list")
    if not isinstance(rubric, dict):
        return errors + ["rubric must be an object"]
    dim_ids = {d.get("id") for d in rubric.get("dimensions") or [] if isinstance(d, dict)}
    for dim in MANDATORY_DIMENSIONS:
        if dim not in dim_ids:
            errors.append(f"rubric: missing mandatory dimension {dim!r}")
    ids = []
    for c in _criteria(rubric):
        if not isinstance(c, dict) or not isinstance(c.get("id"), str) or not isinstance(c.get("desc"), str):
            errors.append(f"rubric: every criterion needs string id and desc ({c!r:.80})")
            continue
        ids.append(c["id"])
        kind, risk = c.get("kind"), c.get("risk")
        if (require_kind or kind is not None) and kind not in rubric_lint.KINDS:
            errors.append(f"rubric: {c['id']} needs kind in {sorted(rubric_lint.KINDS)}, got {kind!r}")
        if risk is not None and risk not in rubric_lint.RISKS:
            errors.append(f"rubric: {c['id']} risk must be one of {sorted(rubric_lint.RISKS)}, got {risk!r}")
    if len(ids) != len(set(ids)):
        errors.append("rubric: criterion ids must be unique")
    return errors


def demote_risky_style(rubric: dict) -> list[str]:
    """Safety net: a "style" criterion whose wording sounds legal, medical, financial or safety-related is set
    back to "factual", so JEV can never pass it on its own. Returns the demoted ids (mutates `rubric`)."""
    demoted = []
    for c in _criteria(rubric):
        desc = c.get("desc", "")
        if c.get("kind") == "style" and (rubric_lint._RISK_WORDS.search(desc) or _EXTRA_RISK_WORDS.search(desc)):
            c["kind"] = "factual"
            demoted.append(c["id"])
    return demoted


def generate(slug: str, backend: str = "claude", overwrite: bool = False) -> dict:
    """Write a draft suite. Raises ValueError (suite exists, invalid reply twice) or BackendUnavailable."""
    if suite.exists(slug) and not overwrite:
        raise ValueError(f"a suite for {slug} already exists — edit it, or pass --overwrite to replace it")
    prompt = _prompt(slug, suite.skill_body(slug))
    cli = get_backend(backend)
    tokens, problems, reply = 0, [], ""
    for attempt in (prompt, prompt + RETRY_SUFFIX):
        response = cli.judge(attempt, None, timeout=GENERATE_TIMEOUT_S)
        reply = response["text"]
        tokens += sum(v for k, v in (response.get("usage") or {}).items() if k.endswith("tokens") and isinstance(v, int))
        data = extract_object(reply) or {}
        problems = validate(slug, data.get("tasks"), data.get("rubric"), require_kind=True)
        if not problems:
            break
    usage.log(slug, "eval", tokens)
    if problems:
        raise ValueError(f"{backend} did not return a valid suite: {'; '.join(problems[:5])}")
    demoted = demote_risky_style(data["rubric"])
    folder = suite.write(slug, data["tasks"], data["rubric"])
    suite.set_status(slug, "draft", generated_by=backend, demoted_to_factual=demoted)
    kinds = sorted({c["kind"] for c in _criteria(data["rubric"])})
    return {"slug": slug, "path": str(folder), "tasks": len(data["tasks"]), "tokens": tokens, "kinds": kinds,
            "demoted_to_factual": demoted}
