"""TODO: deterministic checks for <pack> evals — copy the banned-phrase /
grounding rules verbatim from this skill's own Rules section in its .md
body, so the check and the guidance never drift apart."""
import json
import re

# TODO: replace with this domain's actual banned phrases, copied from the
# skill body's own Rules section.
_BANNED_PHRASES: list[str] = []

# P1.4d task 7.0a: excludes a trailing sentence period or list comma from
# the match — the prior version of this pattern kept them, so "1998." or
# "289,000," never matched a same-numbered inputs value and every such
# number read as "invented".
_NUMBER = re.compile(r"\d+(?:,\d{3})*(?:\.\d+)?")


def _no_banned_phrases(output: str) -> bool:
    lower = output.lower()
    return not any(phrase in lower for phrase in _BANNED_PHRASES)


def _numbers_grounded(output: str, inputs: dict) -> bool:
    """Every number in the output must appear somewhere in the inputs —
    catches invented statistics, prices, or measurements. Delete this
    check if the pack's output legitimately computes new numbers from
    the inputs (e.g. a total) rather than only restating given ones."""
    inputs_text = json.dumps(inputs)
    for match in _NUMBER.findall(output):
        cleaned = match.replace(",", "")
        if len(cleaned) < 2:
            continue
        if cleaned not in inputs_text and match not in inputs_text:
            return False
    return True


def verify(task: dict, output: str) -> dict[str, bool]:
    return {
        "TODO_no_banned_phrases": _no_banned_phrases(output),
        "TODO_numbers_grounded": _numbers_grounded(output, task.get("inputs") or {}),
    }
