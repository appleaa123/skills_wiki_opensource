from pathlib import Path

import pytest
from helpers import install_fixture_skills
from helpers import scripted, snapshot, two_agents

from skillswiki import backup, learnings, setup_flow, uninstall_flow, wiring
from skillswiki.setup_flow import AUTOMATIC


@pytest.fixture(autouse=True)
def no_real_agent_clis(monkeypatch):
    monkeypatch.setattr(wiring, "_which", lambda name: None)


def set_up() -> Path:
    home = two_agents()
    setup_flow.run(scripted([])[0], mode=AUTOMATIC)
    return home


def test_uninstall_puts_everything_back_and_exports_learnings(tmp_home):
    home = set_up()
    learnings.record("email-polisher", "Sign off with Best.")
    io, said = scripted([])
    result = uninstall_flow.run(io, AUTOMATIC)
    assert (home / ".claude/skills/email-polisher/SKILL.md").is_file() and wiring.rows() == []
    assert not (home / ".claude/settings.json").exists() and result["exported"][0].endswith("email-polisher.md")
    assert backup.list_all() and "pipx uninstall skillswiki" in said[-1]


def test_blocked_release_keeps_agents_connected(tmp_home):
    home = set_up()
    install_fixture_skills(home / ".claude" / "skills", ["email-polisher"])  # something is back in the way
    io, said = scripted([])
    result = uninstall_flow.run(io, AUTOMATIC)
    assert result["blocked"] and wiring.get_row("claude_code") is not None
    assert any("stopped before disconnecting" in s for s in said)


def test_guided_can_delete_backups(tmp_home):
    set_up()
    io, _ = scripted(["y", "y"])  # go ahead; delete backups
    assert uninstall_flow.run(io, "guided")["backups_deleted"] >= 2 and backup.list_all() == []


def test_dry_run_changes_nothing(tmp_home):
    home = set_up()
    before = snapshot(home)
    io, said = scripted([])
    uninstall_flow.run(io, AUTOMATIC, dry_run=True)
    assert snapshot(home) == before and any("2 skills go back" in s for s in said)


def test_self_setup_agents_get_a_removal_prompt(tmp_home):
    (Path.home() / ".hermes").mkdir()
    set_up()
    io, said = scripted([])
    uninstall_flow.run(io, AUTOMATIC)
    assert any("Hermes Agent" in s and "remove the Skills Wiki entry" in s for s in said)
