"""How each agent is wired to ask Skills Wiki, as data: where its hook, rules file and MCP settings live and in what
shape. Facts come from each agent's docs (source URL + date checked); see plan/research_agent_wiring.md.
Paths are relative to the home directory."""
from dataclasses import dataclass

from skillswiki.errors import SkillsWikiError

MCP_NAME = "skillswiki"
CHECKED = "2026-10-06"
RULES_TEXT = ("Skills Wiki manages this user's AI skills. Before a task that may need a specialised or documented "
              "procedure, ask Skills Wiki which skill fits: call its `suggest_skill` tool, or run "
              "`skillswiki suggest \"<request>\"`, then load the skill it names. If nothing fits, continue normally.")


@dataclass(frozen=True)
class Target:
    path: str
    kind: str
    detail: dict
    source: str
    checked: str = CHECKED

    def __hash__(self) -> int:  # detail is a dict; identity of a target is its file and kind
        return hash((self.path, self.kind))


@dataclass(frozen=True)
class Wiring:
    agent: str
    hook: Target | None
    hook_format: str | None
    rules: Target | None
    mcp: Target | None
    notes: str = ""


def hook_command(agent: str) -> str:
    return f"skillswiki hook --agent {agent}"


def _json_hook(path, keys, agent, source, group=True, timeout=None, legacy=()):
    return Target(path, "json_hook", {"keys": list(keys), "group": group, "command": hook_command(agent),
                                      "timeout": timeout, "legacy": list(legacy)}, source)


def _mcp(path, source, key="mcpServers", entry="std"):
    return Target(path, "json_mcp", {"key": key, "entry": entry}, source)


def _md(path, source):
    return Target(path, "md_block", {}, source)


def _own_md(path, source):  # a rules file of our own in a rules folder; same marked block, so user notes survive
    return Target(path, "md_block", {}, source)


def _yaml(path, snippet, source):
    return Target(path, "self_setup", {"lang": "yaml", "snippet": snippet, "audience": "agent", "where": path},
                  source)


CLAUDE_DOCS = "https://code.claude.com/docs/en/hooks"
_GEMINI_RULES = _md(".gemini/GEMINI.md", "https://www.antigravity.google/docs/rules")
_AG_HOOK = _json_hook(".gemini/config/hooks.json", ["skillswiki", "PreInvocation"], "antigravity",
                      "https://www.antigravity.google/docs/hooks", group=False, timeout=5)
_AG_MCP = _mcp(".gemini/config/mcp_config.json", "https://www.antigravity.google/docs/mcp")
HERMES_SNIPPET = "hooks:\n  pre_llm_call:\n    - command: skillswiki hook --agent hermes  # skillswiki"
GOOSE_SNIPPET = ("extensions:\n  skillswiki:  # skillswiki\n    type: stdio\n    cmd: skillswiki\n"
                 "    args: [serve-mcp]\n    enabled: true")

