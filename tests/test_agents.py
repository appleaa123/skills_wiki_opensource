from pathlib import Path

import pytest
from conftest import posix_permissions
from helpers import install_fixture_skills

from skillswiki import agents, errors, paths

OLD_FOUR = {".claude/skills", ".codex/skills", ".agents/skills", ".gemini/skills"}


def test_first_four_roots_are_the_old_four_with_claude_first():
    first = agents.user_skill_dirs()[:4]
    assert first[0] == ".claude/skills" and set(first) == OLD_FOUR


def test_shared_folder_listed_once_and_keys_unique():
    dirs = agents.user_skill_dirs()
    assert dirs.count(".agents/skills") == 1 and len(dirs) == len(set(dirs))
    keys = [a.key for a in agents.AGENTS]
    assert len(keys) == len(set(keys)) and len(keys) >= 20


def test_antigravity_rows_match_the_docs():
    assert agents.by_key("antigravity").skills_dirs == (".gemini/config/skills", ".gemini/antigravity/skills")
    assert agents.by_key("antigravity_cli").skills_dirs == (".gemini/antigravity-cli/skills",)
    assert agents.by_key("gemini_cli").skills_dirs == (".gemini/skills", ".agents/skills")
    assert agents.by_key("codex").skills_dirs == (".agents/skills", ".codex/skills")


def test_by_key_unknown_is_invalid_input():
    with pytest.raises(errors.SkillsWikiError) as info:
        agents.by_key("nope")
    assert info.value.code == "INVALID_INPUT" and "claude_code" in str(info.value)


def test_detected_and_skill_count(tmp_home):
    install_fixture_skills(tmp_home / "userhome" / ".cursor" / "skills", ["email-polisher"])
    cursor = agents.by_key("cursor")
    assert agents.detected(cursor) and agents.skill_count(cursor) == 1
    assert not agents.detected(agents.by_key("windsurf")) and agents.skill_count(agents.by_key("windsurf")) == 0


def test_keys_for_a_root(tmp_home):
    shared = tmp_home / "userhome" / ".agents" / "skills"
    shared.mkdir(parents=True)
    assert agents.keys_for(shared) == ["codex", "gemini_cli", "github_copilot", "cline", "warp"]
    assert agents.keys_for(tmp_home / "native") == []


def test_rows_shape(tmp_home):
    row = next(r for r in agents.rows() if r["key"] == "claude_code")
    assert row == {"key": "claude_code", "name": "Claude Code", "detected": False, "skills": 0,
                   "dirs": [str(Path.home() / ".claude" / "skills")]}


def test_scan_roots_come_from_the_registry(tmp_home):
    roots = paths.scan_roots()
    userhome, work = tmp_home / "userhome", tmp_home / "work"
    assert roots[0] == userhome / ".claude" / "skills"
    assert userhome / ".cursor" / "skills" in roots and userhome / ".gemini" / "config" / "skills" in roots
    assert work / ".agent" / "skills" in roots and work / ".cursor" / "skills" not in roots
    assert roots.index(userhome / ".hermes" / "skills") < roots.index(work / ".claude" / "skills")


def test_project_level_roots_name_their_agents(tmp_home):
    work = tmp_home / "work"
    for d in agents.PROJECT_SKILL_DIRS:
        (work / d).mkdir(parents=True)
    assert agents.keys_for(work / ".claude" / "skills") == ["claude_code"]
    assert agents.keys_for(work / ".codex" / "skills") == ["codex"]
    assert agents.keys_for(work / ".gemini" / "skills") == ["gemini_cli"]
    assert agents.keys_for(work / ".agent" / "skills") == ["antigravity"]
    assert agents.keys_for(work / ".agents" / "skills") == ["codex", "gemini_cli", "antigravity", "github_copilot",
                                                             "cline", "warp"]
    assert agents.keys_for(tmp_home / "native") == []  # an extra from SKILLSWIKI_SCAN_ROOTS stays unowned


@posix_permissions
def test_skill_count_survives_an_unreadable_folder(tmp_home):
    folder = install_fixture_skills(tmp_home / "userhome" / ".cursor" / "skills", ["email-polisher"])
    folder.chmod(0)
    try:
        assert agents.skill_count(agents.by_key("cursor")) == 0
    finally:
        folder.chmod(0o755)


def test_project_dirs_prefer_codex_current_folder():
    dirs = agents.PROJECT_SKILL_DIRS
    assert dirs.index(".agents/skills") < dirs.index(".codex/skills")
