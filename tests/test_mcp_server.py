import asyncio

from fastmcp import Client
from helpers import install_fixture_skills

from skillswiki import discovery, library, store
from skillswiki.mcp_server import mcp

EXPECTED_TOOLS = {"suggest_skill", "load_skill", "list_skills", "learning_record", "learning_list"}


def _call(name, args):
    async def go():
        async with Client(mcp) as client:
            return (await client.call_tool(name, args)).data
    return asyncio.run(go())


def _tools():
    async def go():
        async with Client(mcp) as client:
            return {t.name for t in await client.list_tools()}
    return asyncio.run(go())


def test_fixed_tool_set(tmp_home):
    install_fixture_skills(tmp_home / "native")
    discovery.sync_db()
    library.adopt("email-polisher")
    tools = _tools()
    assert tools == EXPECTED_TOOLS and len(tools) == 5


def test_suggest_load_learn_flow(tmp_home):
    install_fixture_skills(tmp_home / "native")
    discovery.sync_db()
    library.adopt("email-polisher")
    suggestion = _call("suggest_skill", {"request": "fix my email draft"})
    assert suggestion["shortlist"][0]["skill"] == "email-polisher"
    assert _call("learning_record", {"slug": "email-polisher", "body": "Keep it under 120 words."}) == \
        {"status": "ok", "id": 1}
    assert _call("learning_list", {"slug": "email-polisher"})["learnings"][0]["body"] == "Keep it under 120 words."
    loaded = _call("load_skill", {"slug": "email-polisher"})
    assert "Keep it under 120 words." in loaded["learnings"]
    assert _call("list_skills", {})["skills"] == [
        {"slug": "email-polisher", "description": "Rewrites draft emails so they sound natural and clear.",
         "has_card": False}]


def test_errors_are_returned_not_raised(tmp_home):
    assert "not found" in _call("load_skill", {"slug": "nope"})["error"]
    store.set_setting("learning", "off")
    assert "Learning Mode is off" in _call("learning_record", {"slug": "x", "body": "y"})["error"]
    assert "Learning Mode is off" in _call("learning_list", {"slug": "x"})["error"]


def test_learning_for_unknown_or_native_skill_refused(tmp_home):
    install_fixture_skills(tmp_home / "native")
    discovery.sync_db()
    assert "not found" in _call("learning_record", {"slug": "typo", "body": "x"})["error"]
    assert "not adopted" in _call("learning_record", {"slug": "csv-cleaner", "body": "x"})["error"]


def test_errors_carry_codes(tmp_home):
    assert _call("load_skill", {"slug": "nope"})["code"] == "NOT_FOUND"
    assert _call("load_skill", {"slug": "nope"})["details"] == {"slug": "nope"}
    store.set_setting("learning", "off")
    assert _call("learning_record", {"slug": "x", "body": "y"})["code"] == "LEARNING_OFF"
    assert _call("learning_list", {"slug": "x"})["code"] == "LEARNING_OFF"
