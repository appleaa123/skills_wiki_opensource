"""Read a skill folder's SKILL.md: frontmatter `name` + `description`, and the body.

The description is the skill's own trigger text, the main routing input. Stdlib only (no PyYAML): supports the
subset agent skills use — top-level `key: value` scalars, quoted strings and `>` / `|` block scalars. Nested
structures and lists are skipped, never raised on. A missing description falls back to the first content line.
"""
import re
from pathlib import Path

SKILL_FILE = "SKILL.md"
_FENCE = "---"
_KEY = re.compile(r"^([A-Za-z0-9_-]+):\s*(.*)$")
_BLOCK = re.compile(r"^[>|][+-]?$")
_FALLBACK_MAX_CHARS = 200


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        return value[1:-1]
    return value


def _parse(lines: list[str]) -> dict:
    data: dict[str, str] = {}
    i = 0
    while i < len(lines):
        line = lines[i]
        i += 1
        match = _KEY.match(line)  # top-level keys only: an indented line never matches
        if not match:
            continue
        key, value = match.group(1), match.group(2).strip()
        if _BLOCK.match(value):
            block = []
            while i < len(lines) and (not lines[i].strip() or lines[i][:1] in " \t"):
                block.append(lines[i].strip())
                i += 1
            data[key] = " ".join(part for part in block if part)
        else:
            data[key] = _unquote(value.split(" #", 1)[0].strip())
    return data


def split(text: str) -> tuple[dict, str]:
    """(frontmatter dict, body). No frontmatter (or no closing fence) → ({}, text)."""
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != _FENCE:
        return {}, text
    for end in range(1, len(lines)):
        if lines[end].strip() == _FENCE:
            fm_lines = [ln.rstrip("\n") for ln in lines[1:end]]
            return _parse(fm_lines), "".join(lines[end + 1:])
    return {}, text


def _first_content_line(body: str) -> str:
    for line in body.splitlines():
        line = line.strip()
        if line and not line.startswith(("#", _FENCE, ">")):
            return line[:_FALLBACK_MAX_CHARS]
    return ""


def parse_skill(folder: Path) -> dict:
    """{"slug", "name", "description", "body"} for a folder containing SKILL.md."""
    folder = Path(folder)
    text = (folder / SKILL_FILE).read_text(encoding="utf-8", errors="replace")
    fm, body = split(text)
    description = " ".join((fm.get("description") or "").split()) or _first_content_line(body)
    return {"slug": folder.name, "name": fm.get("name") or folder.name, "description": description,
            "body": body.lstrip("\n")}
