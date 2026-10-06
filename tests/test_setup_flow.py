from pathlib import Path

import pytest
from helpers import install_fixture_skills, scripted, snapshot, two_agents

from skillswiki import backup, setup_flow, wiring
from skillswiki.setup_flow import AUTOMATIC


@pytest.fixture(autouse=True)
def no_real_agent_clis(monkeypatch):
    monkeypatch.setattr(wiring, "_which", lambda name: None)  # never run a real `claude mcp add` in tests
    # never call a real AI CLI for the test request: a fixed paraphrase stands in for it
    monkeypatch.setattr(setup_flow.paraphrase, "available_backend", lambda: "claude")
    monkeypatch.setattr(setup_flow.paraphrase, "from_ai", lambda description, backend: "a request in other words")


def test_dry_run_explains_everything_and_changes_nothing(tmp_home):
    home = two_agents()
    before = snapshot(home)
    io, said = scripted([])
    result = setup_flow.run(io, dry_run=True)
    text = "\n".join(said)
    assert "2 skills will move" in text and "Claude Code" in text and "Codex" in text
    assert "hook → rules file" in text and "puts every skill back" in text and "Dry run" in text
    assert snapshot(home) == before and backup.list_all() == [] and result["moved"] is None


def test_guided_decline_moves_nothing(tmp_home):
    home = two_agents()
    io, said = scripted(["g", "n"])
    result = setup_flow.run(io)
    assert result["stopped"] and (home / ".claude/skills/email-polisher").is_dir()
    assert "You chose not to continue." in "\n".join(said)


def test_eof_stops_cleanly(tmp_home):
    home = two_agents()
    io, said = scripted(["g"])  # input ends at the "go ahead?" question
    result = setup_flow.run(io)
    assert result["stopped"] and "Stopped" in said[-1] and (home / ".claude/skills/email-polisher").is_dir()


def test_automatic_moves_everything_with_a_backup(tmp_home):
    home = two_agents()
    io, said = scripted([])
    result = setup_flow.run(io, mode=AUTOMATIC)
    assert not result["stopped"] and len(result["moved"]["adopted"]) == 2
    assert not (home / ".claude/skills/email-polisher").exists() and Path(result["backup"]).is_file()
    io, said = scripted([])
    setup_flow.run(io, mode=AUTOMATIC)
    assert "No new skills to move" in "\n".join(said)


import json

from skillswiki import cards


def claude_hook_commands(home: Path) -> list[str]:
    settings = home / ".claude" / "settings.json"
    if not settings.exists():
        return []
    groups = json.loads(settings.read_text(encoding="utf-8")).get("hooks", {}).get("UserPromptSubmit", [])
    return [h["command"] for g in groups for h in g["hooks"]]


def test_automatic_wires_every_detected_agent(tmp_home):
    home = two_agents()
    io, said = scripted([])
    result = setup_flow.run(io, mode=AUTOMATIC)
    # Claude Code carries our own test result (Phase 12), so automatic mode records it as tested by Skills Wiki
    assert {o["agent"]: o["test"] for o in result["agents"]} == {"claude_code": "vouched", "codex": "untested"}
    assert claude_hook_commands(home) == ["skillswiki hook --agent claude_code"]
    assert "not tested yet" in said[-1] and "setup --test codex" in said[-1]


def test_guided_yes_marks_tested(tmp_home):
    two_agents()
    io, said = scripted(["g", "y", "y", "y", "y", "y", "y"])  # go; claude: wire, test anyway, works; codex: wire, works
    result = setup_flow.run(io)
    assert [o["test"] for o in result["agents"]] == ["tested_ok", "tested_ok"]
    assert any("restart it" in s for s in said)


def test_failed_hook_falls_back_to_rules_then_advice(tmp_home):
    home = two_agents()
    # go; claude: hook yes/test anyway/no, rules yes/no → advice; codex: wire, works
    io, said = scripted(["g", "y", "y", "y", "n", "y", "n", "y", "y"])
    result = setup_flow.run(io)
    assert result["agents"][0]["method"] == "advice" and wiring.get_row("claude_code")["test"] == "failed"
    assert claude_hook_commands(home) == [] and not (home / ".claude" / "CLAUDE.md").exists()
    assert any("check Skills Wiki" in s for s in said)


