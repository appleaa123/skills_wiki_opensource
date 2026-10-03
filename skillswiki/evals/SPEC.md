# Eval spec

Each adopted skill that wants a measured scorecard has its own suite (one skill = one suite; "pack" below means
that skill). `skillswiki eval generate <slug>` drafts one; you can also write or edit it by hand.

```
~/.skillswiki/suites/<slug>/
  tasks.jsonl   # one JSON object per line; "skill" must equal <slug>
  rubric.json   # judge rubric
  verify.py     # optional deterministic checks, stdlib only
  suite.json    # status: draft | checked
```

## `tasks.jsonl` — one line per task

```json
{"id":"t01","skill":"<slug>","prompt":"...","inputs":{...},
 "expected":null,"verifier":"deterministic"|"rubric"|"both","tags":["copy","email"]}
```

**Grading-v2 prompt rules (P1.4d):**
1. Prompts are what a user would type with the skill loaded. A prompt must not restate the skill's rules — a restating prompt makes the no-skill arm score like the skill arm (shows up as LOW_HEADROOM / LOW_SIGNAL_TASKS).
2. Include at least two `implicit` tasks (tag `implicit`): a normal writing/work request with no style instruction, so the skill's default behaviour is measured.

## `rubric.json`

```json
{"dimensions":[
   {"id":"failure_mechanism","desc":"Output shows awareness of the common failure modes for this task and avoids them."},
   {"id":"actionable_specificity","desc":"Output is specific enough to use as-is; no placeholders or vague advice."},
   {"id":"high_risk_blacklist","desc":"Output contains none of the banned actions/claims listed for this pack."}],
 "items":[{"id":"has_cta","desc":"Ends with one clear call to action.","weight":1}, ...],
 "applied":[{"id":"rule1","desc":"TODO: one rule from the skill's own text, phrased affirmatively."}]}
```

The three `dimensions` are mandatory on every pack (SkillLens: a judge guided by
these three dimensions picks the higher-utility skill document 73.8% of the time
vs. 46.4% unguided, arXiv 2605.23899 §6 — that result is about selecting skill
text; the output-grading rubric here is Skills Wiki's adaptation and has not
been validated against human graders). `items` are pack-specific, 3–6 per pack.

**Grading-v2 rubric rules (P1.4d):**
3. Only the final deliverable is graded (`evals/deliverable.py`). Working notes are stripped; `paste_ready` is reported, not scored.
4. Phrase every criterion affirmatively (what a good output does), not as a negation — negation-heavy rubric text biases LLM judges toward fail (arXiv 2609.02942).
5. `applied` (optional): the skill's own rules, judged per output on the same 0/1/2 scale, reported as `applied_rate`; excluded from pass/`rubric_score`.
6. Criterion properties (optional; used by the JEV cascade). LLM judges never see them:
   - `kind`: `factual` (fidelity to inputs) | `constraint` ("no X" rule) | `count` (length/number rule) | `style` | `legal`.
     Untagged = treated as `factual` (the cautious default: JEV may only fail it). Tag `style` to let JEV pass it too.
   - `risk`: `low` (default) | `high` (legal, safety, money: always graded by the LLM judge).
   - `verify`: id of a check that covers this criterion. List the ids whose FAIL can be trusted without a judge in `verify.py`'s `STRICT = {...}`.
     Every `verify()` check is a pass/fail gate in every run; a check that should only decide a criterion (e.g. a length rule
     in `applied`) goes in an optional `criterion_checks(task, output)` in the same file instead.
   - `levels`: three strings describing a 0, 1 and 2 output (concrete situations grade more consistently than degree words).
   Check them with `skillswiki eval check <slug>` (it suggests `kind` from the wording and flags wording
   that may be legal or high-risk for a person to confirm).

## `verify.py`

```python
def verify(task: dict, output: str) -> dict[str, bool]:
    # return {"check_id": True/False, ...}; keep pure, no network
```

Stdlib only — no new dependency for a verifier to run in CI.
