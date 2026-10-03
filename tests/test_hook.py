import io
import json

import pytest
from helpers import install_fixture_skills

from skillswiki import discovery, hook, library


@pytest.fixture
def adopted(tmp_home):
    install_fixture_skills(tmp_home / "native")
    discovery.sync_db()
    library.adopt("email-polisher")


def _run(monkeypatch, capsys, payload: str) -> str:
    monkeypatch.setattr("sys.stdin", io.StringIO(payload))
    hook.main()
    return capsys.readouterr().out


def test_shortlist_becomes_additional_context(adopted, monkeypatch, capsys):
    out = _run(monkeypatch, capsys, json.dumps({"prompt": "please fix my email draft before I send it"}))
    data = json.loads(out)["hookSpecificOutput"]
    assert data["hookEventName"] == "UserPromptSubmit"
    assert "email-polisher" in data["additionalContext"]
    assert "Email polisher" not in data["additionalContext"]  # never the skill body


@pytest.mark.parametrize("prompt", ["short one", "/compact everything in the session now", "zzzz qqqq xxxx yyyy"])
def test_silent_cases(adopted, monkeypatch, capsys, prompt):
    assert _run(monkeypatch, capsys, json.dumps({"prompt": prompt})) == ""


def test_errors_never_block(adopted, monkeypatch, capsys):
    assert _run(monkeypatch, capsys, "not json at all") == ""
    monkeypatch.setattr("skillswiki.route.suggest", lambda r: (_ for _ in ()).throw(RuntimeError("boom")))
    assert _run(monkeypatch, capsys, json.dumps({"prompt": "please fix my email draft before I send it"})) == ""


def test_context_line_for_confident_pick():
    line = hook.context_line({"skill": "email-polisher", "confidence": 0.93, "shortlist": []})
    assert "email-polisher" in line and "0.93" in line


def test_cli_hook_is_fail_safe_with_broken_env(adopted, monkeypatch, capsys):
    from skillswiki import cli, paths
    (paths.home() / ".env").write_bytes(b"\xff\xfe broken \x00")
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"prompt": "please fix my email draft before I send it"})))
    assert cli.main(["hook"]) == 0
    out = capsys.readouterr()
    assert out.err == ""


def test_hook_passes_its_time_budget(adopted, monkeypatch, capsys):
    seen = {}
    monkeypatch.setattr("skillswiki.route.suggest", lambda r, budget_s=None: seen.update(b=budget_s) or {"shortlist": []})
    _run(monkeypatch, capsys, json.dumps({"prompt": "please fix my email draft before I send it"}))
    assert seen["b"] == hook.BUDGET_S
