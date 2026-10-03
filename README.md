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
are listed but never moved. If the same skill is installed for several agents, identical copies move with it (and
come back on release), so no agent keeps triggering it on its own; a copy that differs stays put and `adopt` warns
you about it.

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
unreachable, or takes more than a few seconds, routing falls back to keywords. In a live test with five adopted
skills, each routed request used about 2,000–2,700 JEV tokens (about $0.0001); "what's the capital of France?"
was correctly told no skill was needed.

**Ways in.** The CLI (`skillswiki ...`) is the main interface. An MCP server over stdio gives agents five fixed
tools (`suggest_skill`, `load_skill`, `list_skills`, `learning_record`, `learning_list`), the same five whether you
have 5 skills or 500. An optional Claude Code `UserPromptSubmit` hook routes every prompt and adds one line of
advice; it never pastes the skill and never blocks your prompt.

## How evaluation works

A skill should make your AI's answers measurably better. Skills Wiki tests that the way you'd test any change:
same tasks, with and without the skill, graded blind.

**1. Generate a test suite.** Skills you install don't come with tests, so `skillswiki eval generate <slug>` asks
your own AI to draft one from the `SKILL.md`: six tasks a real user would type, plus a rubric to grade them. The
prompt carries hard-won rules. Tasks must not restate the skill's own instructions (if the task tells the AI how to
do the job, the no-skill answer scores just as well and the test measures nothing). At least two tasks are plain
requests with no style hints, to measure the skill's default behaviour. At least one tests a failure the skill
warns against. Criteria are phrased as what a good answer does. Review it with `skillswiki eval show <slug>` and
edit freely; it is yours.

**2. Check the suite before spending tokens.** `skillswiki eval check <slug>` validates the files, lints the
rubric, and flags task prompts that copy the skill's wording. With a TypeSafe key, JEV reads every prompt and
criterion in one batched call and flags prompts that restate the skill, prompts that don't read like a real
request, and criteria a grader couldn't decide from the answer alone.

**3. Run it.** `skillswiki eval run <slug> --tier screen` (quick) or `--tier publish` (each task, with and without
the skill, three times). Your AI CLI does the work on your plan; the token estimate is shown first.

**4. Grade it.** Every answer is graded against the rubric by a judge, ideally a different AI from the one that
did the work (`--judge gemini` while Claude works). The judge also compares the two answers to each task blind,
in both orders, and picks the better one. With a key, `--cascade` lets JEV grade every criterion too. Its grade
becomes final only where your own runs show it agrees with your judge; until then your judge decides and JEV's
grades are stored as evidence (`skillswiki eval calibration <slug>`).

**5. Read the benchmark.** A verdict (gain, noise or loss), the change in pass rate with a 95% confidence
interval, blind wins/losses/ties, token cost, and named findings such as a check that only the skill passes.
`skillswiki eval accept <id>` makes a run the baseline; later runs are compared with it only when measured the
same way (same AI, judge, settings and rubric).

**6. Learn.** Corrections you record (`skillswiki learn add`, or your agent via `learning_record`) are attached
to the skill every time it loads. `--tier learning_screen` measures whether they actually help.

### A real run

`llm-approach-advisor` (one of our own skills: it recommends prompting vs RAG vs fine-tuning for an LLM project),
on 2026-10-03. Claude did the work, Gemini judged, and the TypeSafe key was on.

- **Suite:** generated in 50 seconds (~49,000 tokens). Six tasks, e.g. *"A couple of people on the team think we
  should fine-tune a model on the docs so it 'really knows' the product. Is that the right call?"* JEV's check
  cost **$0.00015** and flagged two criteria as hard to grade from the answer alone.
- **Benchmark (publish tier, two runs, ~137,000 Claude tokens each, 6–10 minutes):** verdict **gain** both times.
  The judge preferred the answer written with the skill **18 of 18** times in each run. Pass rate went from 33% to
  67% in the first run and to 83% in the second, under identical settings: run-to-run noise is real, which is why
  the interval (−22 to +78 points, then +6 to +89) is shown next to every delta.
- **What the skill fixed:** naming rejected alternatives with reasons (100% with the skill vs 44% without) and
  including a safety note. **What it still missed:** stating the constraints it relied on (11% with the skill).
- **Learning:** one recorded learning targeting that gap ("list each constraint and label it from the inputs,
  inferred, or unknown") took the pass rate from 67% to **100%**, preferred **6 of 6** times blind (one quick run
  of six tasks; a small sample).
- **JEV in the cascade:** ~116,000 JEV tokens, about **$0.005** per run. JEV made the final call on **0 of 468**
  grades. That is the system working as designed: the generated rubric left criteria untagged, untagged criteria
  are treated as factual, and on factual criteria JEV may only *fail* an answer, never pass it. JEV rarely failed
  these good answers, so most criteria are still collecting evidence, and on two criteria it disagreed with the
  judge, so those stay with the judge. Tag criteria `"kind": "style"` where a lenient grader is harmless, and JEV
  can earn the right to settle them after enough agreeing runs.

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
