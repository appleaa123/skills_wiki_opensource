import os

import pytest
from conftest import needs_symlinks, posix_permissions
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
                      "fingerprint": library.fingerprint(target), "moved_copies": [], "other_copies": [],
                      "differing_copies": [], "linked_copies": [], "warnings": []}
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


@needs_symlinks
def test_symlinked_skill_round_trip(tmp_home):
    real = install_fixture_skills(tmp_home / "elsewhere", ["meeting-notes"]) / "meeting-notes"
    link = tmp_home / "native" / "meeting-notes"
    os.symlink(real, link)
    discovery.sync_db()
    library.adopt("meeting-notes")
    target = paths.library_dir() / "meeting-notes"
    assert target.is_symlink() and not link.exists() and real.is_dir()
    library.release("meeting-notes")
    assert link.is_symlink() and link.resolve() == real.resolve()  # Windows reports targets as \\?\C:\...


def test_adopt_moves_identical_other_copy(tmp_home):
    # Behaviour changed 2026-10-03 (owner): identical copies are moved with the skill instead of only reported.
    install_fixture_skills(tmp_home / "userhome" / ".claude" / "skills", ["email-polisher"])
    install_fixture_skills(tmp_home / "userhome" / ".agents" / "skills", ["email-polisher"])
    discovery.sync_db()
    result = library.adopt("email-polisher")
    assert result["moved_copies"] == [str(tmp_home / "userhome" / ".agents" / "skills" / "email-polisher")]
    assert result["other_copies"] == []


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


@posix_permissions
def test_adopt_survives_unreadable_skill_elsewhere(native, tmp_home):
    bad = install_fixture_skills(tmp_home / "userhome" / ".agents" / "skills", ["meeting-notes"]) / "meeting-notes"
    (bad / "SKILL.md").chmod(0)
    try:
        result = library.adopt("email-polisher")
        assert result["other_copies"] == []
    finally:
        (bad / "SKILL.md").chmod(0o644)


# ── adopt every identical copy (owner decision 2026-10-03) ─────────────


def _copies(tmp_home, *dirs):
    roots = [tmp_home / "userhome" / d / "skills" for d in dirs]
    for root in roots:
        install_fixture_skills(root, ["email-polisher"])
    discovery.sync_db()
    return [root / "email-polisher" for root in roots]


def test_identical_copies_are_all_moved_and_all_restored(tmp_home):
    claude, agents, gemini = _copies(tmp_home, ".claude", ".agents", ".gemini")
    before = library.fingerprint(claude)
    result = library.adopt("email-polisher")
    assert result["moved_copies"] == [str(agents), str(gemini)] and result["other_copies"] == []
    assert not claude.exists() and not agents.exists() and not gemini.exists()
    assert {s["slug"] for s in discovery.scan()["skills"]} == {"email-polisher"}  # no native copy left to trigger
    assert discovery.scan()["conflicts"] == []
    discovery.sync_db()  # a rescan keeps the recorded copies
    result = library.release("email-polisher")
    assert result["restored_copies"] == [str(agents), str(gemini)]
    for folder in (claude, agents, gemini):
        assert library.fingerprint(folder) == before
    assert _row("email-polisher")["copies"] is None


def test_differing_copy_stays_and_is_reported(tmp_home):
    claude, agents = _copies(tmp_home, ".claude", ".agents")
    (agents / "SKILL.md").write_text((agents / "SKILL.md").read_text() + "\nlocal tweak\n")
    result = library.adopt("email-polisher")
    assert result["moved_copies"] == [] and result["other_copies"] == [str(agents)]
    assert result["differing_copies"] == [str(agents)]
    assert agents.exists() and not claude.exists()


def test_release_refuses_if_any_copy_origin_is_occupied(tmp_home):
    claude, agents = _copies(tmp_home, ".claude", ".agents")
    library.adopt("email-polisher")
    agents.mkdir()
    with pytest.raises(ValueError, match="already exists"):
        library.release("email-polisher")
    assert (paths.library_dir() / "email-polisher").exists() and not claude.exists()


def test_adopt_rolls_back_copies_when_store_update_fails(tmp_home, monkeypatch):
    claude, agents = _copies(tmp_home, ".claude", ".agents")
    calls = {"n": 0}
    real = store.connect

    def flaky():
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("disk full")
        return real()
    monkeypatch.setattr(library.store, "connect", flaky)
    with pytest.raises(RuntimeError, match="disk full"):
        library.adopt("email-polisher")
    monkeypatch.setattr(library.store, "connect", real)
    assert claude.exists() and agents.exists()
    assert not (paths.library_dir() / "email-polisher").exists()


def test_failed_adopt_can_be_retried(tmp_home, monkeypatch):
    _copies(tmp_home, ".claude", ".agents")
    calls = {"n": 0}
    real = store.connect

    def flaky():
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("disk full")
        return real()
    monkeypatch.setattr(library.store, "connect", flaky)
    with pytest.raises(RuntimeError):
        library.adopt("email-polisher")
    monkeypatch.setattr(library.store, "connect", real)
    assert not (paths.library_dir() / ".copies" / "email-polisher").exists()
    assert library.adopt("email-polisher")["moved_copies"]  # retry works


