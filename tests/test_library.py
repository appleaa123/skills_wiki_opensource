import os

import pytest
from helpers import install_fixture_skills

from skillswiki import discovery, library, paths, store


def _row(slug):
    with store.connect() as conn:
        row = conn.execute("SELECT * FROM skills WHERE slug = ?", (slug,)).fetchone()
    return dict(row) if row else None


@pytest.fixture
def native(tmp_home):
    root = install_fixture_skills(tmp_home / "native")
    discovery.sync_db()
    return root


def test_fingerprint_stable_and_sensitive(native):
    folder = native / "email-polisher"
    first = library.fingerprint(folder)
    assert first == library.fingerprint(folder) and len(first) == 16
    (folder / ".DS_Store").write_text("ignored")
    assert library.fingerprint(folder) == first
    (folder / "SKILL.md").write_text((folder / "SKILL.md").read_text() + "x")
    assert library.fingerprint(folder) != first


def test_fingerprint_changes_when_file_added(native):
    folder = native / "meeting-notes"
    before = library.fingerprint(folder)
    (folder / "extra.md").write_text("new")
    assert library.fingerprint(folder) != before


def test_adopt_moves_folder_and_updates_row(native):
    result = library.adopt("email-polisher")
    target = paths.library_dir() / "email-polisher"
    assert result == {"slug": "email-polisher", "from": str(native / "email-polisher"), "to": str(target),
                      "other_copies": []}
    assert not (native / "email-polisher").exists() and (target / "SKILL.md").is_file()
    row = _row("email-polisher")
    assert row["status"] == "adopted" and row["path"] == str(target)
    assert row["origin_path"] == str(native / "email-polisher")
    assert row["fingerprint"] == library.fingerprint(target) and row["adopted_at"]
    assert library.changed("email-polisher") is False


def test_rescan_keeps_adopted_state(native):
    library.adopt("email-polisher")
    discovery.sync_db()
    row = _row("email-polisher")
    assert row["status"] == "adopted" and row["origin_path"] == str(native / "email-polisher")


def test_changed_flag(native):
    library.adopt("meeting-notes")
    (paths.library_dir() / "meeting-notes" / "SKILL.md").write_text("edited")
    assert library.changed("meeting-notes") is True


def test_release_restores_byte_identical(native):
    before = library.fingerprint(native / "csv-cleaner")
    library.adopt("csv-cleaner")
    result = library.release("csv-cleaner")
    assert result["to"] == str(native / "csv-cleaner")
    assert library.fingerprint(native / "csv-cleaner") == before
    row = _row("csv-cleaner")
    assert row["status"] == "native" and row["origin_path"] is None and row["fingerprint"] is None


def test_adopt_refuses_when_target_exists(native):
    (paths.library_dir() / "email-polisher").mkdir()
    with pytest.raises(ValueError, match="already exists"):
        library.adopt("email-polisher")
    assert (native / "email-polisher").exists()


def test_adopt_refuses_unknown_and_plugin(native):
    with pytest.raises(ValueError, match="not found"):
        library.adopt("nope")
    with store.connect() as conn:
        conn.execute("INSERT INTO skills (slug, name, status, path, updated_at) VALUES "
                     "('kit:x', 'x', 'plugin', '/p', ?)", (store.now(),))
    with pytest.raises(ValueError, match="plugin-managed"):
        library.adopt("kit:x")


def test_adopt_twice_refused(native):
    library.adopt("email-polisher")
    with pytest.raises(ValueError, match="already adopted"):
        library.adopt("email-polisher")


def test_release_refuses_when_origin_occupied(native):
    library.adopt("email-polisher")
    (native / "email-polisher").mkdir()
    with pytest.raises(ValueError, match="already exists"):
        library.release("email-polisher")
    assert (paths.library_dir() / "email-polisher").exists()


def test_release_refuses_native(native):
    with pytest.raises(ValueError, match="not adopted"):
        library.release("email-polisher")


def test_symlinked_skill_round_trip(tmp_home):
    real = install_fixture_skills(tmp_home / "elsewhere", ["meeting-notes"]) / "meeting-notes"
    link = tmp_home / "native" / "meeting-notes"
    os.symlink(real, link)
    discovery.sync_db()
    library.adopt("meeting-notes")
    target = paths.library_dir() / "meeting-notes"
    assert target.is_symlink() and not link.exists() and real.is_dir()
    library.release("meeting-notes")
    assert link.is_symlink() and os.readlink(link) == str(real)


def test_adopt_reports_other_native_copies(tmp_home):
    install_fixture_skills(tmp_home / "userhome" / ".claude" / "skills", ["email-polisher"])
    install_fixture_skills(tmp_home / "userhome" / ".agents" / "skills", ["email-polisher"])
    discovery.sync_db()
    result = library.adopt("email-polisher")
    assert result["other_copies"] == [str(tmp_home / "userhome" / ".agents" / "skills" / "email-polisher")]


def test_adopt_rolls_back_when_db_update_fails(native, monkeypatch):
    def broken_connect():
        raise RuntimeError("disk full")
    calls = {"n": 0}
    real = store.connect

    def flaky():
        calls["n"] += 1
        return real() if calls["n"] == 1 else broken_connect()
    monkeypatch.setattr(library.store, "connect", flaky)
    with pytest.raises(RuntimeError, match="disk full"):
        library.adopt("email-polisher")
    monkeypatch.setattr(library.store, "connect", real)
    assert (native / "email-polisher" / "SKILL.md").is_file()
    assert not (paths.library_dir() / "email-polisher").exists()
    assert _row("email-polisher")["status"] == "native"


def test_adopt_survives_unreadable_skill_elsewhere(native, tmp_home):
    bad = install_fixture_skills(tmp_home / "userhome" / ".agents" / "skills", ["meeting-notes"]) / "meeting-notes"
    (bad / "SKILL.md").chmod(0)
    try:
        result = library.adopt("email-polisher")
        assert result["other_copies"] == []
    finally:
        (bad / "SKILL.md").chmod(0o644)
