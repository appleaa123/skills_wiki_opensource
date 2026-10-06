from skillswiki import agents, wiring_data

KINDS = {"json_hook", "json_mcp", "toml_block", "md_block", "own_file", "cli", "self_setup"}
FORMATS = {"claude", "gemini", "cline", "antigravity", "hermes", "plain"}


def test_every_row_is_a_known_agent_with_known_kinds():
    keys = {a.key for a in agents.AGENTS}
    for w in wiring_data.WIRINGS:
        assert w.agent in keys
        for target in (w.hook, w.rules, w.mcp):
            assert target is None or (target.kind in KINDS and target.source.startswith("http"))
        assert (w.hook is None) == (w.hook_format is None) and (w.hook_format in FORMATS | {None})


def test_every_agent_has_a_row_and_lookup_works():
    assert {w.agent for w in wiring_data.WIRINGS} == {a.key for a in agents.AGENTS}
    assert wiring_data.by_key("codex").hook.detail["command"] == "skillswiki hook --agent codex"


def test_shared_gemini_targets_are_identical():
    gem, ag, agy = (wiring_data.by_key(k) for k in ("gemini_cli", "antigravity", "antigravity_cli"))
    assert gem.rules == ag.rules == agy.rules
    assert ag.hook == agy.hook and ag.mcp == agy.mcp


def test_hookless_agents_go_to_rules():
    for key in ("github_copilot", "cursor", "windsurf", "goose"):
        assert wiring_data.by_key(key).hook is None
