"""The agents whose skill folders Skills Wiki scans, as data: add a row to support an agent.

`skills_dirs` are user-level folders relative to the home directory; the first is where `skillswiki setup`
installs the Skills Wiki skill. `detect_dir` is a folder whose presence means the agent is installed. Row order
is adoption priority: when the same skill sits in several folders, the first folder's copy is the one adopted.
The path list is adapted from skills-manager's agent table (github.com/xingkongliang/skills-manager, MIT) and
the Antigravity docs (antigravity.google/docs/skills); no code was copied.
"""
from dataclasses import dataclass
from pathlib import Path

from skillswiki import frontmatter
from skillswiki.errors import SkillsWikiError

# Project-level folders (relative to the working directory). Only these agents read project skills.
PROJECT_SKILL_DIRS = (".claude/skills", ".agents/skills", ".codex/skills", ".gemini/skills", ".agent/skills")
# Project-level readers beyond the agents whose user-level folder has the same name: Antigravity reads
# <workspace>/.agents/skills (legacy .agent/skills) but has no user-level folder of that name.
PROJECT_EXTRA_READERS = {".agents/skills": ("antigravity",), ".agent/skills": ("antigravity",)}


@dataclass(frozen=True)
class Agent:
    key: str
    name: str
    skills_dirs: tuple[str, ...]
    detect_dir: str


AGENTS: tuple[Agent, ...] = (
    Agent("claude_code", "Claude Code", (".claude/skills",), ".claude"),
    Agent("codex", "Codex", (".agents/skills", ".codex/skills"), ".codex"),  # .codex/skills is legacy
    Agent("gemini_cli", "Gemini CLI", (".gemini/skills", ".agents/skills"), ".gemini"),
    Agent("antigravity", "Antigravity (IDE)", (".gemini/config/skills", ".gemini/antigravity/skills"),
          ".gemini/antigravity"),  # second entry is the pre-2.0 folder
    Agent("antigravity_cli", "Antigravity CLI", (".gemini/antigravity-cli/skills",), ".gemini/antigravity-cli"),
    Agent("github_copilot", "GitHub Copilot", (".copilot/skills", ".agents/skills"), ".copilot"),
    Agent("cursor", "Cursor", (".cursor/skills",), ".cursor"),
    Agent("windsurf", "Windsurf", (".codeium/windsurf/skills",), ".codeium/windsurf"),
    Agent("opencode", "OpenCode", (".config/opencode/skills",), ".config/opencode"),
    Agent("cline", "Cline", (".agents/skills",), ".cline"),
    Agent("warp", "Warp", (".agents/skills",), ".warp"),
    Agent("amp", "Amp", (".config/agents/skills",), ".config/agents"),
    Agent("goose", "Goose", (".config/goose/skills",), ".config/goose"),
    Agent("roo_code", "Roo Code", (".roo/skills",), ".roo"),
    Agent("kilo_code", "Kilo Code", (".kilocode/skills",), ".kilocode"),
    Agent("qwen_code", "Qwen Code", (".qwen/skills",), ".qwen"),
    Agent("openhands", "OpenHands", (".openhands/skills",), ".openhands"),
    Agent("continue", "Continue", (".continue/skills",), ".continue"),
    Agent("droid", "Droid", (".factory/skills",), ".factory"),
    Agent("kiro", "Kiro CLI", (".kiro/skills",), ".kiro"),
    Agent("hermes", "Hermes Agent", (".hermes/skills",), ".hermes"),
)


def user_skill_dirs() -> list[str]:
    """Every agent's user-level folders in table order, each listed once."""
    seen: list[str] = []
    for agent in AGENTS:
        seen += [d for d in agent.skills_dirs if d not in seen]
    return seen


def by_key(key: str) -> Agent:
    for agent in AGENTS:
        if agent.key == key:
            return agent
    raise SkillsWikiError("INVALID_INPUT", f"unknown agent {key!r}; known: {', '.join(a.key for a in AGENTS)}",
                          field="agent")


def detected(agent: Agent) -> bool:
    return (Path.home() / agent.detect_dir).is_dir()


def _count(folder: Path) -> int:
    if not folder.is_dir():
        return 0
    return sum(1 for c in folder.iterdir() if c.is_dir() and (c / frontmatter.SKILL_FILE).is_file())


def skill_count(agent: Agent) -> int:
    return sum(_count(Path.home() / d) for d in agent.skills_dirs)


def keys_for(path: Path) -> list[str]:
    """Keys of the agents that read this user-level or project-level folder (empty for a folder no agent owns)."""
    target = Path(path).resolve()
    for d in PROJECT_SKILL_DIRS:
        if (Path.cwd() / d).resolve() == target:
            readers = [a.key for a in AGENTS if d in a.skills_dirs] + list(PROJECT_EXTRA_READERS.get(d, ()))
            return [a.key for a in AGENTS if a.key in readers]  # table order
    return [a.key for a in AGENTS if any((Path.home() / d).resolve() == target for d in a.skills_dirs)]


def rows() -> list[dict]:
    return [{"key": a.key, "name": a.name, "detected": detected(a), "skills": skill_count(a),
             "dirs": [str(Path.home() / d) for d in a.skills_dirs]} for a in AGENTS]
