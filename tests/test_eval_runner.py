import json

import pytest
from fakes import FakeBackend, write_suite
from helpers import install_fixture_skills

from skillswiki import discovery, learnings, library, paths
from skillswiki.evals import runner


@pytest.fixture
def ready(tmp_home, monkeypatch):
    install_fixture_skills(tmp_home / "native")
    discovery.sync_db()
    library.adopt("email-polisher")
    write_suite()
    fake = FakeBackend()
    monkeypatch.setattr("skillswiki.evals.backends.get_backend", lambda name: fake)
    return fake


def _config():
    return json.loads((paths.package_dir() / "evals" / "config.json").read_text())


def test_run_pack_two_arms(ready):
    result = runner.run_pack("email-polisher", "claude", None, "claude", None, runs=1, arms=["skill", "no_skill"],
                             config=_config())
    for key in ("arms", "delta_pp", "token_usage", "grading", "pairwise", "skill_sha", "verdicts", "findings"):
        assert key in result, key
    assert result["pack"] == "email-polisher" and result["tasks"] == 2
    assert result["arms"]["skill"]["pass_rate"] > result["arms"]["no_skill"]["pass_rate"]
    assert result["delta_pp"] > 0
    assert result["token_usage"]["executor"] > 0
    assert result["pairwise"]["skill_vs_no_skill"]["wins"] == 2
    assert result["verdicts"]["skill_vs_no_skill"]["basis"] == "pairwise"


def test_write_result_under_home(ready):
    result = runner.run_pack("email-polisher", "claude", None, "claude", None, runs=1, arms=["skill"],
                             config=_config())
    out = paths.results_dir() / "email-polisher" / "20261003T000000Z.json"
    outputs = runner._write_result(result, out)
    assert out.is_file() and outputs.is_file()
    assert str(paths.home()) in str(out)


def test_learning_arm_needs_learnings(ready):
    with pytest.raises(ValueError, match="needs live learnings"):
        runner.run_pack("email-polisher", "claude", None, "claude", None, runs=1, arms=["skill", "skill_learning"],
                        config=_config())
    learnings.record("email-polisher", "Always end with a next step.")
    block = learnings.build_learnings_block("email-polisher", learnings.list_live("email-polisher"))
    result = runner.run_pack("email-polisher", "claude", None, "claude", None, runs=1,
                             arms=["skill", "skill_learning"], config=_config(), learnings_block=block)
    assert "skill_learning" in result["arms"]


def test_only_one_cli_message(ready, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/claude" if name == "claude" else None)
    with pytest.raises(runner.NoIndependentJudgeAvailable, match="re-run with --judge claude"):
        runner._select_judge_backend("claude", None)


def test_not_adopted_skill_fails_loudly(ready):
    library.release("email-polisher")
    with pytest.raises(RuntimeError, match="not adopted"):
        runner.run_pack("email-polisher", "claude", None, "claude", None, runs=1, arms=["skill"], config=_config())
