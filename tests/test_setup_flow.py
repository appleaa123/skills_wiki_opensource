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
