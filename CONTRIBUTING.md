# Contributing

Thanks for helping. Skills Wiki is source-available under the Elastic License 2.0 with an Additional
Limitation (see LICENSE).

## Setup

```bash
python3.11 -m venv .venv && .venv/bin/pip install -e . -r requirements-dev.txt
.venv/bin/python -m pytest tests -q
```

## Rules

- **No new dependencies** without an issue first. The runtime needs only `fastmcp` and `httpx`.
- **No third-party skills** in pull requests, not even as test fixtures. Write small stub skills instead
  (see `tests/fixtures/`).
- Tests first: every change comes with a test, and tests never touch your real home folder (the autouse
  fixture in `tests/conftest.py` takes care of that).
- Nothing in tests may call a real AI CLI or the TypeSafe API. Use the fakes in `tests/fakes.py`.

## Contributor licence grant

By submitting a contribution you confirm that you wrote it (or have the right to submit it) and you grant Skills
Wiki a perpetual, worldwide, irrevocable, royalty-free licence to use, modify, sublicense and relicense your
contribution, including in commercial products and services offered by Skills Wiki. You keep the copyright in
your contribution. Pull requests that do not agree to this cannot be merged.
