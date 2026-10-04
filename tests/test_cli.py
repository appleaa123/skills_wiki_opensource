import json
import subprocess
import sys

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
    bin_dir = __import__("pathlib").Path(sys.executable).parent
    result = subprocess.run([str(bin_dir / "skillswiki"), "--help"], capture_output=True, text=True)
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
