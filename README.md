# Skills Wiki (self-hosted)

**Manage the AI skills you already installed.** Skills Wiki routes each request to the right skill, remembers
your corrections as learnings, and evaluates and benchmarks your skills on your own tokens. Everything runs on
your machine, with no account and no server of ours.

- **Route**: one router for all your skills, instead of every skill's description competing in your agent's context.
- **Learn**: corrections you make ("from now on, sign off with Best") are attached to that skill every time it loads.
- **Benchmark**: generate a test suite for any skill, run it with and without the skill, and see whether it helps.

Optional: bring your own [TypeSafe](https://typesafe.ai) key and **JEV**, a calibrated decision model, routes your
requests, checks your test suites and grades through a cascade. Without a key, everything still works.

Works with skills for **Claude Code, Codex and Gemini CLI** (folders containing a `SKILL.md`).

## Quickstart

```bash
pipx install git+https://github.com/appleaa123/skills_wiki_opensource.git
skillswiki scan                    # find installed skills
skillswiki adopt <slug>            # let Skills Wiki route and load it (reversible: skillswiki release <slug>)
claude mcp add skills-wiki -- skillswiki serve-mcp    # or any MCP client
skillswiki ui                      # the local management page
```

Full walkthrough, including the Claude Code hook and other agents: [docs/quickstart.md](docs/quickstart.md).

## How routing works

**Adopt.** Your agent triggers a skill natively by reading every installed skill's description. With dozens of
skills those descriptions crowd the context and compete with each other. `skillswiki adopt <slug>` moves the skill
folder from your agent's skills directory into `~/.skillswiki/library/`. Your agent stops triggering it on its own,
and Skills Wiki routes it instead. `skillswiki release <slug>` moves it back, byte for byte. Plugin-managed skills
are listed but never moved. If the same skill is installed for several agents, `adopt` tells you which other
copies are still active.

**Without a key: keyword routing.** Each request is matched (BM25) against the skill's own `description`, the
trigger text its author wrote. Skills Wiki returns a shortlist and your agent decides. It never forces a skill.

**Routing cards.** Descriptions are often one terse sentence, so keyword matching misses synonyms ("this email
sounds robotic" never mentions "rewrite"). A routing card adds example requests, keywords and what the skill is
*not* for. Write one by hand (`skillswiki card set`), or click **Enrich** on the page (or run
`skillswiki enrich <slug>`) to have your own AI draft one from the `SKILL.md`. That uses your tokens, so it is
never automatic. The page shows "card: yes/no" for every skill. Cards live only in Skills Wiki's database, never
in your skill files.

**With a TypeSafe key: JEV routing.** JEV asks whether the request needs a skill at all, picks among the
candidates and checks the top picks' fit. It suggests a single skill only when it is confident (fit ≥ 0.85);
otherwise your agent gets the shortlist. On Skills Wiki's own catalog and frozen task set, at that bar JEV was
right **98.5%** of the time when it suggested, suggested on about **65%** of requests, and made **0%** wrong-tool and
**0%** needless suggestions. Your library is different, so treat that as a strong guide, not a promise. If JEV is
unreachable, routing falls back to keywords.

**Ways in.** The CLI (`skillswiki ...`) is the main interface. An MCP server over stdio gives agents five fixed
tools (`suggest_skill`, `load_skill`, `list_skills`, `learning_record`, `learning_list`), the same five whether you
have 5 skills or 500. An optional Claude Code `UserPromptSubmit` hook routes every prompt and adds one line of
advice; it never pastes the skill and never blocks your prompt.

## How evaluation works

<!-- OWNER WRITES THIS SECTION. Scaffold of the flow, for reference:
1. Generate a suite: `skillswiki eval generate <slug>` (your AI drafts tasks.jsonl + rubric.json from SKILL.md:
   prompts a real user would type, never restating the skill's rules; at least two "implicit" tasks; criteria
   phrased affirmatively). Review it: `skillswiki eval show <slug>`.
2. Check it before spending tokens: `skillswiki eval check <slug>` (schema, rubric lint, restatement heuristic;
   with a key, one batched JEV pass flags restating or unrealistic prompts and ungradeable criteria; costs fractions
   of a cent).
3. Run it on your tokens: `skillswiki eval run <slug> --tier screen|publish` (each task with and without the skill).
4. Grade: deterministic checks (optional verify.py) + an LLM judge + blind pairwise comparison. With a key,
   `--cascade`: JEV grades every criterion; its grade becomes final only where your own runs show it agrees with
   your judge (the calibration store starts empty). On Skills Wiki's study JEV was final on about 0–21% of grades.
5. Benchmark: verdict (gain / noise / loss), delta with a confidence interval, pairwise wins/losses/ties, tokens.
   `skillswiki eval accept <id>` sets the baseline; later runs are compared under the same conditions.
6. Learnings: `--tier learning` measures whether your recorded learnings make the skill better.
-->

## JEV, honestly

JEV (by TypeSafe) is a decision model: it does not write text. It answers typed questions (pick one, score
against levels, true or false) with calibrated probabilities. In Skills Wiki it:

- **routes** requests to your adopted skills (above);
- **checks** generated test suites before you spend tokens on them;
- **grades** in a cascade: it grades every criterion, but its grade only becomes final where your own runs have
  shown it agrees with your LLM judge. Fact rules and "no X" rules can only ever be *failed* by JEV, never passed.

What it does not do: replace your judge. On Skills Wiki's own study JEV's grade was final on about **0–21%** of
grades, and every output still needed one LLM call. Its value is cheap, calibrated triage, and routing. JEV is
not deterministic either: about 5.6% of picks changed between identical runs, so 1–2 point differences are noise.

Bring your own key: put `TYPESAFE_API_KEY=` in `~/.skillswiki/.env` (see `.env.example`). You are TypeSafe's
customer under their terms; Skills Wiki never sees your key. `SKILLSWIKI_JEV=off` turns it off.

## Costs and privacy

- **Tokens.** Every eval run, routing-card enrich and suite generation runs on your own AI plan. `eval run` shows a
  token estimate first and asks before the larger tiers. Tips: start with `--tier screen`, and judge with a
  *different* CLI (codex or gemini) when you have one, since a model grading its own outputs tends to favour them.
  If only one CLI is installed, pass `--judge claude` (for example) to opt in.
- **Privacy.** Everything stays on your computer (`~/.skillswiki/`). Nothing is sent anywhere except to your own
  AI CLI and, with a key, to TypeSafe.
- **Scripts.** A skill folder can contain scripts your agent may run. The page flags skills that contain scripts
  and skills whose files changed since you adopted them.

## Want it done for you?

Run it yourself, free. Or let [skillwiki.app](https://skillwiki.app) find, vet, maintain and benchmark skills for
you, with suites already written and tested, working in claude.ai, ChatGPT and other web chats, not just your
terminal.

## Licence

**Elastic License 2.0 with an Additional Limitation**: source-available (fair-code), not OSI open source. Free for
your own use, including inside a for-profit business. You may not sell it, offer it as a hosted service, or
install, run or support it for paying clients. Commercial licence: aliu@skillwiki.app. See [LICENSE](LICENSE).

Contributing: see [CONTRIBUTING.md](CONTRIBUTING.md). Security: see [SECURITY.md](SECURITY.md).
