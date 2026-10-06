import json
import subprocess
from pathlib import Path

import pytest

from skillswiki import backup, wiring, wiring_data


def _home(rel: str) -> Path:
    return Path.home() / rel


def test_methods_skip_missing_slots():
    assert wiring.methods("codex") == ["hook", "rules", "advice"]
    assert wiring.methods("cursor") == ["rules", "advice"]
    assert wiring.methods("warp") == ["advice"]


def test_apply_codex_hook_registers_mcp_and_records_untested():
    toml = _home(".codex/config.toml")
    toml.parent.mkdir(parents=True)
    toml.write_text('model = "o4"\n', encoding="utf-8")
    zip_path = backup.start()
    result = wiring.apply("codex", "hook", zip_path)
    assert result["state"] == "written" and result["mcp"]["state"] == "done"
    assert _home(".codex/hooks.json").is_file()
    assert wiring.get_row("codex") == {"agent": "codex", "method": "hook", "self_setup": 0, "mcp": "done",
                                       "test": "untested", "updated_at": wiring.get_row("codex")["updated_at"]}
    import zipfile
    assert "config/.codex/config.toml" in zipfile.ZipFile(zip_path).namelist()


def test_unparseable_settings_fall_back_to_a_self_setup_prompt():
    settings = _home(".gemini/settings.json")
    settings.parent.mkdir(parents=True)
    settings.write_text("{ // my comments\n}", encoding="utf-8")
    result = wiring.apply("gemini_cli", "hook")
    assert result["state"] == "self_setup" and "not plain JSON" in result["reason"]
    assert "skillswiki hook --agent gemini_cli" in result["prompt"] and "BeforeAgent" in result["prompt"]
    assert settings.read_text(encoding="utf-8") == "{ // my comments\n}"
    assert wiring.get_row("gemini_cli")["self_setup"] == 1


def test_undo_then_remove_all_puts_files_back():
    wiring.apply("codex", "hook")
    assert wiring.undo("codex", "hook")["state"] == "deleted"
    wiring.apply("codex", "rules")
    wiring.remove_all("codex")
    assert not _home(".codex/AGENTS.md").exists() and not _home(".codex/config.toml").exists()
    assert wiring.get_row("codex") is None


def test_hermes_hook_is_a_self_setup_prompt_with_its_removal_twin():
    result = wiring.apply("hermes", "hook")
    assert result["state"] == "self_setup" and "pre_llm_call" in result["prompt"]
    assert "wait for my approval" in result["prompt"]
    removal = wiring.undo("hermes", "hook")
    assert removal["state"] == "self_setup" and "remove the Skills Wiki entry" in removal["prompt"]


def test_cursor_rules_prompt_is_for_the_user():
    result = wiring.apply("cursor", "rules")
    assert result["prompt"].startswith("In Cursor Settings") and wiring_data.RULES_TEXT in result["prompt"]


def test_claude_mcp_uses_the_claude_cli(monkeypatch):
    calls = []
    monkeypatch.setattr(wiring, "_which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(wiring.subprocess, "run",
                        lambda argv, **kw: calls.append(argv) or subprocess.CompletedProcess(argv, 0, "", ""))
    assert wiring.apply("claude_code", "hook")["mcp"]["state"] == "done"
    wiring.remove_all("claude_code")
    assert calls[0][1:5] == ["mcp", "add", "--transport", "stdio"] and calls[1][1:4] == ["mcp", "remove", "skillswiki"]


def test_claude_mcp_already_present_is_left_alone_on_removal(monkeypatch):
    _home(".claude.json").write_text(json.dumps({"mcpServers": {"skillswiki": {"command": "x"}}}), encoding="utf-8")
    monkeypatch.setattr(wiring.subprocess, "run", lambda *a, **k: pytest.fail("must not call the CLI"))
    assert wiring.apply("claude_code", "rules")["mcp"]["state"] == "present"
    assert wiring.remove_all("claude_code")["mcp"]["state"] == "present"


def test_claude_cli_missing_gives_the_command_to_run(monkeypatch):
    monkeypatch.setattr(wiring, "_which", lambda name: None)
    mcp = wiring.apply("claude_code", "rules")["mcp"]
    assert mcp["state"] == "none" and "claude mcp add" in mcp["reason"]


def test_shared_gemini_rules_stay_until_both_agents_are_removed():
    wiring.apply("gemini_cli", "rules")
    wiring.apply("antigravity", "rules")
    wiring.remove_all("gemini_cli")
    assert "skillswiki:begin" in _home(".gemini/GEMINI.md").read_text(encoding="utf-8")
    wiring.remove_all("antigravity")
    assert not _home(".gemini/GEMINI.md").exists()


def test_set_test_and_rows():
    wiring.apply("warp", "advice")
    wiring.set_test("warp", "failed")
    assert [r["test"] for r in wiring.rows()] == ["failed"]
