import json

import pytest
from fakes import FakeBackend, write_suite
from helpers import install_fixture_skills

from skillswiki import cli, discovery, learnings, library


@pytest.fixture
def ready(tmp_home, monkeypatch):
    install_fixture_skills(tmp_home / "native")
    discovery.sync_db()
    library.adopt("email-polisher")
    write_suite()
    fake = FakeBackend()
    monkeypatch.setattr("skillswiki.evals.backends.get_backend", lambda name: fake)
    return fake


def run(capsys, *argv):
    code = cli.main(list(argv))
    out = capsys.readouterr()
    return code, out.out, out.err


def test_run_report_accept(ready, capsys):
    code, out, err = run(capsys, "--json", "eval", "run", "email-polisher", "--tier", "screen", "--judge", "claude")
    assert code == 0, err
    assert "tokens on your own AI plan" in err
    eval_id = json.loads(out)["eval_id"]
    code, out, _ = run(capsys, "--json", "eval", "report", "email-polisher")
    assert json.loads(out)[0]["id"] == eval_id
    code, out, _ = run(capsys, "eval", "accept", str(eval_id))
    assert code == 0 and f"Accepted eval #{eval_id}" in out


def test_publish_tier_requires_confirmation(ready, capsys, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda prompt="": "n")
    code, _, err = run(capsys, "eval", "run", "email-polisher", "--tier", "publish", "--judge", "claude")
    assert code == 1 and "cancelled" in err and ready.calls == 0
    code, out, _ = run(capsys, "eval", "run", "email-polisher", "--tier", "publish", "--judge", "claude", "--yes",
                       "--runs", "1")
    assert code == 0 and "Recorded as eval #" in out


def test_learning_tier_uses_live_learnings(ready, capsys):
    learnings.record("email-polisher", "Always end with a next step.")
    code, out, err = run(capsys, "--json", "eval", "run", "email-polisher", "--tier", "learning_screen",
                         "--judge", "claude")
    assert code == 0, err
    code, out, _ = run(capsys, "--json", "eval", "report", "email-polisher")
    assert json.loads(out)[0]["tier"] == "learning_screen"


def test_missing_suite_is_a_clean_error(tmp_home, capsys):
    code, _, err = run(capsys, "eval", "run", "nothing-here", "--tier", "screen")
    assert code == 1 and "eval generate" in err and "Traceback" not in err


def test_unchecked_suite_warns(ready, capsys):
    from skillswiki.evals import suite
    suite.set_status("email-polisher", "draft")
    _, _, err = run(capsys, "eval", "run", "email-polisher", "--tier", "screen", "--judge", "claude")
    assert "not checked" in err
