"""Cross-platform behaviour that macOS/Linux CI cannot otherwise exercise."""
import os

from skillswiki.evals.backends import TEXT_IO, executable, gemini_cli
from skillswiki.web import server


def test_executable_resolves_from_path(tmp_path, monkeypatch):
    tool = tmp_path / ("claude.cmd" if os.name == "nt" else "claude")
    tool.write_text("@echo off" if os.name == "nt" else "#!/bin/sh\n")
    tool.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    assert executable("claude") == str(tool)
    assert executable("not-installed-anywhere") == "not-installed-anywhere"


def test_cli_output_is_decoded_as_utf8():
    assert TEXT_IO == {"text": True, "encoding": "utf-8", "errors": "replace"}


def test_antigravity_flag_follows_the_binary_name(monkeypatch):
    for name, expect in (("agy", True), (r"C:\\tools\\agy.exe", True), ("/usr/local/bin/agy", True), ("gemini", False)):
        monkeypatch.setenv("SKILLSWIKI_GEMINI_BIN", name)
        assert ("--disable-slash-commands" in gemini_cli._cmd("hi", None)) is expect, name


def test_local_server_never_shares_a_port_on_windows():
    assert server.LocalServer.allow_reuse_address is (os.name != "nt")


def test_utf8_skill_text_round_trips(tmp_home):
    from skillswiki import frontmatter
    folder = tmp_home / "native" / "zh-skill"
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text("---\nname: zh\ndescription: 跟用户对话讨论选题\n---\n正文\n", encoding="utf-8")
    assert frontmatter.parse_skill(folder)["description"] == "跟用户对话讨论选题"
