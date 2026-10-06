"""Test helpers: install fixture skills into a fake native root."""
import shutil
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"
SKILL_NAMES = ("email-polisher", "meeting-notes", "csv-cleaner")


def install_fixture_skills(root: Path, names=SKILL_NAMES) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for name in names:
        shutil.copytree(FIXTURES / "skills" / name, root / name)
    return root


def scripted(answers):
    """An Io whose ask() returns the given answers in order, then raises Stop (input ended)."""
    from skillswiki.setup_flow import Io, Stop
    said, queue = [], list(answers)

    def ask(question, choices):
        said.append(question)
        if not queue:
            raise Stop("Input ended.")
        answer = queue.pop(0)
        assert not choices or answer in choices, (question, choices)  # () = free text
        return answer
    return Io(say=said.append, ask=ask), said


def two_agents() -> Path:
    """Claude Code with email-polisher and Codex with csv-cleaner, in the fake home."""
    home = Path.home()
    install_fixture_skills(home / ".claude" / "skills", ["email-polisher"])
    install_fixture_skills(home / ".agents" / "skills", ["csv-cleaner"])
    (home / ".codex").mkdir()
    return home


def snapshot(root: Path) -> dict:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}
