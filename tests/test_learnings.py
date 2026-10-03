import pytest

from skillswiki import learnings, store


def test_record_and_list_newest_first(tmp_home):
    a = learnings.record("email-polisher", "Always sign off with 'Best'.")
    b = learnings.record("email-polisher", "Never use exclamation marks.")
    live = learnings.list_live("email-polisher")
    assert [r["id"] for r in live] == [b, a]
    assert set(live[0]) >= {"id", "slug", "body", "created_at"}


def test_body_rules(tmp_home):
    with pytest.raises(ValueError, match="body is empty"):
        learnings.record("s", "   ")
    with pytest.raises(ValueError, match="max 600"):
        learnings.record("s", "x" * 601)


def test_cap_and_supersede(tmp_home):
    ids = [learnings.record("s", f"rule {i}") for i in range(learnings.LEARNING_MAX_LIVE_PER_SKILL)]
    with pytest.raises(ValueError, match="already has 8 live learnings"):
        learnings.record("s", "one too many")
    new_id = learnings.record("s", "replacement", supersedes=[ids[0]])
    live_ids = [r["id"] for r in learnings.list_live("s")]
    assert ids[0] not in live_ids and new_id in live_ids and len(live_ids) == 8
    history = learnings.list_all("s")
    assert next(r for r in history if r["id"] == ids[0])["superseded_by"] == new_id


def test_supersede_only_own_skill(tmp_home):
    other = learnings.record("other", "keep me")
    learnings.record("s", "new", supersedes=[other])
    assert [r["id"] for r in learnings.list_live("other")] == [other]


def test_edit_keeps_history_and_retire(tmp_home):
    first = learnings.record("s", "old wording")
    second = learnings.edit(first, "new wording")
    assert [r["body"] for r in learnings.list_live("s")] == ["new wording"]
    learnings.retire(second)
    assert learnings.list_live("s") == []
    assert len(learnings.list_all("s")) == 2
    with pytest.raises(ValueError, match="not found"):
        learnings.retire(999)


def test_learning_mode_off(tmp_home):
    store.set_setting("learning", "off")
    with pytest.raises(ValueError, match="Learning Mode is off"):
        learnings.record("s", "x")
    assert learnings.block_for("s") == ""


def test_block_newest_first_with_reminder(tmp_home):
    learnings.record("s", "first")
    learnings.record("s", "second")
    block = learnings.block_for("s")
    assert block.index("second") < block.index("first")
    assert block.startswith("<learnings>") and learnings.LEARNING_REMINDER in block
    assert "[#2 · s] second" in block


def test_block_cold_start_has_reminder_only(tmp_home):
    assert learnings.block_for("s") == learnings.LEARNING_REMINDER


def test_block_size_cap_never_cuts_a_line():
    rows = [{"id": i, "slug": "s", "body": "y" * 590} for i in range(20)]
    block = learnings.build_learnings_block("s", rows)
    assert len(block) <= learnings.LEARNINGS_BLOCK_MAX_CHARS + len("<learnings>\n\n</learnings>")
    assert all(line.endswith("y" * 590) for line in block.splitlines() if line.startswith("- [#"))
