---
name: skills-wiki
description: Ask Skills Wiki which of the user's installed skills fits before any task that may need a documented procedure or a specialised workflow (writing in a set style, a domain workflow, a file-processing routine). Use when the user's request might be covered by a skill they installed, when they name a skill, or when they correct how a skill was applied.
---

Skills Wiki manages the AI skills this user installed. Skills it has adopted are no longer in your own skills
folder: Skills Wiki routes and loads them. Before a task that may need a specialised, documented procedure
(writing in a set style, a domain workflow, a file-processing routine), ask it which skill fits. It never forces
a skill on you; if nothing fits, proceed without one.

## If you have the Skills Wiki tools (suggest_skill, load_skill, list_skills, learning_record, learning_list)

1. Call `suggest_skill` with the user's request in their own words.
2. If it returns a `skill`, call `load_skill` with that slug and follow the skill. If it returns only a
   `shortlist`, pick one that clearly fits and load it, or proceed without a skill.
3. `load_skill` returns the skill's folder on disk: read or run its files from there. Scripts run with the
   user's permissions.
4. If the user corrects how a skill is applied, or asks you to remember something specific to a skill
   ("remember this", "from now on"), record it with `learning_record` (call `learning_list` first to avoid a
   near-duplicate). If Learning Mode is off, tell the user they can turn it on with:
   `skillswiki config set learning on`.

## If you only have a shell

1. `skillswiki --json suggest "<the user's request>"` prints `{"skill": ..., "shortlist": [...]}`.
2. `skillswiki load <slug>` prints the skill with the user's learnings; follow it.
3. `skillswiki learn add <slug> "<the correction, as an instruction for next time>"` records a learning.

A general preference unrelated to any skill (for example how you format every reply) is not a Skills Wiki
learning; it belongs in your own persistent memory, if you have one.
