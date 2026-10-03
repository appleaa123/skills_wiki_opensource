You are writing an evaluation suite for an AI skill. The suite measures whether outputs produced WITH the skill
loaded are better than outputs produced WITHOUT it, so the tasks must leave room for the skill to make a difference.

Return ONLY one JSON object, no prose, in this shape:
{"tasks": [ ... ], "rubric": {"dimensions": [ ... ], "items": [ ... ], "applied": [ ... ]}}

TASKS — exactly 6 objects, each:
{"id": "t01".."t06", "skill": "<<SLUG>>", "prompt": "...", "inputs": {...}, "expected": null,
 "verifier": "rubric", "tags": ["..."]}
Rules for tasks:
1. Each prompt is what a real user would type to an assistant that has this skill loaded. Write it the way a
   person asks for help, in their own words.
2. A prompt must NOT restate, paraphrase or hint at the skill's own rules, steps or style guidance. If the prompt
   tells the assistant how to do the job, the no-skill answer will score as well as the skill answer and the
   benchmark measures nothing.
3. At least 2 tasks are tagged "implicit": a normal request with no instruction about style or method at all, so
   the skill's default behaviour is measured.
4. At least 1 task tests a failure mode the skill warns against (for example, inputs with a fact missing, to test
   that nothing is invented). Tag it with a short name for that failure mode.
5. "inputs" holds realistic material the task needs (a draft, data, notes), as a JSON object. Vary the tasks: not
   one task with a word changed.

RUBRIC — criteria are graded 0/1/2 by a judge who sees only the task and the final output:
- "dimensions": exactly these 3 ids, each with a "desc" specialised to this skill:
  "failure_mechanism" (the output avoids the specific failure this skill exists to prevent — name it),
  "actionable_specificity" (the output is ready to use as-is, not notes about what it should contain),
  "high_risk_blacklist" (the output contains none of this domain's risky claims or actions — name them).
- "items": 3 to 6 skill-specific checks, each {"id": "snake_case", "desc": "...", "weight": 1}.
- "applied": 1 to 4 rules taken from the skill's own text, each {"id": "snake_case", "desc": "..."}.
Rules for every criterion "desc":
6. Phrase it affirmatively: describe what a good output does ("Keeps every number from the inputs"), not a
   negation ("Does not invent numbers").
7. A grader must be able to decide it from the output text alone.
Optional per criterion: "kind" ("factual" | "constraint" | "count" | "style" | "legal") and "risk" ("low" | "high";
use "high" for legal, safety or money rules).

THE SKILL (slug "<<SLUG>>"):
<<SKILL>>