def test_test_request_prefers_a_card_example_then_an_ai_paraphrase(tmp_home):
    two_agents()
    setup_flow.run(scripted([])[0], mode=AUTOMATIC)
    io, said = scripted([])
    assert setup_flow.test_prompt(io) == ("csv-cleaner", "a request in other words")
    assert any("a few of your tokens" in s for s in said)
    cards.set_manual("email-polisher", ["Polish this email to my landlord"], [], [])
    assert setup_flow.test_prompt(scripted([])[0]) == ("email-polisher", "Polish this email to my landlord")


def test_test_request_falls_back_to_what_the_user_types(tmp_home, monkeypatch):
    two_agents()
    setup_flow.run(scripted([])[0], mode=AUTOMATIC)
    monkeypatch.setattr(setup_flow.paraphrase, "from_ai", lambda description, backend: None)
    io, said = scripted(["tidy up this export, the dates are all over the place"])
    assert setup_flow.test_prompt(io) == ("csv-cleaner", "tidy up this export, the dates are all over the place")
    assert any("csv-cleaner" in s for s in said)
    assert setup_flow.test_prompt(scripted([""])[0]) is None  # nothing typed: no test


def test_second_run_adds_nothing(tmp_home):
    home = two_agents()
    setup_flow.run(scripted([])[0], mode=AUTOMATIC)
    first = snapshot(home)
    setup_flow.run(scripted([])[0], mode=AUTOMATIC)
    assert snapshot(home) == first


def test_warns_when_not_on_path(tmp_home, monkeypatch):
    two_agents()
    monkeypatch.setattr(setup_flow, "_on_path", lambda: False)
    io, said = scripted([])
    setup_flow.run(io, mode=AUTOMATIC)
    assert any("not on your PATH" in s for s in said)


def test_run_tests_later(tmp_home):
    two_agents()
    setup_flow.run(scripted([])[0], mode=AUTOMATIC)
    io, said = scripted(["y"])
    [outcome] = setup_flow.run_tests(io, "codex")
    assert outcome["test"] == "tested_ok" and wiring.get_row("codex")["test"] == "tested_ok"
    assert setup_flow.run_tests(scripted([])[0], "cursor") == []


def test_ctrl_c_during_the_move_stops_cleanly(tmp_home, monkeypatch):
    """Review #3: Ctrl-C outside a question still ends with a stopped summary."""
    two_agents()
    monkeypatch.setattr(setup_flow.library, "adopt_all",
                        lambda **kw: (_ for _ in ()).throw(KeyboardInterrupt()) if not kw.get("dry_run") else
                        {"adopted": [], "failed": []})
    io, said = scripted([])
    assert setup_flow.run(io, mode=AUTOMATIC)["stopped"] and "Stopped" in said[-1]


def test_one_broken_agent_does_not_stop_the_others(tmp_home, monkeypatch):
    """Review #3: an unexpected error is reported for that agent; the rest are still connected."""
    two_agents()
    real_apply = wiring.apply

    def flaky(key, method, zip_path=None):
        if key == "claude_code":
            raise OSError("disk full")
        return real_apply(key, method, zip_path)
    monkeypatch.setattr(setup_flow.wiring, "apply", flaky)
    io, said = scripted([])
    result = setup_flow.run(io, mode=AUTOMATIC)
    assert [o["method"] for o in result["agents"]] == ["error", "hook"]
    assert any("disk full" in s for s in said)


def test_a_failed_agent_is_offered_again_on_the_next_run(tmp_home):
    """Review #5: an agent that ended in advice can be retried."""
    two_agents()
    setup_flow.run(scripted(["g", "y", "y", "y", "n", "y", "n", "y", "y"])[0])  # claude ends in advice
    assert wiring.get_row("claude_code")["test"] == "failed"
    io, _ = scripted(["g", "y", "y", "y", "y"])  # go; claude: wire hook, test anyway, works
    result = setup_flow.run(io)
    assert result["agents"][0]["agent"] == "claude_code" and result["agents"][0]["test"] == "tested_ok"
    assert wiring.get_row("claude_code")["method"] == "hook"


