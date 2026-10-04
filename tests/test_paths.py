import os
from pathlib import Path

from skillswiki import paths


def test_home_defaults_and_is_created(tmp_home):
    assert paths.home() == tmp_home / "home"
    assert paths.home().is_dir()


def test_home_default_without_env(tmp_home, monkeypatch):
    monkeypatch.delenv("SKILLSWIKI_HOME")
    assert paths.home() == tmp_home / "userhome" / ".skillswiki"


def test_subdirs_live_under_home(tmp_home):
    home = paths.home()
    assert paths.library_dir() == home / "library"
    assert paths.suites_dir() == home / "suites"
    assert paths.results_dir() == home / "results"
    assert paths.calibration_dir() == home / "calibration"
    assert paths.db_path() == home / "skillswiki.db"
    assert paths.library_dir().is_dir()


def test_scan_roots_user_project_and_extra(tmp_home):
    roots = paths.scan_roots()
    userhome, work = tmp_home / "userhome", tmp_home / "work"
    for name in (".claude", ".codex", ".agents", ".gemini"):
        assert userhome / name / "skills" in roots
        assert work / name / "skills" in roots
    assert tmp_home / "native" in roots
    # user-level before project-level before extras
    assert roots.index(userhome / ".claude" / "skills") < roots.index(work / ".claude" / "skills")
    assert roots[-1] == tmp_home / "native"


def test_scan_roots_dedupes(tmp_home, monkeypatch):
    extra = tmp_home / "userhome" / ".claude" / "skills"
    monkeypatch.setenv("SKILLSWIKI_SCAN_ROOTS", f"{extra}{os.pathsep}{extra}")
    roots = paths.scan_roots()
    assert roots.count(extra) == 1


def test_package_dir_is_the_package():
    assert paths.package_dir() == Path(paths.__file__).resolve().parent


def test_load_env_reads_home_dotenv_without_overriding(tmp_home, monkeypatch):
    (paths.home() / ".env").write_text("# comment\nTYPESAFE_API_KEY=abc\nSKILLSWIKI_JEV = off\nBROKEN\n")
    monkeypatch.setenv("SKILLSWIKI_JEV", "on")
    paths.load_env()
    import os
    assert os.environ["TYPESAFE_API_KEY"] == "abc"
    assert os.environ["SKILLSWIKI_JEV"] == "on"


def test_relative_extra_scan_root_is_made_absolute(tmp_home, monkeypatch):
    monkeypatch.setenv("SKILLSWIKI_SCAN_ROOTS", "relative/skills")
    assert paths.scan_roots()[-1] == tmp_home / "work" / "relative" / "skills"
    assert paths.scan_roots()[-1].is_absolute()
