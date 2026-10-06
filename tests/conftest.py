"""Shared fixtures. Every test runs in a throwaway home so nothing touches the real ~ or ~/.skillswiki."""
import os
import tempfile

import pytest

WINDOWS = os.name == "nt"


def _can_symlink() -> bool:
    with tempfile.TemporaryDirectory() as d:
        try:
            os.symlink(d, os.path.join(d, "link"), target_is_directory=True)
            return True
        except (OSError, NotImplementedError):
            return False


# Windows: chmod(0) does not make a file unreadable, and symlinks need Developer Mode or admin rights.
posix_permissions = pytest.mark.skipif(WINDOWS, reason="file permission bits are POSIX-only")
needs_symlinks = pytest.mark.skipif(not _can_symlink(), reason="symlinks not permitted here")


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
    monkeypatch.setenv("USERPROFILE", str(userhome))  # Path.home() on Windows
    monkeypatch.setenv("SKILLSWIKI_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("SKILLSWIKI_SCAN_ROOTS", str(native))
    for var in ("TYPESAFE_API_KEY", "SKILLSWIKI_JEV", "JEV_MODEL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(work)
    return tmp_path


@pytest.fixture(autouse=True)
def no_real_agent_or_ai_cli(monkeypatch):
    """No test may run a real agent CLI (`claude mcp add`) or spend AI tokens: tests that need them patch these."""
    from skillswiki import paraphrase, wiring

    def refuse(name):
        raise AssertionError(f"tests never call a real AI CLI ({name}); patch paraphrase.get_backend")
    monkeypatch.setattr(wiring, "_which", lambda name: None)
    monkeypatch.setattr(paraphrase, "get_backend", refuse)
