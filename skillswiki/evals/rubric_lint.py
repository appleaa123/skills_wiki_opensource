"""Lint a suite's rubric.json criterion properties (JEV Phase 2, P2.0b).

The JEV cascade judge decides by
criterion PROPERTY, never by pack or criterion name, so each criterion may
carry: `kind` (factual | constraint | count | style | legal; default style),
`risk` (low | high; default low), `verify` (a check id returned by the pack's
verify.py) and `levels` (3 strings for 0/1/2). This linter checks those fields
and suggests `kind` from the wording, at authoring time. Stdlib only.

Run through `skillswiki eval check <slug>` (skillswiki/evals/suite_check.py).
"""
import importlib.util
import json
import re
from dataclasses import dataclass
from pathlib import Path

from skillswiki.evals import suite

KINDS = frozenset({"factual", "constraint", "count", "style", "legal"})
RISKS = frozenset({"low", "high"})
LEVEL_COUNT = 3
_UNITS = r"(characters?|words?|lines?|paragraphs?|sentences?|bullets?)"
# A number (or one/two/three) that directly measures a unit, at most two words between:
# "under 100 characters", "2-4 short paragraphs"; not "a one-line closer".
_COUNT = re.compile(rf"(?<![\w-])(\d+\s*[-–]\s*\d+|\d+|one|two|three)\s+(\w+\s+){{0,2}}{_UNITS}\b", re.IGNORECASE)
# General harm vocabulary (never pack names): wording that MAY make a criterion legal or high-risk.
# A person decides; the linter only asks. Tagging it kind "legal" or risk "high" silences the warning.
_RISK_WORDS = re.compile(
    r"\b(fair housing|discriminat\w*|familial status|religion|national origin|disabilit\w*|medical|diagnos\w*|"
    r"health|legal advice|lawsuit|financial advice|investment advice|guarantee\w*|complian\w*|privacy|"
    r"personal data|safety)\b", re.IGNORECASE)
_NEGATED = re.compile(
    r"^\s*(output\s+)?(no|never|does not|doesn't|none of|avoids|contains no|invents no|adds no|without)\b",
    re.IGNORECASE)


@dataclass(frozen=True)
class Issue:
    level: str  # "error" | "warn"
    criterion: str
    message: str


def infer_kind(desc: str) -> str | None:
    """A suggestion from wording only: count or constraint, else None (no opinion)."""
    if _COUNT.search(desc):
        return "count"
    if _NEGATED.search(desc):
        return "constraint"
    return None


def _criteria(rubric: dict) -> list[dict]:
    return rubric.get("dimensions", []) + rubric.get("items", []) + rubric.get("applied", [])


def _lint_criterion(c: dict, verify_ids: set | None, strict_ids: set) -> list[Issue]:
    cid, issues = c["id"], []
    kind, risk, check, levels = c.get("kind"), c.get("risk"), c.get("verify"), c.get("levels")
    if kind is not None and kind not in KINDS:
        issues.append(Issue("error", cid, f"unknown kind {kind!r} (allowed: {sorted(KINDS)})"))
    if risk is not None and risk not in RISKS:
        issues.append(Issue("error", cid, f"unknown risk {risk!r} (allowed: {sorted(RISKS)})"))
    if check is not None:
        if verify_ids is None:
            issues.append(Issue("error", cid, f"verify {check!r} set but the pack has no verify.py"))
        elif check not in verify_ids:
            issues.append(Issue("error", cid, f"verify {check!r} is not a check returned by verify.py"))
    if levels is not None and not (isinstance(levels, list) and len(levels) == LEVEL_COUNT
                                   and all(isinstance(x, str) and x for x in levels)):
        issues.append(Issue("error", cid, f"levels must be {LEVEL_COUNT} non-empty strings (0/1/2)"))
    inferred = infer_kind(c.get("desc", ""))
    if inferred and inferred != kind:
        issues.append(Issue("warn", cid, f"wording suggests kind {inferred!r}; declared {kind!r}"))
    if kind != "legal" and risk != "high" and _RISK_WORDS.search(c.get("desc", "")):
        issues.append(Issue("warn", cid, "wording suggests a possible legal or high-risk rule: confirm, or tag "
                                         "kind \"legal\" / risk \"high\" so JEV never decides it"))
    if kind == "count" and check not in strict_ids:
        issues.append(Issue("warn", cid, "count criterion without a strict verify check: the LLM judge decides it"))
    return issues


def lint_rubric(rubric: dict, verify_ids: set | None, strict_ids: set) -> list[Issue]:
    return [i for c in _criteria(rubric) for i in _lint_criterion(c, verify_ids, strict_ids)]


def load_checks(path: Path):
    """(verify, criterion_checks, STRICT) from a pack's verify.py; (None, None, set()) without one.
    verify() checks are pass/fail gates in every run; criterion_checks() (optional) only feed rubric
    criteria in the JEV cascade."""
    if not path.exists():
        return None, None, set()
    spec = importlib.util.spec_from_file_location(f"_checks_{path.parent.parent.name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.verify, getattr(module, "criterion_checks", None), set(getattr(module, "STRICT", set()))


def _verify_info(path: Path) -> tuple[set | None, set]:
    """(every check id a criterion may link to, the STRICT set); (None, set()) without a verify.py."""
    verify, criterion, strict = load_checks(path)
    if verify is None:
        return None, set()
    ids = set(verify({"inputs": {}}, "")) | (set(criterion({"inputs": {}}, "")) if criterion else set())
    return ids, strict


def lint_suite(slug: str) -> list[Issue]:
    folder = suite.suite_dir(slug)
    rubric = json.loads((folder / "rubric.json").read_text(encoding="utf-8"))
    verify_ids, strict_ids = _verify_info(folder / "verify.py")
    return lint_rubric(rubric, verify_ids, strict_ids)
