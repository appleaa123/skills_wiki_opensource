"""Claude Code UserPromptSubmit hook: route every prompt and add a one-line suggestion to the context.

Input (stdin JSON): {"prompt": "...", ...}. Output: {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
"additionalContext": "..."}} (per https://code.claude.com/docs/en/hooks). Never prints the skill body, and never
blocks the user's prompt: any error prints nothing and exits 0.
"""
import json
import sys

MIN_PROMPT_CHARS = 15
SHORTLIST_SHOWN = 3
EVENT = "UserPromptSubmit"


def context_line(result: dict) -> str:
    """One line of advice for the model, or "" when nothing matched."""
    if result.get("skill"):
        return (f"Skills Wiki: skill \"{result['skill']}\" fits this request (confidence "
                f"{result.get('confidence', 0):.2f}). Load it with load_skill or `skillswiki load {result['skill']}`.")
    names = [i["skill"] for i in result.get("shortlist", [])[:SHORTLIST_SHOWN]]
    if names:
        return (f"Skills Wiki: possibly relevant skills: {', '.join(names)}. If one clearly fits, load it with "
                "load_skill or `skillswiki load <slug>`; otherwise proceed without a skill.")
    return ""


def respond(payload: dict) -> str:
    prompt = (payload.get("prompt") or "").strip()
    if len(prompt) < MIN_PROMPT_CHARS or prompt.startswith("/"):
        return ""
    from skillswiki.route import suggest
    line = context_line(suggest(prompt))
    if not line:
        return ""
    return json.dumps({"hookSpecificOutput": {"hookEventName": EVENT, "additionalContext": line}})


def main() -> None:
    try:
        out = respond(json.loads(sys.stdin.read() or "{}"))
    except Exception:
        out = ""
    if out:
        print(out)
