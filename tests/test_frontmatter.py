from pathlib import Path

from skillswiki import frontmatter

FIXTURES = Path(__file__).parent / "fixtures"


def test_plain_scalars():
    s = frontmatter.parse_skill(FIXTURES / "skills" / "email-polisher")
    assert s["slug"] == "email-polisher"
    assert s["name"] == "email-polisher"
    assert s["description"] == "Rewrites draft emails so they sound natural and clear."
    assert s["body"].lstrip().startswith("# Email polisher")
    assert "---" not in s["body"].splitlines()[0]


def test_folded_multiline_and_nested_mapping_skipped():
    s = frontmatter.parse_skill(FIXTURES / "skills" / "meeting-notes")
    assert s["description"] == "Turns a meeting transcript into decisions, owners and next steps."
    assert s["name"] == "meeting-notes"


def test_quoted_values():
    s = frontmatter.parse_skill(FIXTURES / "skills" / "csv-cleaner")
    assert s["slug"] == "csv-cleaner"
    assert s["name"] == "CSV Cleaner"
    assert s["description"] == "Cleans messy CSV files: trims, dedupes, normalises dates."


def test_no_frontmatter_falls_back_to_first_content_line():
    s = frontmatter.parse_skill(FIXTURES / "broken" / "no-frontmatter")
    assert s["name"] == "no-frontmatter"
    assert s["description"] == "Summarises long documents into five bullet points."
    assert s["body"].startswith("# A skill without frontmatter")


def test_literal_block_keeps_lines_joined_for_description():
    fm, body = frontmatter.split("---\ndescription: |\n  line one\n  line two\nname: x\n---\nbody\n")
    assert fm == {"description": "line one line two", "name": "x"}
    assert body == "body\n"


def test_unterminated_frontmatter_is_body():
    fm, body = frontmatter.split("---\nname: x\nno closing fence\n")
    assert fm == {}
    assert body.startswith("---")


def test_lists_and_comments_ignored():
    fm, _ = frontmatter.split("---\n# comment\nname: x\ntags:\n  - a\n  - b\nallowed-tools: Read, Bash\n---\n")
    assert fm == {"name": "x", "tags": "", "allowed-tools": "Read, Bash"}


def test_hash_inside_quotes_is_kept():
    fm, _ = frontmatter.split('---\ndescription: "Use the #1 rule"\nname: x # trailing comment\n---\n')
    assert fm == {"description": "Use the #1 rule", "name": "x"}


def test_plain_multiline_scalar_is_joined():
    fm, _ = frontmatter.split("---\ndescription: Turns notes\n  into a plan\n  with owners.\nname: x\n---\n")
    assert fm == {"description": "Turns notes into a plan with owners.", "name": "x"}