WIRINGS: tuple[Wiring, ...] = (
    Wiring("claude_code",
           _json_hook(".claude/settings.json", ["hooks", "UserPromptSubmit"], "claude_code", CLAUDE_DOCS,
                      legacy=["skillswiki hook"]), "claude",
           _md(".claude/CLAUDE.md", "https://code.claude.com/docs/en/memory"),
           Target(".claude.json", "cli", {"add": ["claude", "mcp", "add", "--transport", "stdio", MCP_NAME, "--scope",
                                                  "user", "--", "skillswiki", "serve-mcp"],
                                          "remove": ["claude", "mcp", "remove", MCP_NAME, "--scope", "user"],
                                          "check_file": ".claude.json", "check_key": "mcpServers"},
                  "https://code.claude.com/docs/en/mcp")),
    Wiring("codex", _json_hook(".codex/hooks.json", ["hooks", "UserPromptSubmit"], "codex",
                               "https://learn.chatgpt.com/docs/hooks"), "claude",
           _md(".codex/AGENTS.md", "https://developers.openai.com/codex/guides/agents-md"),
           Target(".codex/config.toml", "toml_block", {}, "https://learn.chatgpt.com/docs/extend/mcp")),
    Wiring("gemini_cli", _json_hook(".gemini/settings.json", ["hooks", "BeforeAgent"], "gemini_cli",
                                    "https://geminicli.com/docs/hooks/reference/"), "gemini",
           _GEMINI_RULES,
           _mcp(".gemini/settings.json", "https://geminicli.com/docs/tools/mcp-server/")),
    Wiring("antigravity", _AG_HOOK, "antigravity", _GEMINI_RULES, _AG_MCP),
    Wiring("antigravity_cli", _AG_HOOK, "antigravity", _GEMINI_RULES, _AG_MCP),
    Wiring("github_copilot", None, None,
           _md(".copilot/copilot-instructions.md", "https://docs.github.com/en/copilot/how-tos/copilot-cli/"
               "customize-copilot/add-custom-instructions"),
           _mcp(".copilot/mcp-config.json", "https://docs.github.com/en/copilot/how-tos/copilot-cli/"
                "customize-copilot/add-mcp-servers", entry="copilot")),
    Wiring("cursor", None, None,
           Target("Cursor Settings → Rules → User Rules", "self_setup",
                  {"lang": "text", "snippet": RULES_TEXT, "audience": "user",
                   "where": "Cursor Settings → Rules → User Rules"}, "https://cursor.com/docs/context/rules"),
           _mcp(".cursor/mcp.json", "https://cursor.com/docs/context/mcp")),
    Wiring("windsurf", None, None,
           _md(".codeium/windsurf/memories/global_rules.md", "https://docs.devin.ai/desktop/cascade/memories"),
           _mcp(".config/devin/mcp_config.json", "https://docs.devin.ai/desktop/cascade/mcp")),
    Wiring("opencode", None, None,
           _md(".config/opencode/AGENTS.md", "https://opencode.ai/docs/rules/"),
           _mcp(".config/opencode/opencode.json", "https://opencode.ai/docs/mcp-servers/", key="mcp",
                entry="opencode")),
    Wiring("cline", None, None,  # its hook's input shape is no longer in the docs (Task 1)
           _own_md(".cline/rules/skillswiki.md", "https://docs.cline.bot/getting-started/config.md"),
           _mcp(".cline/data/settings/cline_mcp_settings.json", "https://docs.cline.bot/getting-started/config.md")),
    Wiring("warp", None, None, None, None),
    Wiring("amp", None, None, _md(".config/amp/AGENTS.md", "https://ampcode.com/manual"),
           _mcp(".config/amp/settings.json", "https://ampcode.com/manual/mcp.md", key="amp.mcpServers")),
    Wiring("goose", None, None, _md(".config/goose/.goosehints", "https://goose-docs.ai/docs/guides/config-files"),
           _yaml(".config/goose/config.yaml", GOOSE_SNIPPET, "https://goose-docs.ai/docs/guides/config-files")),
    Wiring("roo_code", None, None,
           _own_md(".roo/rules/skillswiki.md", "https://roocodeinc.github.io/Roo-Code/features/custom-instructions"),
           None),
    Wiring("kilo_code", None, None, None, None),  # global rules moved into kilo.jsonc (Task 1)
    Wiring("qwen_code", _json_hook(".qwen/settings.json", ["hooks", "UserPromptSubmit"], "qwen_code",
                                   "https://qwenlm.github.io/qwen-code-docs/en/users/features/hooks/"), "claude",
           _md(".qwen/QWEN.md", "https://qwenlm.github.io/qwen-code-docs/en/users/features/memory/"),
           _mcp(".qwen/settings.json", "https://qwenlm.github.io/qwen-code-docs/en/users/features/mcp/")),
    Wiring("openhands", None, None, None, None),
    Wiring("continue", None, None, None, None),  # global rules and config paths are not in the current docs
    Wiring("droid", _json_hook(".factory/hooks.json", ["UserPromptSubmit"], "droid",
                               "https://docs.factory.com/reference/hooks-reference"), "claude",
           _md(".factory/AGENTS.md", "https://docs.factory.com/cli/configuration/agents-md"), None),
    Wiring("kiro", None, None,  # hooks are project-only since Kiro CLI 3.0 (Task 1)
           _own_md(".kiro/steering/skillswiki.md", "https://kiro.dev/docs/steering/"), None),
    Wiring("hermes", _yaml(".hermes/config.yaml", HERMES_SNIPPET,
                           "https://hermes-agent.nousresearch.com/docs/user-guide/features/hooks"), "hermes",
           None, None),
)


def by_key(key: str) -> Wiring:
    for w in WIRINGS:
        if w.agent == key:
            return w
    raise SkillsWikiError("INVALID_INPUT", f"no wiring known for agent {key!r}", field="agent")
