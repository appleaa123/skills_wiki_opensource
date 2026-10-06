import json
import os
import subprocess
from pathlib import Path
import sys

from conftest import needs_symlinks
from helpers import install_fixture_skills

from skillswiki import cli


def run(capsys, *argv):
    code = cli.main(list(argv))
    out = capsys.readouterr()
    return code, out.out, out.err


def test_end_to_end_flow(tmp_home, capsys):
    install_fixture_skills(tmp_home / "native")
    code, out, _ = run(capsys, "scan")
    assert code == 0 and "3 skills found" in out
    code, out, _ = run(capsys, "adopt", "email-polisher")
    assert code == 0 and "Adopted email-polisher" in out
    code, out, _ = run(capsys, "--json", "suggest", "fix my email draft")
    assert json.loads(out)["shortlist"][0]["skill"] == "email-polisher"
    code, out, _ = run(capsys, "learn", "add", "email-polisher", "Sign off with Best.")
    assert code == 0 and "#1" in out
    code, out, _ = run(capsys, "load", "email-polisher")
    assert "Email polisher" in out and "Sign off with Best." in out
    code, out, _ = run(capsys, "--json", "list", "--status", "adopted")
    assert [r["slug"] for r in json.loads(out)] == ["email-polisher"]
    code, out, _ = run(capsys, "release", "email-polisher")
    assert code == 0 and (tmp_home / "native" / "email-polisher").is_dir()


def test_card_set_show_delete(tmp_home, capsys):
    install_fixture_skills(tmp_home / "native")
    run(capsys, "scan")
    code, _, _ = run(capsys, "card", "set", "meeting-notes", "--examples", "summarise this call|notes please",
                     "--keywords", "minutes")
    assert code == 0
    _, out, _ = run(capsys, "--json", "card", "show", "meeting-notes")
    assert json.loads(out)["examples"] == ["summarise this call", "notes please"]
    run(capsys, "card", "delete", "meeting-notes")
    _, out, _ = run(capsys, "card", "show", "meeting-notes")
    assert "No card" in out


def test_errors_exit_1_without_traceback(tmp_home, capsys):
    code, _, err = run(capsys, "adopt", "nope")
    assert code == 1 and "not found" in err and "Traceback" not in err


def test_config_learning(tmp_home, capsys):
    _, out, _ = run(capsys, "config", "get", "learning")
    assert "learning = on" in out
    run(capsys, "config", "set", "learning", "off")
    code, _, err = run(capsys, "learn", "add", "x", "text")
    assert code == 1 and "Learning Mode is off" in err
    code, _, err = run(capsys, "config", "set", "learning", "maybe")
    assert code == 1


def test_console_script_help():
    import sysconfig
    # console scripts live in the interpreter's scripts dir (bin/ in a venv, Scripts\ on Windows)
    script = Path(sysconfig.get_path("scripts")) / ("skillswiki.exe" if sys.platform == "win32" else "skillswiki")
    result = subprocess.run([str(script), "--help"], capture_output=True, text=True)
    assert result.returncode == 0 and "adopt" in result.stdout


def test_release_all_cli(tmp_home, capsys):
    install_fixture_skills(tmp_home / "native")
    run(capsys, "scan")
    run(capsys, "adopt", "email-polisher")
    run(capsys, "adopt", "csv-cleaner")
    code, out, _ = run(capsys, "release", "--all")
    assert code == 0 and "Released 2 skills back to their folders" in out
    code, out, _ = run(capsys, "release", "--all")
    assert code == 0 and "No adopted skills" in out
    code, _, err = run(capsys, "release")
    assert code == 1 and "slug or --all" in err


