import json
from pathlib import Path

import pytest

from skillswiki import errors, wiring_data, wiring_io
from skillswiki.wiring_data import Target

MD = Target(".gemini/GEMINI.md", "md_block", {}, "https://x")
HOOK = wiring_data.by_key("codex").hook
CLAUDE_HOOK = wiring_data.by_key("claude_code").hook
TOML = Target(".codex/config.toml", "toml_block", {}, "https://x")
OPENCODE_MCP = Target(".config/opencode/opencode.json", "json_mcp", {"key": "mcp", "entry": "opencode"}, "https://x")


def _file(rel: str) -> Path:
    return Path.home() / rel


def test_md_block_on_a_missing_file_is_created_then_deleted():
    assert wiring_io.apply(MD, "gemini_cli:rules") == "written"
    assert wiring_data.RULES_TEXT in _file(MD.path).read_text(encoding="utf-8")
    assert wiring_io.remove(MD, "gemini_cli:rules") == "deleted"
    assert not _file(MD.path).exists()


def test_md_block_restores_exact_bytes_without_trailing_newline():
    path = _file(MD.path)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"# My rules\nBe brief.")
    wiring_io.apply(MD, "gemini_cli:rules")
    assert path.read_text(encoding="utf-8").startswith("# My rules\nBe brief.\n")
    assert wiring_io.remove(MD, "gemini_cli:rules") == "restored"
    assert path.read_bytes() == b"# My rules\nBe brief."


def test_shared_block_stays_until_its_last_user_leaves():
    assert wiring_io.apply(MD, "gemini_cli:rules") == "written"
    assert wiring_io.apply(MD, "antigravity:rules") == "shared"
    assert wiring_io.remove(MD, "gemini_cli:rules") == "kept"
    assert wiring_io.MD_BLOCK in _file(MD.path).read_text(encoding="utf-8")
    assert wiring_io.remove(MD, "antigravity:rules") == "deleted"


def test_json_hook_is_added_once_and_keeps_other_settings():
    path = _file(HOOK.path)
    path.parent.mkdir(parents=True)
    path.write_text('{"theme": "dark"}', encoding="utf-8")
    assert wiring_io.apply(HOOK, "codex:hook") == "written"
    assert wiring_io.apply(HOOK, "codex:hook") == "shared"  # second setup run: no duplicate
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["theme"] == "dark" and len(data["hooks"]["UserPromptSubmit"]) == 1
    assert wiring_io.remove(HOOK, "codex:hook") == "restored"
    assert path.read_text(encoding="utf-8") == '{"theme": "dark"}'


def test_user_entry_survives_removal():
    path = _file(HOOK.path)
    wiring_io.apply(HOOK, "codex:hook")
    data = json.loads(path.read_text(encoding="utf-8"))
    data["hooks"]["UserPromptSubmit"].append({"hooks": [{"type": "command", "command": "my-own-hook"}]})
    path.write_text(json.dumps(data), encoding="utf-8")
    assert wiring_io.remove(HOOK, "codex:hook") == "edited"
    left = json.loads(path.read_text(encoding="utf-8"))["hooks"]["UserPromptSubmit"]
    assert left == [{"hooks": [{"type": "command", "command": "my-own-hook"}]}]


def test_legacy_claude_hook_counts_as_present_and_is_never_removed():
    path = _file(CLAUDE_HOOK.path)
    path.parent.mkdir(parents=True)
    original = json.dumps({"hooks": {"UserPromptSubmit": [{"hooks": [{"type": "command",
                                                                       "command": "skillswiki hook"}]}]}})
    path.write_text(original, encoding="utf-8")
    assert wiring_io.apply(CLAUDE_HOOK, "claude_code:hook") == "present"
    assert wiring_io.remove(CLAUDE_HOOK, "claude_code:hook") == "kept"
    assert path.read_text(encoding="utf-8") == original


@pytest.mark.parametrize("text", ['{"a": 1, // comment\n}', "[1, 2]"])
def test_unparseable_or_non_object_json_is_never_touched(text):
    path = _file(HOOK.path)
    path.parent.mkdir(parents=True)
    path.write_text(text, encoding="utf-8")
    with pytest.raises(errors.SkillsWikiError) as info:
        wiring_io.apply(HOOK, "codex:hook")
    assert info.value.code == "INVALID_INPUT" and path.read_text(encoding="utf-8") == text


def test_empty_json_file_counts_as_empty_object_and_comes_back_empty():
    path = _file(HOOK.path)
    path.parent.mkdir(parents=True)
    path.write_text("", encoding="utf-8")
    assert wiring_io.apply(HOOK, "codex:hook") == "written"
    assert wiring_io.remove(HOOK, "codex:hook") == "restored" and path.read_text(encoding="utf-8") == ""


def test_toml_block_round_trip_and_refuses_a_conflicting_inline_table():
    path = _file(TOML.path)
    path.parent.mkdir(parents=True)
    path.write_text('model = "o4"\n', encoding="utf-8")
    assert wiring_io.apply(TOML, "codex:mcp") == "written"
    import tomllib
    assert tomllib.loads(path.read_text(encoding="utf-8"))["mcp_servers"]["skillswiki"]["args"] == ["serve-mcp"]
    assert wiring_io.remove(TOML, "codex:mcp") == "restored" and path.read_text(encoding="utf-8") == 'model = "o4"\n'
    path.write_text('mcp_servers = { other = { command = "x" } }\n', encoding="utf-8")
    with pytest.raises(errors.SkillsWikiError):
        wiring_io.apply(TOML, "codex:mcp")


def test_opencode_mcp_entry_shape():
    wiring_io.apply(OPENCODE_MCP, "opencode:mcp")
    data = json.loads(_file(OPENCODE_MCP.path).read_text(encoding="utf-8"))
    assert data["mcp"]["skillswiki"] == {"type": "local", "command": ["skillswiki", "serve-mcp"]}


def test_own_rules_file_is_created_refused_if_different_and_removed():
    rules = wiring_data.by_key("cline").rules
    assert wiring_io.apply(rules, "cline:rules") == "written"
    path = wiring_io.resolve(rules)
    assert path.read_text(encoding="utf-8") == wiring_data.RULES_TEXT + "\n"
    assert wiring_io.remove(rules, "cline:rules") == "deleted" and not path.exists()
    path.write_text("my own notes", encoding="utf-8")
    with pytest.raises(errors.SkillsWikiError):
        wiring_io.apply(rules, "cline:rules")


def test_copilot_mcp_entry_shape():
    wiring_io.apply(wiring_data.by_key("github_copilot").mcp, "github_copilot:mcp")
    data = json.loads(_file(".copilot/mcp-config.json").read_text(encoding="utf-8"))
    assert data["mcpServers"]["skillswiki"]["tools"] == ["*"]


def test_a_failed_write_leaves_the_original(monkeypatch):
    path = _file(HOOK.path)
    path.parent.mkdir(parents=True)
    path.write_text('{"theme": "dark"}', encoding="utf-8")
    monkeypatch.setattr(wiring_io.os, "replace", lambda *a: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError):
        wiring_io.apply(HOOK, "codex:hook")
    assert path.read_text(encoding="utf-8") == '{"theme": "dark"}'


def test_file_deleted_by_the_user_is_gone_not_recreated():
    wiring_io.apply(MD, "gemini_cli:rules")
    _file(MD.path).unlink()
    assert wiring_io.remove(MD, "gemini_cli:rules") == "gone" and not _file(MD.path).exists()
