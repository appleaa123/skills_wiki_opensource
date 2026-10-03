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
