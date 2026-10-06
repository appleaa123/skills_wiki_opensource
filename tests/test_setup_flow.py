from pathlib import Path

import pytest
from helpers import scripted, snapshot, two_agents

from skillswiki import backup, setup_flow, wiring
from skillswiki.setup_flow import AUTOMATIC


@pytest.fixture(autouse=True)
def no_real_agent_clis(monkeypatch):
    monkeypatch.setattr(wiring, "_which", lambda name: None)  # never run a real `claude mcp add` in tests


def test_dry_run_explains_everything_and_changes_nothing(tmp_home):
    home = two_agents()
    before = snapshot(home)
    io, said = scripted([])
    result = setup_flow.run(io, dry_run=True)
    text = "\n".join(said)
    assert "2 skills will move" in text and "Claude Code" in text and "Codex" in text
    assert "hook → rules file" in text and "puts every skill back" in text and "Dry run" in text
    assert snapshot(home) == before and backup.list_all() == [] and result["moved"] is None


def test_guided_decline_moves_nothing(tmp_home):
    home = two_agents()
    io, said = scripted(["g", "n"])
    result = setup_flow.run(io)
    assert result["stopped"] and (home / ".claude/skills/email-polisher").is_dir()
    assert "You chose not to continue." in "\n".join(said)


def test_eof_stops_cleanly(tmp_home):
    home = two_agents()
    io, said = scripted(["g"])  # input ends at the "go ahead?" question
    result = setup_flow.run(io)
    assert result["stopped"] and "Stopped" in said[-1] and (home / ".claude/skills/email-polisher").is_dir()


def test_automatic_moves_everything_with_a_backup(tmp_home):
    home = two_agents()
    io, said = scripted([])
    result = setup_flow.run(io, mode=AUTOMATIC)
    assert not result["stopped"] and len(result["moved"]["adopted"]) == 2
    assert not (home / ".claude/skills/email-polisher").exists() and Path(result["backup"]).is_file()
    io, said = scripted([])
    setup_flow.run(io, mode=AUTOMATIC)
    assert "No new skills to move" in "\n".join(said)


import json

from skillswiki import cards


def claude_hook_commands(home: Path) -> list[str]:
    settings = home / ".claude" / "settings.json"
    if not settings.exists():
        return []
    groups = json.loads(settings.read_text(encoding="utf-8")).get("hooks", {}).get("UserPromptSubmit", [])
    return [h["command"] for g in groups for h in g["hooks"]]


def test_automatic_wires_every_detected_agent_untested(tmp_home):
    home = two_agents()
    io, said = scripted([])
    result = setup_flow.run(io, mode=AUTOMATIC)
    assert {o["agent"]: o["test"] for o in result["agents"]} == {"claude_code": "untested", "codex": "untested"}
    assert claude_hook_commands(home) == ["skillswiki hook --agent claude_code"]
    assert "not tested yet" in said[-1] and "setup --test codex" in said[-1]


def test_guided_yes_marks_tested(tmp_home):
    two_agents()
    io, said = scripted(["g", "y", "y", "y", "y", "y"])  # go; claude: wire, works; codex: wire, works
    result = setup_flow.run(io)
    assert [o["test"] for o in result["agents"]] == ["tested_ok", "tested_ok"]
    assert any("restart it" in s for s in said)


def test_failed_hook_falls_back_to_rules_then_advice(tmp_home):
    home = two_agents()
    # go; claude: hook yes/no-test, rules yes/no-test → advice; codex: wire, works
    io, said = scripted(["g", "y", "y", "n", "y", "n", "y", "y"])
    result = setup_flow.run(io)
    assert result["agents"][0]["method"] == "advice" and wiring.get_row("claude_code")["test"] == "failed"
    assert claude_hook_commands(home) == [] and not (home / ".claude" / "CLAUDE.md").exists()
    assert any("check Skills Wiki" in s for s in said)


def test_test_prompt_prefers_a_card_example(tmp_home):
    two_agents()
    setup_flow.run(scripted([])[0], mode=AUTOMATIC)
    # the fixture's quoted YAML description parses without quotes (checked with frontmatter.split, 2026-10-06)
    assert setup_flow.test_prompt() == ("csv-cleaner", "Help me with this: Cleans messy CSV files: trims, dedupes, normalises dates")
    cards.set_manual("email-polisher", ["Polish this email to my landlord"], [], [])
    assert setup_flow.test_prompt() == ("email-polisher", "Polish this email to my landlord")


def test_second_run_adds_nothing(tmp_home):
    home = two_agents()
    setup_flow.run(scripted([])[0], mode=AUTOMATIC)
    first = snapshot(home)
    setup_flow.run(scripted([])[0], mode=AUTOMATIC)
    assert snapshot(home) == first


def test_warns_when_not_on_path(tmp_home, monkeypatch):
    two_agents()
    monkeypatch.setattr(setup_flow, "_on_path", lambda: False)
    io, said = scripted([])
    setup_flow.run(io, mode=AUTOMATIC)
    assert any("not on your PATH" in s for s in said)


def test_run_tests_later(tmp_home):
    two_agents()
    setup_flow.run(scripted([])[0], mode=AUTOMATIC)
    io, said = scripted(["y"])
    [outcome] = setup_flow.run_tests(io, "codex")
    assert outcome["test"] == "tested_ok" and wiring.get_row("codex")["test"] == "tested_ok"
    assert setup_flow.run_tests(scripted([])[0], "cursor") == []


def test_ctrl_c_during_the_move_stops_cleanly(tmp_home, monkeypatch):
    """Review #3: Ctrl-C outside a question still ends with a stopped summary."""
    two_agents()
    monkeypatch.setattr(setup_flow.library, "adopt_all",
                        lambda **kw: (_ for _ in ()).throw(KeyboardInterrupt()) if not kw.get("dry_run") else
                        {"adopted": [], "failed": []})
    io, said = scripted([])
    assert setup_flow.run(io, mode=AUTOMATIC)["stopped"] and "Stopped" in said[-1]


def test_one_broken_agent_does_not_stop_the_others(tmp_home, monkeypatch):
    """Review #3: an unexpected error is reported for that agent; the rest are still connected."""
    two_agents()
    real_apply = wiring.apply

    def flaky(key, method, zip_path=None):
        if key == "claude_code":
            raise OSError("disk full")
        return real_apply(key, method, zip_path)
    monkeypatch.setattr(setup_flow.wiring, "apply", flaky)
    io, said = scripted([])
    result = setup_flow.run(io, mode=AUTOMATIC)
    assert [o["method"] for o in result["agents"]] == ["error", "hook"]
    assert any("disk full" in s for s in said)


def test_a_failed_agent_is_offered_again_on_the_next_run(tmp_home):
    """Review #5: an agent that ended in advice can be retried."""
    two_agents()
    setup_flow.run(scripted(["g", "y", "y", "n", "y", "n", "y", "y"])[0])  # claude ends in advice
    assert wiring.get_row("claude_code")["test"] == "failed"
    io, _ = scripted(["g", "y", "y", "y"])  # go; claude: wire hook, works
    result = setup_flow.run(io)
    assert result["agents"][0]["agent"] == "claude_code" and result["agents"][0]["test"] == "tested_ok"
    assert wiring.get_row("claude_code")["method"] == "hook"
