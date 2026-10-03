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
