"""Pull one JSON object out of an AI CLI reply (bare, fenced in ```json, or wrapped in prose)."""
import json
import re

_FENCED = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def extract_object(text: str) -> dict | None:
    text = text or ""
    candidates = [m.group(1) for m in _FENCED.finditer(text)] + [text[text.find("{"): text.rfind("}") + 1]]
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(data, dict):
            return data
    return None
