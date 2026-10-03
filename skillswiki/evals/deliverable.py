"""Deliverable extraction for the eval engine (P1.4d, founder decision 1).

Strips working scaffolding (drafts, audit notes, self-check lists) so every
check and judge call sees only the text a user would paste. Pack-agnostic.
Validated 2026-09-12 against 522 stored outputs across 4 packs — change the
label lists only with a fresh corpus audit (see the P1.4d plan, task 1.1).
"""
import re

VERSION = "d1"  # bump when a regex or label list changes; feeds grading.hash

# A label that selects the deliverable. Must match the WHOLE label text.
_FINAL_RE = re.compile(
    r"(?:final(?:[ \t]+(?:rewrite|rewritten|version|text|answer|output|draft|copy))?"
    r"|rewrite|rewritten(?:[ \t]+(?:text|version|copy))?"
    r"|revised(?:[ \t]+(?:text|version|copy))?|deliverable)$",
    re.IGNORECASE)

# Labels that open a working-notes section. Matched as a prefix of the label
# ("remaining patterns checked", "drafts" both count). Deliberately excludes
# note/notes/analysis/result/output/before/after: those are deliverable content
# in real pack outputs (e.g. "Note: the facts sheet did not include lot size").
PROCESS_LABELS = (
    "draft", "remaining patterns", "patterns checked", "changes made",
    "what changed", "self-check", "self-review", "verification checklist",
)

# A candidate label line, either:
#   (a) heading or bold marker + label text, optional colon, rest of line; or
#   (b) plain label text (letters/spaces only) immediately followed by a colon.
_CANDIDATE_RE = re.compile(
    r"^[ \t]*(?:#{1,6}[ \t]+|\*\*|__)(?P<m>[A-Za-z][^\n*_:#]{0,59})"
    r"(?:\*\*|__)?[ \t]*:?[ \t]*(?:\*\*|__)?[ \t]*(?P<mr>.*)$"
    r"|^[ \t]*(?P<p>[A-Za-z][A-Za-z '&/-]{0,39}?)[ \t]*:[ \t]*(?P<pr>.*)$",
    re.MULTILINE)


def _is_process(label: str) -> bool:
    lab = label.lower().strip()
    return any(lab == p or lab.startswith(p + " ") or lab.startswith(p + "s") for p in PROCESS_LABELS)


def _labels(output: str) -> list[tuple[str, str, re.Match, str]]:
    """(kind, label, match, same_line_rest) for every FINAL or PROCESS label line, in order."""
    found = []
    for m in _CANDIDATE_RE.finditer(output):
        marked = m.group("m") is not None
        label = (m.group("m") if marked else m.group("p")).strip()
        rest = (m.group("mr") if marked else m.group("pr")) or ""
        if _FINAL_RE.match(label):
            found.append(("final", label, m, rest))
        elif _is_process(label):
            found.append(("process", label, m, rest))
    return found


def extract(output: str) -> tuple[str, bool, str | None]:
    """Return (deliverable, paste_ready, reason).

    - No FINAL/PROCESS label: the whole output, paste_ready True.
    - A FINAL label exists: the section under the LAST one (up to the next label).
    - Only PROCESS labels: the text before the first label; if that is empty,
      the section under the last label.
    Any label found -> paste_ready False, reason "labels:<sorted lowercase labels>".
    """
    if not output or not output.strip():
        return "", True, None
    labels = _labels(output)
    if not labels:
        return output.strip(), True, None

    def section(i: int) -> str:
        _, _, m, rest = labels[i]
        end = labels[i + 1][2].start() if i + 1 < len(labels) else len(output)
        return (rest + "\n" + output[m.end():end]).strip()

    finals = [i for i, item in enumerate(labels) if item[0] == "final"]
    if finals:
        deliverable = section(finals[-1])
    else:
        deliverable = output[:labels[0][2].start()].strip() or section(len(labels) - 1)
    reason = "labels:" + ",".join(sorted({item[1].lower() for item in labels}))
    return deliverable, False, reason
