"""Per-prompt hook for every agent that has one: route the prompt and add one line of advice to the model's context,
in the reply format that agent expects (wiring_data hook_format). Formats per the agents' docs; see
plan/research_agent_wiring.md. Never prints the skill body and never blocks the prompt: any error prints nothing and
exits 0. Without --agent it behaves exactly as before (Claude Code, https://code.claude.com/docs/en/hooks).
"""
import json
import sys

MIN_PROMPT_CHARS = 15
BUDGET_S = 5.0  # well inside every agent's hook timeout; past it, keyword routing answers
SHORTLIST_SHOWN = 3
EVENT = "UserPromptSubmit"
DEFAULT_AGENT = "claude_code"


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


def _prompt(fmt: str, payload: dict) -> str:
    if fmt == "hermes":  # shell hooks may carry it under "extra"
        return payload.get("user_message") or (payload.get("extra") or {}).get("user_message") or ""
    return payload.get("prompt") or ""


def _reply(fmt: str, event: str, line: str) -> str:
    if not line:
        return ""
    if fmt == "hermes":
        return json.dumps({"context": line})
    return json.dumps({"hookSpecificOutput": {"hookEventName": event, "additionalContext": line}})


def respond(payload: dict, agent: str = DEFAULT_AGENT) -> str:
    from skillswiki import wiring_data
    w = wiring_data.by_key(agent)
    fmt = w.hook_format or "claude"
    if fmt == "antigravity":  # its hook never sees the prompt: inject the standing reminder instead
        return json.dumps({"injectSteps": [{"ephemeralMessage": wiring_data.RULES_TEXT}]})
    event = (w.hook.detail.get("keys") or [EVENT])[-1] if w.hook else EVENT
    prompt = _prompt(fmt, payload).strip()
    if len(prompt) < MIN_PROMPT_CHARS or prompt.startswith("/"):
        return _reply(fmt, event, "")
    from skillswiki.route import suggest
    return _reply(fmt, event, context_line(suggest(prompt, budget_s=BUDGET_S)))


def agent_from(argv: list[str]) -> str:
    if "--agent" in argv and argv.index("--agent") + 1 < len(argv):
        return argv[argv.index("--agent") + 1]
    return DEFAULT_AGENT


def main(argv: list[str] | None = None) -> None:
    try:
        from skillswiki import paths
        paths.load_env()
        out = respond(json.loads(sys.stdin.read() or "{}"), agent_from(argv or []))
    except Exception:
        out = ""
    if out:
        print(out)
