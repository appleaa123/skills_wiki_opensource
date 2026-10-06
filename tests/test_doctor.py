import json
from pathlib import Path

from helpers import install_fixture_skills

import skillswiki
from skillswiki import discovery, doctor, library, paths


def test_doctor_without_a_database(tmp_home):
    rep = doctor.report()
    assert rep["version"] == skillswiki.__version__ and rep["db"] == {"present": False, "tables": {}}
    assert rep["library"]["adopted"] == [] and rep["library"]["restore_json"] is False
    assert not paths.db_path().exists()  # reporting must not create it
    assert rep["typesafe_key_set"] is False and rep["jev"] == "off"
    assert set(rep["clis"]) == {"claude", "codex", "gemini", "agy"}
    assert rep["claude_code"] == {"hook_user": "no", "hook_project": "no", "mcp_user": "no", "mcp_project": "no"}


def test_doctor_with_adopted_and_missing(tmp_home):
    import shutil
    install_fixture_skills(tmp_home / "native")
    discovery.sync_db()
    library.adopt("email-polisher")
    library.adopt("csv-cleaner")
    shutil.rmtree(paths.library_dir() / "csv-cleaner")
    rep = doctor.report()
    assert rep["db"]["present"] is True and rep["db"]["tables"]["skills"] == 3
    assert rep["library"]["adopted"] == ["csv-cleaner", "email-polisher"]
    assert rep["library"]["missing"] == ["csv-cleaner"]
    assert rep["library"]["restore_json"] is True
    assert any(r["key"] == "claude_code" for r in rep["agents"])


def test_doctor_reads_claude_code_config(tmp_home, monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "sk-test")
    home = Path.home()
    (home / ".claude").mkdir()
    (home / ".claude" / "settings.json").write_text(json.dumps(
        {"hooks": {"UserPromptSubmit": [{"hooks": [{"type": "command", "command": "skillswiki hook"}]}]}}),
        encoding="utf-8")
    (home / ".claude.json").write_text(json.dumps(
        {"mcpServers": {"skills-wiki": {"command": "skillswiki", "args": ["serve-mcp"]},
                        "hosted": {"type": "http", "url": "https://example.test/mcp"}}}), encoding="utf-8")
    (Path.cwd() / ".mcp.json").write_text("{not json", encoding="utf-8")
    rep = doctor.report()
    assert rep["typesafe_key_set"] is True
    assert rep["claude_code"] == {"hook_user": "yes", "hook_project": "no", "mcp_user": "yes",
                                  "mcp_project": "unreadable"}
