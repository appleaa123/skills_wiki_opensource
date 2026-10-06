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
    assert rep["claude_code"] == {"hook_user": "no", "hook_project": "no", "mcp_user": "no", "mcp_local": "no",
                                  "mcp_project": "no"}


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
    assert rep["claude_code"] == {"hook_user": "yes", "hook_project": "no", "mcp_user": "yes", "mcp_local": "no",
                                  "mcp_project": "unreadable"}


def test_export_without_a_database(tmp_home):
    import zipfile
    path = doctor.export(Path.cwd())
    assert path.name.startswith("skillswiki-doctor-") and path.suffix == ".zip"
    with zipfile.ZipFile(path) as zf:
        assert sorted(zf.namelist()) == ["env_keys.json", "report.json"]
        assert json.loads(zf.read("report.json"))["db"]["present"] is False


def test_export_contents_and_no_secret(tmp_home, monkeypatch):
    import zipfile

    from skillswiki import usage
    install_fixture_skills(tmp_home / "native")
    discovery.sync_db()
    library.adopt("email-polisher")
    secret = "sk-very-secret-value"
    monkeypatch.setenv("TYPESAFE_API_KEY", secret)
    (paths.home() / ".env").write_text(f"TYPESAFE_API_KEY={secret}\nSKILLSWIKI_JEV=off\n", encoding="utf-8")
    usage.log("email-polisher", "load")
    path = doctor.export(Path.cwd())
    with zipfile.ZipFile(path) as zf:
        assert sorted(zf.namelist()) == ["RESTORE.json", "env_keys.json", "report.json", "settings.json",
                                         "skills.json", "usage_summary.json"]
        assert json.loads(zf.read("env_keys.json")) == ["SKILLSWIKI_JEV", "TYPESAFE_API_KEY"]
        assert json.loads(zf.read("usage_summary.json")) == {"email-polisher": {"load": 1}}
        assert json.loads(zf.read("skills.json"))[0]["slug"] == "csv-cleaner"
        assert all(secret.encode() not in zf.read(name) for name in zf.namelist())


def test_doctor_sees_local_scope_mcp(tmp_home):
    # `claude mcp add` without --scope writes to ~/.claude.json -> projects[<cwd>].mcpServers (local scope)
    (Path.home() / ".claude.json").write_text(json.dumps(
        {"projects": {str(Path.cwd()): {"mcpServers": {"skills-wiki": {"command": "skillswiki",
                                                                        "args": ["serve-mcp"]}}}}}),
        encoding="utf-8")
    assert doctor.report()["claude_code"]["mcp_local"] == "yes"


def test_doctor_survives_unexpected_json_shapes(tmp_home):
    home = Path.home()
    (home / ".claude").mkdir()
    for settings, config in ((["x"], [None]),
                             ({"hooks": {"UserPromptSubmit": ["skillswiki hook"]}}, {"mcpServers": {"a": None}}),
                             ({"hooks": {"UserPromptSubmit": [{"hooks": ["x"]}]}}, {"mcpServers": {"a": "x"}}),
                             ({"hooks": None}, {"projects": {str(Path.cwd()): None}})):
        (home / ".claude" / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
        (home / ".claude.json").write_text(json.dumps(config), encoding="utf-8")
        status = doctor.report()["claude_code"]
        assert set(status.values()) <= {"yes", "no", "unreadable"}


def test_tables_counts_a_table_with_a_reserved_name(tmp_home):
    from skillswiki import store
    with store.connect() as conn:
        conn.execute('CREATE TABLE "order" (id INTEGER)')
        conn.execute('INSERT INTO "order" VALUES (1)')
    assert doctor.report()["db"]["tables"]["order"] == 1