@needs_symlinks
def test_symlinked_primary_never_strands_its_target(tmp_home):
    real = install_fixture_skills(tmp_home / "userhome" / ".agents" / "skills", ["email-polisher"]) / "email-polisher"
    link_root = tmp_home / "userhome" / ".claude" / "skills"
    link_root.mkdir(parents=True)
    os.symlink(real, link_root / "email-polisher")
    discovery.sync_db()
    result = library.adopt("email-polisher")
    assert result["moved_copies"] == [] and result["linked_copies"] == [str(real)]
    assert (paths.library_dir() / "email-polisher" / "SKILL.md").is_file()  # the link still resolves
    library.release("email-polisher")
    assert (link_root / "email-polisher").is_symlink() and real.is_dir()


def test_release_never_deletes_user_files_in_holding_folder(tmp_home):
    _copies(tmp_home, ".claude", ".agents")
    library.adopt("email-polisher")
    keep = paths.library_dir() / ".copies" / "email-polisher" / "my-notes.txt"
    keep.write_text("mine")
    library.release("email-polisher")
    assert keep.read_text() == "mine"


def test_release_skips_a_missing_stored_copy(tmp_home):
    import shutil
    claude, agents = _copies(tmp_home, ".claude", ".agents")
    library.adopt("email-polisher")
    shutil.rmtree(paths.library_dir() / ".copies" / "email-polisher" / "1")
    result = library.release("email-polisher")
    assert claude.is_dir() and not agents.exists() and result["missing_copies"] == [str(agents)]


@needs_symlinks
def test_relative_symlink_primary_is_refused(tmp_home):
    real = install_fixture_skills(tmp_home / "elsewhere", ["meeting-notes"]) / "meeting-notes"
    link = tmp_home / "native" / "meeting-notes"
    os.symlink(os.path.relpath(real, link.parent), link)
    discovery.sync_db()
    with pytest.raises(ValueError, match="relative symlink"):
        library.adopt("meeting-notes")
    assert link.is_symlink() and (link / "SKILL.md").is_file()


def test_release_with_library_folder_gone_is_clean(native):
    import shutil
    library.adopt("email-polisher")
    shutil.rmtree(paths.library_dir() / "email-polisher")
    with pytest.raises(ValueError, match="no longer in the library"):
        library.release("email-polisher")


# ── release everything + recovery manifest ─────────────────────────────


def test_release_all_restores_every_adopted_skill(native, tmp_home):
    install_fixture_skills(tmp_home / "userhome" / ".agents" / "skills", ["meeting-notes"])
    discovery.sync_db()
    for slug in ("email-polisher", "meeting-notes"):
        library.adopt(slug)
    result = library.release_all()
    assert sorted(r["slug"] for r in result["released"]) == ["email-polisher", "meeting-notes"]
    assert result["failed"] == []
    assert (native / "email-polisher").is_dir() and (native / "meeting-notes").is_dir()
    assert (tmp_home / "userhome" / ".agents" / "skills" / "meeting-notes").is_dir()


def test_release_all_keeps_going_after_a_failure(native):
    library.adopt("email-polisher")
    library.adopt("csv-cleaner")
    (native / "email-polisher").mkdir()  # origin occupied: this one must fail, the other still released
    result = library.release_all()
    assert [r["slug"] for r in result["released"]] == ["csv-cleaner"]
    assert result["failed"][0]["slug"] == "email-polisher" and "already exists" in result["failed"][0]["error"]


def test_recovery_manifest_tracks_adopted_skills(native, tmp_home):
    import json
    install_fixture_skills(tmp_home / "userhome" / ".agents" / "skills", ["email-polisher"])
    discovery.sync_db()
    library.adopt("email-polisher")
    manifest = json.loads((paths.library_dir() / library.MANIFEST_NAME).read_text())
    entry = manifest["skills"]["email-polisher"]
    origins = {entry["origin"], *(c["origin"] for c in entry["copies"])}
    assert origins == {str(native / "email-polisher"),
                       str(tmp_home / "userhome" / ".agents" / "skills" / "email-polisher")}
    assert "move each folder back" in manifest["how_to_restore_by_hand"]
    library.release("email-polisher")
    manifest = json.loads((paths.library_dir() / library.MANIFEST_NAME).read_text())
    assert manifest["skills"] == {}


def test_manifest_is_not_a_skill(native):
    library.adopt("email-polisher")
    assert {s["slug"] for s in discovery.scan()["skills"] if s["status"] == "adopted"} == {"email-polisher"}


def test_plan_adopt_moves_nothing(native):
    before = _row("email-polisher")
    plan = library.plan_adopt("email-polisher")
    assert plan["from"] == str(native / "email-polisher")
    assert plan["to"] == str(paths.library_dir() / "email-polisher")
    assert plan["moved_copies"] == [] and plan["warnings"] == [] and len(plan["fingerprint"]) == 16
    assert (native / "email-polisher").is_dir() and not (paths.library_dir() / "email-polisher").exists()
    assert _row("email-polisher") == before
    assert not (paths.library_dir() / "RESTORE.json").exists()


