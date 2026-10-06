import io
import json

import pytest
from helpers import install_fixture_skills

from skillswiki import discovery, hook, library


@pytest.fixture
def adopted(tmp_home):
    install_fixture_skills(tmp_home / "native")
    discovery.sync_db()
    library.adopt("email-polisher")


def _run(monkeypatch, capsys, payload: str) -> str:
    monkeypatch.setattr("sys.stdin", io.StringIO(payload))
    hook.main()
    return capsys.readouterr().out


def test_shortlist_becomes_additional_context(adopted, monkeypatch, capsys):
    out = _run(monkeypatch, capsys, json.dumps({"prompt": "please fix my email draft before I send it"}))
    data = json.loads(out)["hookSpecificOutput"]
    assert data["hookEventName"] == "UserPromptSubmit"
    assert "email-polisher" in data["additionalContext"]
    assert "Email polisher" not in data["additionalContext"]  # never the skill body


@pytest.mark.parametrize("prompt", ["short one", "/compact everything in the session now", "zzzz qqqq xxxx yyyy"])
def test_silent_cases(adopted, monkeypatch, capsys, prompt):
    assert _run(monkeypatch, capsys, json.dumps({"prompt": prompt})) == ""


def test_errors_never_block(adopted, monkeypatch, capsys):
    assert _run(monkeypatch, capsys, "not json at all") == ""
    monkeypatch.setattr("skillswiki.route.suggest", lambda r: (_ for _ in ()).throw(RuntimeError("boom")))
    assert _run(monkeypatch, capsys, json.dumps({"prompt": "please fix my email draft before I send it"})) == ""


def test_context_line_for_confident_pick():
    line = hook.context_line({"skill": "email-polisher", "confidence": 0.93, "shortlist": []})
    assert "email-polisher" in line and "0.93" in line


def test_cli_hook_is_fail_safe_with_broken_env(adopted, monkeypatch, capsys):
    from skillswiki import cli, paths
    (paths.home() / ".env").write_bytes(b"\xff\xfe broken \x00")
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"prompt": "please fix my email draft before I send it"})))
    assert cli.main(["hook"]) == 0
    out = capsys.readouterr()
    assert out.err == ""


def test_hook_passes_its_time_budget(adopted, monkeypatch, capsys):
    seen = {}
    monkeypatch.setattr("skillswiki.route.suggest", lambda r, budget_s=None: seen.update(b=budget_s) or {"shortlist": []})
    _run(monkeypatch, capsys, json.dumps({"prompt": "please fix my email draft before I send it"}))
    assert seen["b"] == hook.BUDGET_S


from skillswiki import cli, wiring_data

PROMPT = "please fix my email draft before I send it"


@pytest.mark.parametrize("agent,event", [("codex", "UserPromptSubmit"), ("gemini_cli", "BeforeAgent"),
                                         ("qwen_code", "UserPromptSubmit"), ("droid", "UserPromptSubmit")])
def test_claude_style_agents_get_their_event_name(adopted, agent, event):
    data = json.loads(hook.respond({"prompt": PROMPT}, agent))["hookSpecificOutput"]
    assert data["hookEventName"] == event and "email-polisher" in data["additionalContext"]


def test_antigravity_always_gets_the_standing_reminder(adopted):
    out = json.loads(hook.respond({"invocationNum": 3}, "antigravity"))
    assert out == {"injectSteps": [{"ephemeralMessage": wiring_data.RULES_TEXT}]}


def test_hermes_format_reads_top_level_or_extra(adopted):
    assert "email-polisher" in json.loads(hook.respond({"user_message": PROMPT}, "hermes"))["context"]
    assert "email-polisher" in json.loads(hook.respond({"extra": {"user_message": PROMPT}}, "hermes"))["context"]
    assert hook.respond({"user_message": "hi"}, "hermes") == ""


def test_cli_passes_the_agent_flag(adopted, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"prompt": PROMPT})))
    assert cli.main(["hook", "--agent", "gemini_cli"]) == 0
    assert json.loads(capsys.readouterr().out)["hookSpecificOutput"]["hookEventName"] == "BeforeAgent"


def test_unknown_agent_prints_nothing(adopted, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"prompt": PROMPT})))
    hook.main(["--agent", "nope"])
    assert capsys.readouterr().out == ""


def test_shortlist_hint_asks_the_agent_to_check_before_answering(adopted):
    """Phase 12: Claude Code ignored 'if one clearly fits ... otherwise proceed'; the hint now asks it to open the
    top match and judge, without ordering it to use a skill that may be a keyword false positive."""
    line = hook.context_line({"shortlist": [{"skill": "email-polisher", "score": 3.0},
                                            {"skill": "csv-cleaner", "score": 1.0}]})
    assert "Before you answer" in line and "load_skill" in line and "email-polisher" in line
    assert "otherwise proceed without a skill" not in line
    assert "csv-cleaner" in line and "if it does not fit" in line
