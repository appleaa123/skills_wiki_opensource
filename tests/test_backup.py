import zipfile
from pathlib import Path

import pytest
from conftest import needs_symlinks
from helpers import install_fixture_skills

from skillswiki import backup, errors, paths


def _names(zip_path: Path) -> list[str]:
    with zipfile.ZipFile(zip_path) as zf:
        return sorted(zf.namelist())


def test_skill_folders_and_config_files_are_stored_by_home_relative_path(tmp_home):
    native = install_fixture_skills(Path.home() / ".claude" / "skills", ["email-polisher"])
    settings = Path.home() / ".claude" / "settings.json"
    settings.write_text("{}", encoding="utf-8")
    zip_path = backup.start()
    backup.add_skill(zip_path, native / "email-polisher")
    assert backup.add_file(zip_path, settings) is True
    assert backup.add_file(zip_path, settings) is False  # stored once
    assert backup.add_file(zip_path, Path.home() / "missing.json") is False
    names = _names(zip_path)
    assert "skills/.claude/skills/email-polisher/SKILL.md" in names and "config/.claude/settings.json" in names


def test_two_backups_in_one_second_never_collide(tmp_home):
    assert backup.start() != backup.start() and len(backup.list_all()) == 2


def test_skills_wiki_data_is_never_backed_up(tmp_home):
    env = paths.home() / ".env"
    env.write_text("TYPESAFE_API_KEY=secret", encoding="utf-8")
    with pytest.raises(errors.SkillsWikiError):
        backup.add_file(backup.start(), env)


@needs_symlinks
def test_a_linked_skill_is_stored_as_its_link(tmp_home):
    real = install_fixture_skills(tmp_home / "native", ["csv-cleaner"]) / "csv-cleaner"
    link = Path.home() / ".claude" / "skills" / "csv-cleaner"
    link.parent.mkdir(parents=True)
    link.symlink_to(real, target_is_directory=True)
    zip_path = backup.start()
    backup.add_skill(zip_path, link)
    assert _names(zip_path) == ["skills/.claude/skills/csv-cleaner" + backup.LINK_SUFFIX]


def test_delete_all(tmp_home):
    backup.start()
    assert backup.delete_all() == 1 and backup.list_all() == []
