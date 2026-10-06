from pathlib import Path

import pytest
from helpers import install_fixture_skills

import skillswiki
from skillswiki import discovery, errors, learnings, library, loader, store

CORE_MODULES = ("library.py", "loader.py", "learnings.py", "cards.py")


def test_no_bare_value_errors_in_core_modules():
    package = Path(skillswiki.__file__).parent
    for name in CORE_MODULES:
        assert "raise ValueError(" not in (package / name).read_text(encoding="utf-8"), name


def test_error_is_a_value_error_with_code_and_details():
    exc = errors.SkillsWikiError("NOT_FOUND", "skill 'x' not found", slug="x")
    assert isinstance(exc, ValueError) and str(exc) == "skill 'x' not found"
    assert exc.code == "NOT_FOUND" and exc.details == {"slug": "x"}
    assert errors.as_payload(exc) == {"code": "NOT_FOUND", "message": "skill 'x' not found", "details": {"slug": "x"}}
    assert errors.as_payload(ValueError("x")) == {"code": "INVALID_INPUT", "message": "x", "details": {}}


def test_unknown_code_is_a_programming_error_even_under_python_O():
    # LookupError: never a ValueError, so no CLI/MCP/web handler reports a typo in a code as INVALID_INPUT
    with pytest.raises(LookupError):
        errors.SkillsWikiError("NOPE", "x")
    assert "assert " not in Path(errors.__file__).read_text(encoding="utf-8")


def _code(fn, *args, **kwargs):
    with pytest.raises(errors.SkillsWikiError) as info:
        fn(*args, **kwargs)
    return info.value.code, info.value.details


def test_codes_on_the_adopt_path(tmp_home):
    native = install_fixture_skills(tmp_home / "native")
    discovery.sync_db()
    assert _code(library.adopt, "nope") == ("NOT_FOUND", {"slug": "nope"})
    assert _code(library.release, "email-polisher") == ("NOT_ADOPTED", {"slug": "email-polisher"})
    assert _code(loader.load, "email-polisher")[0] == "NOT_ADOPTED"
    library.adopt("email-polisher")
    assert _code(library.adopt, "email-polisher") == ("ALREADY_ADOPTED", {"slug": "email-polisher"})
    (native / "email-polisher").mkdir()
    code, details = _code(library.release, "email-polisher")
    assert code == "TARGET_EXISTS" and details == {"paths": [str(native / "email-polisher")]}


def test_codes_on_learnings(tmp_home):
    install_fixture_skills(tmp_home / "native")
    discovery.sync_db()
    library.adopt("email-polisher")
    assert _code(learnings.record, "email-polisher", "   ") == ("INVALID_INPUT", {"field": "body"})
    assert _code(learnings.retire, 99) == ("NOT_FOUND", {"id": 99})
    assert _code(learnings.require_adopted, "meeting-notes")[0] == "NOT_ADOPTED"
    store.set_setting("learning", "off")
    assert _code(learnings.record, "email-polisher", "x") == ("LEARNING_OFF", {})


def test_load_of_vanished_library_folder_is_source_missing(tmp_home):
    import shutil

    from skillswiki import paths
    install_fixture_skills(tmp_home / "native")
    discovery.sync_db()
    library.adopt("email-polisher")
    shutil.rmtree(paths.library_dir() / "email-polisher")
    assert _code(loader.load, "email-polisher")[0] == "SOURCE_MISSING"