def test_known_result_makes_the_test_optional(tmp_home):
    two_agents()
    io, said = scripted(["g", "y", "y", "n", "y", "y"])  # go; claude: wire, skip test; codex: wire, works
    result = setup_flow.run(io)
    assert [o["test"] for o in result["agents"]] == ["vouched", "tested_ok"]
    assert wiring.get_row("claude_code")["vouched"] == 1
    assert any("We tested Claude Code" in s for s in said) and "tested by Skills Wiki" in said[-1]


def test_automatic_records_the_known_result(tmp_home):
    two_agents()
    result = setup_flow.run(scripted([])[0], mode=AUTOMATIC)
    assert {o["agent"]: o["test"] for o in result["agents"]} == {"claude_code": "vouched", "codex": "untested"}


def test_vouched_agents_are_not_asked_again_on_a_rerun(tmp_home):
    two_agents()
    setup_flow.run(scripted([])[0], mode=AUTOMATIC)
    result = setup_flow.run(scripted([])[0], mode=AUTOMATIC)
    assert [o["agent"] for o in result["agents"]] == ["codex"]


def test_run_tests_survives_input_ending_at_the_request_question(tmp_home, monkeypatch):
    """Review: --test must not traceback when the free-text request question gets no input."""
    two_agents()
    setup_flow.run(scripted([])[0], mode=AUTOMATIC)
    monkeypatch.setattr(setup_flow.paraphrase, "from_ai", lambda description, backend: None)
    io, said = scripted([])  # input ends at "Your request"
    assert setup_flow.run_tests(io, "codex") == [] and "Stopped" in " ".join(said)


def test_no_vouching_when_the_hook_was_not_written(tmp_home):
    """Review: our result vouches only for a hook that is actually installed."""
    home = two_agents()
    (home / ".claude" / "settings.json").write_text("{ // comments\n}", encoding="utf-8")
    result = setup_flow.run(scripted([])[0], mode=AUTOMATIC)
    claude = result["agents"][0]
    assert claude["agent"] == "claude_code" and claude["test"] == "untested" and claude["self_setup"]
    assert not wiring.get_row("claude_code")["vouched"]


def test_no_tokens_spent_when_the_user_skips_the_only_test(tmp_home, monkeypatch):
    """Review: the AI paraphrase is requested only when a test is actually about to run."""
    home = Path.home()
    install_fixture_skills(home / ".claude" / "skills", ["email-polisher"])  # Claude Code only
    calls = []
    monkeypatch.setattr(setup_flow.paraphrase, "from_ai", lambda d, b: calls.append(d) or "other words")
    setup_flow.run(scripted(["g", "y", "y", "n"])[0])  # go; claude: wire, skip test
    assert calls == []


def test_skipping_a_retest_keeps_the_vouched_mark(tmp_home):
    """Review: 's' is not a result, so it must not erase 'tested by Skills Wiki'."""
    two_agents()
    setup_flow.run(scripted([])[0], mode=AUTOMATIC)
    setup_flow.run_tests(scripted(["s"])[0], "claude_code")
    assert wiring.get_row("claude_code")["vouched"] == 1


def test_plan_groups_links_placed_by_another_tool():
    """Cleanup: the live run printed 14 near-identical warnings."""
    from skillswiki import setup_text
    plans = [{"slug": f"cheat-{i}", "from": f"/h/.claude/skills/cheat-{i}", "moved_copies": [], "differing_copies": [],
              "linked_copies": [], "warnings": [{"code": "FOREIGN_LINK", "path": f"/h/.claude/skills/cheat-{i}",
                                                 "target": f"/h/proj/skills/cheat-{i}", "message": "long text"}]}
             for i in range(3)]
    lines = setup_text.plan_lines({"adopted": plans, "failed": []})
    assert "long text" not in lines and lines.count("/h/proj/skills") == 1 and "3 of these skills" in lines


def test_skipping_the_request_says_testing_was_skipped(tmp_home, monkeypatch):
    two_agents()
    setup_flow.run(scripted([])[0], mode=AUTOMATIC)
    monkeypatch.setattr(setup_flow.paraphrase, "from_ai", lambda description, backend: None)
    io, said = scripted([""])
    setup_flow.run_tests(io, "codex")
    assert any("skipped" in s for s in said) and not any("no adopted skill" in s for s in said)
