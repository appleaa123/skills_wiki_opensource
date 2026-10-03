import pytest
from helpers import install_fixture_skills

from skillswiki import discovery, learnings, library, loader, paths, store


@pytest.fixture
def adopted(tmp_home):
    install_fixture_skills(tmp_home / "native")
    discovery.sync_db()
    library.adopt("csv-cleaner")


def test_load_returns_body_files_learnings(adopted):
    learnings.record("csv-cleaner", "Keep the header row.")
    result = loader.load("csv-cleaner")
    folder = paths.library_dir() / "csv-cleaner"
    assert result["slug"] == "csv-cleaner" and result["path"] == str(folder)
    assert result["skill_md"].startswith("---") and "CSV cleaner" in result["skill_md"]
    assert result["files"] == ["SKILL.md", "scripts/clean.py"]
    assert result["has_scripts"] is True
    assert "Keep the header row." in result["learnings"]
    assert str(folder) in result["note"]


def test_load_logs_usage(adopted):
    loader.load("csv-cleaner")
    with store.connect() as conn:
        row = conn.execute("SELECT * FROM usage WHERE event = 'load'").fetchone()
    assert row["slug"] == "csv-cleaner" and row["tokens_est"] > 0


def test_native_and_unknown_refused(adopted):
    with pytest.raises(ValueError, match="not adopted"):
        loader.load("email-polisher")
    with pytest.raises(ValueError, match="not found"):
        loader.load("nope")