def test_adopt_returns_exactly_its_plan(tmp_home):
    _copies(tmp_home, ".claude", ".agents")
    plan = library.plan_adopt("email-polisher")
    result = library.adopt("email-polisher")
    assert result == plan
    assert not os.path.exists(plan["from"]) and os.path.isdir(plan["to"])
    assert len(plan["moved_copies"]) == 1 and not os.path.exists(plan["moved_copies"][0])


def test_plan_adopt_raises_like_adopt(native):
    library.adopt("email-polisher")
    with pytest.raises(ValueError, match="already adopted"):
        library.plan_adopt("email-polisher")
    with pytest.raises(ValueError, match="not found"):
        library.plan_adopt("nope")


def test_plan_release_moves_nothing(native):
    library.adopt("email-polisher")
    plan = library.plan_release("email-polisher")
    assert plan["to"] == str(native / "email-polisher")
    assert plan["from"] == str(paths.library_dir() / "email-polisher")
    assert plan["moves"][0] == [plan["to"], plan["from"]]
    assert not (native / "email-polisher").exists() and _row("email-polisher")["status"] == "adopted"


def test_release_returns_exactly_its_plan(tmp_home):
    _copies(tmp_home, ".claude", ".agents")
    library.adopt("email-polisher")
    plan = library.plan_release("email-polisher")
    assert library.release("email-polisher") == plan
    assert all(os.path.isdir(origin) for origin, _stored in plan["moves"])


def test_plan_release_refuses_native(native):
    with pytest.raises(ValueError, match="not adopted"):
        library.plan_release("email-polisher")


def test_release_all_dry_run_moves_nothing(native):
    library.adopt("email-polisher")
    library.adopt("csv-cleaner")
    result = library.release_all(dry_run=True)
    assert [r["slug"] for r in result["released"]] == ["csv-cleaner", "email-polisher"] and result["failed"] == []
    assert not (native / "email-polisher").exists() and not (native / "csv-cleaner").exists()


@needs_symlinks
def test_adopt_warns_on_link_placed_by_another_tool(tmp_home):
    real = install_fixture_skills(tmp_home / "other-tool" / "skills", ["meeting-notes"]) / "meeting-notes"
    os.symlink(real, tmp_home / "native" / "meeting-notes")
    discovery.sync_db()
    plan = library.plan_adopt("meeting-notes")
    assert [w["code"] for w in plan["warnings"]] == ["FOREIGN_LINK"]
    assert plan["warnings"][0]["target"] == str(real.resolve())
    assert "skills-manager" in plan["warnings"][0]["message"]
    assert library.adopt("meeting-notes")["warnings"] == plan["warnings"]  # warned, still adopted


@needs_symlinks
def test_link_into_a_scanned_root_is_not_foreign(tmp_home):
    real = install_fixture_skills(tmp_home / "userhome" / ".agents" / "skills", ["email-polisher"]) / "email-polisher"
    link_root = tmp_home / "userhome" / ".claude" / "skills"
    link_root.mkdir(parents=True)
    os.symlink(real, link_root / "email-polisher")
    discovery.sync_db()
    assert library.plan_adopt("email-polisher")["warnings"] == []


def test_plain_folder_is_never_foreign(native):
    assert library.plan_adopt("email-polisher")["warnings"] == []


def test_shipped_skill_cannot_be_adopted(tmp_home):
    import shutil
    shutil.copytree(paths.shipped_skill_dir(), tmp_home / "native" / "skills-wiki")
    discovery.sync_db()
    assert _row("skills-wiki")["status"] == "native"
    with pytest.raises(ValueError, match="hide it from your agent") as info:
        library.plan_adopt("skills-wiki")
    assert info.value.code == "RESERVED"


def test_adopt_all_dry_run_moves_nothing_then_adopts_everything(tmp_home):
    import zipfile

    from skillswiki import backup
    native = install_fixture_skills(tmp_home / "native")
    discovery.sync_db()
    preview = library.adopt_all(dry_run=True)
    assert [p["slug"] for p in preview["adopted"]] == ["csv-cleaner", "email-polisher", "meeting-notes"]
    assert (native / "csv-cleaner").is_dir()
    zip_path = backup.start()
    result = library.adopt_all(zip_path=zip_path)
    assert len(result["adopted"]) == 3 and result["failed"] == [] and not (native / "csv-cleaner").exists()
    assert any(n.endswith("csv-cleaner/SKILL.md") for n in zipfile.ZipFile(zip_path).namelist())


def test_adopt_all_keeps_going_past_a_failure(tmp_home):
    install_fixture_skills(tmp_home / "native")
    discovery.sync_db()
    (paths.library_dir() / "email-polisher").mkdir(parents=True)
    result = library.adopt_all()
    assert [f["slug"] for f in result["failed"]] == ["email-polisher"]
    assert result["failed"][0]["code"] == "TARGET_EXISTS" and len(result["adopted"]) == 2
