# Quickstart

Skills Wiki runs on your machine. It needs Python 3.11+ and at least one AI CLI (Claude Code, Codex or Gemini CLI).

## 1. Install

Install it so your AI agent can find the `skillswiki` command (a virtualenv is not on the agent's PATH):

```bash
pipx install git+https://github.com/appleaa123/skills_wiki_opensource.git
```

Or from a clone: `pipx install .` — or use the absolute path of the console script, e.g.
`/path/to/skills_wiki_opensource/.venv/bin/skillswiki`, everywhere `skillswiki` appears below.

Optional settings (your own TypeSafe key for JEV, extra skill folders):

```bash
mkdir -p ~/.skillswiki && cp .env.example ~/.skillswiki/.env   # then edit it
```

## 2. Find and adopt your skills

```bash
skillswiki scan                 # finds skills in ~/.claude/skills, ~/.codex/skills, ~/.agents/skills, ~/.gemini/skills
                                # and the same folders under your current project
skillswiki list
skillswiki adopt <slug>         # moves the skill into ~/.skillswiki/library — your agent stops auto-triggering it
skillswiki release <slug>       # moves it back, byte-for-byte
```

Plugin-managed skills are listed but never moved. If the same skill is installed for several agents, identical
copies move with it and come back on release; a copy that differs stays where it is and `adopt` warns you.

## 3. Connect your agent

**Any MCP client (Claude Desktop, Claude Code, Cursor, …)** — register the stdio server. For Claude Code:

```bash
claude mcp add skills-wiki -- skillswiki serve-mcp
```

The agent gets five tools: `suggest_skill`, `load_skill`, `list_skills`, `learning_record`, `learning_list`.

**Claude Code hook (optional)** — routes every prompt automatically. Add to `.claude/settings.json`
(project) or `~/.claude/settings.json` (all projects):

```json
{
  "hooks": {
    "UserPromptSubmit": [
      {"hooks": [{"type": "command", "command": "skillswiki hook"}]}
    ]
  }
}
```

The hook adds one line ("possibly relevant skills: …") to the context; it never pastes the skill itself and never
blocks your prompt.

## 4. Try it

```bash
skillswiki suggest "fix my email draft"
skillswiki load <slug>
skillswiki learn add <slug> "Always sign off with 'Best'."
```

If keyword matching misses requests you expect to match, give the skill a routing card:
`skillswiki card set <slug> --examples "this sounds robotic|make it human" --keywords "tone|robotic"`, or let your
own AI draft one with `skillswiki enrich <slug>` (uses your tokens; never automatic).
