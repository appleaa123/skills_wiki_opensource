---
name: "CSV Cleaner"
description: 'Cleans messy CSV files: trims, dedupes, normalises dates.'
---

# CSV cleaner

Run `scripts/clean.py` on the user's CSV, then summarise what changed.

## Rules
- Never drop a row without saying why.
- Dates become ISO 8601 (YYYY-MM-DD).
- Keep the original column order.
