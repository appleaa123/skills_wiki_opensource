import json
import shutil

from helpers import FIXTURES, install_fixture_skills

from skillswiki import discovery, store


def _rows():
    with store.connect() as conn:
        return {r["slug"]: dict(r) for r in conn.execute("SELECT * FROM skills")}


def test_scan_finds_native_skills(tmp_home):
    native = install_fixture_skills(tmp_home / "native")
    shutil.copytree(FIXTURES / "broken" / "no-frontmatter", native / "no-frontmatter")
    (native / "not-a-skill").mkdir()
    result = discovery.scan()
    slugs = {s["slug"]: s for s in result["skills"]}
    assert set(slugs) == {"email-polisher", "meeting-notes", "csv-cleaner", "no-frontmatter"}
    assert all(s["status"] == "native" for s in slugs.values())
    assert slugs["csv-cleaner"]["has_scripts"] is True
    assert slugs["email-polisher"]["has_scripts"] is False
    assert slugs["email-polisher"]["context_tokens_est"] > 0
    assert result["conflicts"] == []


def test_scan_reads_agent_dirs_under_home_and_cwd(tmp_home):
    install_fixture_skills(tmp_home / "userhome" / ".agents" / "skills", ["email-polisher"])
    install_fixture_skills(tmp_home / "work" / ".gemini" / "skills", ["meeting-notes"])
    slugs = {s["slug"] for s in discovery.scan()["skills"]}
    assert slugs == {"email-polisher", "meeting-notes"}


def test_slug_collision_reported_not_overwritten(tmp_home):
    install_fixture_skills(tmp_home / "userhome" / ".claude" / "skills", ["email-polisher"])
    install_fixture_skills(tmp_home / "native", ["email-polisher"])
    result = discovery.scan()
    paths = [s["path"] for s in result["skills"] if s["slug"] == "email-polisher"]
    assert len(paths) == 1 and ".claude" in paths[0]
    assert result["conflicts"][0]["slug"] == "email-polisher"
    assert "native" in result["conflicts"][0]["path"]


def test_plugin_skills_listed_read_only(tmp_home):
    install = tmp_home / "userhome" / ".claude" / "plugins" / "cache" / "mkt" / "toolkit" / "abc"
    install_fixture_skills(install / "skills", ["meeting-notes"])
    install_fixture_skills(install / "docs" / "zh-TW" / "skills", ["meeting-notes"])  # translated copy: ignored
    install_fixture_skills(install / "skills" / "meeting-notes" / "upstream", ["csv-cleaner"])  # nested: ignored
    manifest = tmp_home / "userhome" / ".claude" / "plugins" / "installed_plugins.json"
    manifest.write_text(json.dumps({"version": 2, "plugins": {"toolkit@mkt": [{"installPath": str(install)}]}}))
    skills = discovery.scan()["skills"]
    assert [(s["slug"], s["status"]) for s in skills] == [("toolkit:meeting-notes", "plugin")]


def test_bad_plugin_manifest_is_ignored(tmp_home):
    manifest = tmp_home / "userhome" / ".claude" / "plugins" / "installed_plugins.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("{not json")
    assert discovery.scan()["skills"] == []


def test_sync_db_upserts_and_removes(tmp_home):
    native = install_fixture_skills(tmp_home / "native")
    report = discovery.sync_db()
    assert report["total"] == 3 and sorted(report["added"]) == ["csv-cleaner", "email-polisher", "meeting-notes"]
    rows = _rows()
    assert rows["meeting-notes"]["description"].startswith("Turns a meeting transcript")
    assert rows["csv-cleaner"]["has_scripts"] == 1
    shutil.rmtree(native / "meeting-notes")
    report = discovery.sync_db()
    assert report["removed"] == ["meeting-notes"]
    assert "meeting-notes" not in _rows()
