"""Shared fixtures. Every test runs in a throwaway home so nothing touches the real ~ or ~/.skillswiki."""
import pytest


@pytest.fixture(autouse=True)
def tmp_home(tmp_path, monkeypatch):
    """Redirect HOME (covers Path.home() and os.path.expanduser), the Skills Wiki data home, the extra
    scan root and the working directory into tmp_path."""
    userhome = tmp_path / "userhome"
    userhome.mkdir()
    native = tmp_path / "native"
    native.mkdir()
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.setenv("HOME", str(userhome))
    monkeypatch.setenv("SKILLSWIKI_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("SKILLSWIKI_SCAN_ROOTS", str(native))
    for var in ("TYPESAFE_API_KEY", "SKILLSWIKI_JEV", "JEV_MODEL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(work)
    return tmp_path
