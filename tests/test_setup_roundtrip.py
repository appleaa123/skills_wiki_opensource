from pathlib import Path

import pytest
from helpers import install_fixture_skills, snapshot

from skillswiki import setup_flow, uninstall_flow, wiring
from skillswiki.setup_flow import AUTOMATIC, Io


def test_setup_then_uninstall_leaves_home_as_it_was(tmp_home, monkeypatch):
    monkeypatch.setattr(wiring, "_which", lambda name: None)
    home = Path.home()
    install_fixture_skills(home / ".claude" / "skills", ["email-polisher"])
    install_fixture_skills(home / ".agents" / "skills", ["csv-cleaner", "meeting-notes"])
    (home / ".codex").mkdir()
    (home / ".gemini").mkdir()
    (home / ".claude" / "settings.json").write_text('{"theme": "dark"}', encoding="utf-8")
    (home / ".gemini" / "GEMINI.md").write_text("# my rules", encoding="utf-8")
    before = snapshot(home)
    io = Io(say=lambda _t: None, ask=lambda q, c: pytest.fail(f"automatic mode asked: {q}"))
    assert setup_flow.run(io, mode=AUTOMATIC)["stopped"] is False
    assert snapshot(home) != before
    result = uninstall_flow.run(io, AUTOMATIC, delete_backups=True)
    assert not result["blocked"] and snapshot(home) == before