def test_adopt_dry_run_moves_nothing(tmp_home, capsys):
    native = install_fixture_skills(tmp_home / "native")
    run(capsys, "scan")
    code, out, _ = run(capsys, "adopt", "email-polisher", "--dry-run")
    assert code == 0 and out.startswith("Would adopt email-polisher:")
    assert (native / "email-polisher").is_dir()
    _, out, _ = run(capsys, "--json", "list", "--status", "adopted")
    assert json.loads(out) == []
    code, out, _ = run(capsys, "--json", "adopt", "email-polisher", "--dry-run")
    assert code == 0 and json.loads(out)["to"].endswith("email-polisher")


def test_adopt_dry_run_on_vanished_folder(tmp_home, capsys):
    import shutil
    native = install_fixture_skills(tmp_home / "native")
    run(capsys, "scan")
    shutil.rmtree(native / "email-polisher")
    code, _, err = run(capsys, "adopt", "email-polisher", "--dry-run")
    assert code == 1 and "no longer at" in err
    _, out, _ = run(capsys, "--json", "list", "--status", "native")
    assert "email-polisher" in [r["slug"] for r in json.loads(out)]  # the row is untouched until the next scan


def test_release_dry_run(tmp_home, capsys):
    native = install_fixture_skills(tmp_home / "native")
    run(capsys, "scan")
    run(capsys, "adopt", "email-polisher")
    run(capsys, "adopt", "csv-cleaner")
    code, out, _ = run(capsys, "release", "email-polisher", "--dry-run")
    assert code == 0 and out.startswith("Would release email-polisher:")
    assert not (native / "email-polisher").exists()
    code, out, _ = run(capsys, "release", "--all", "--dry-run")
    assert code == 0 and "Would release 2 skills" in out
    assert not (native / "csv-cleaner").exists()
    code, out, _ = run(capsys, "release", "--all")
    assert code == 0 and "Released 2 skills" in out and (native / "csv-cleaner").is_dir()


@needs_symlinks
def test_adopt_dry_run_prints_foreign_link_warning(tmp_home, capsys):
    real = install_fixture_skills(tmp_home / "other-tool" / "skills", ["meeting-notes"]) / "meeting-notes"
    os.symlink(real, tmp_home / "native" / "meeting-notes")
    run(capsys, "scan")
    code, out, _ = run(capsys, "adopt", "meeting-notes", "--dry-run")
    assert code == 0 and "warning:" in out and "another tool" in out


def test_json_error_shape(tmp_home, capsys):
    code, out, err = run(capsys, "--json", "adopt", "nope")
    assert code == 1 and out == ""
    payload = json.loads(err)
    assert payload == {"ok": False, "code": "NOT_FOUND", "message": payload["message"], "details": {"slug": "nope"}}
    assert "not found" in payload["message"]


def test_json_error_shape_for_bare_value_error(tmp_home, capsys):
    code, _, err = run(capsys, "--json", "release")
    assert code == 1 and json.loads(err) == {"ok": False, "code": "INVALID_INPUT",
                                             "message": "give a skill slug or --all", "details": {}}


def test_agents_command(tmp_home, capsys):
    install_fixture_skills(tmp_home / "userhome" / ".cursor" / "skills", ["email-polisher"])
    code, out, _ = run(capsys, "agents")
    assert code == 0 and "claude_code" in out and "Cursor" in out
    _, out, _ = run(capsys, "--json", "agents")
    cursor = next(r for r in json.loads(out) if r["key"] == "cursor")
    assert cursor["detected"] is True and cursor["skills"] == 1


def test_doctor_command(tmp_home, capsys):
    code, out, _ = run(capsys, "doctor")
    assert code == 0 and "version" in out and "database" in out
    code, out, _ = run(capsys, "--json", "doctor")
    assert code == 0 and json.loads(out)["db"]["present"] is False


def test_doctor_export_command(tmp_home, capsys):
    code, out, _ = run(capsys, "doctor", "--export")
    assert code == 0 and "skillswiki-doctor-" in out and "report.json" in out
    assert any(p.name.startswith("skillswiki-doctor-") for p in (tmp_home / "work").iterdir())
