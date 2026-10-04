"""Codex CLI backend. Not exercised for real in Phase 1 — correct in shape
only (unit-tested with subprocess.run mocked). Prompt is piped via stdin,
matching claude_cli.py — see that module's docstring for why (a skill body
starting with "---" breaks positional-arg parsing).

Raises BackendUnavailable on a failed call (non-zero exit, empty stdout, or
a detected quota/rate-limit message) instead of returning the failure text
as output — see skillswiki/evals/backends/__init__.py (P1.4c, 2026-09-09). Unlike
claude_cli.py, `codex exec` has no confirmed structured JSON output mode, so
`usage` is always None here — this backend is not included in a run's token
ledger. `profile` (the lean invocation profile, P1.4c) is accepted for
interface parity but not applied: its flags are Claude-CLI-specific."""
import subprocess
import sys

from . import TEXT_IO, Backend, BackendUnavailable, executable

_QUOTA_PATTERNS = ("session limit", "usage limit", "rate limit", "quota exceeded")


def _invoke(cmd: list[str], prompt: str, timeout: int) -> dict:
    try:
        result = subprocess.run(cmd, input=prompt, capture_output=True, timeout=timeout, **TEXT_IO)
    except Exception as exc:
        raise BackendUnavailable(f"codex exec failed to start: {exc}") from exc

    if result.returncode != 0:
        raise BackendUnavailable(f"codex exec exited {result.returncode}: {(result.stderr or '').strip()[:300]}")

    text = (result.stdout or "").strip()
    if not text:
        raise BackendUnavailable("codex exec returned empty stdout")
    if any(p in text.lower() for p in _QUOTA_PATTERNS):
        raise BackendUnavailable(f"codex exec returned a quota/limit message: {text[:200]!r}")

    return {"text": text, "usage": None}


class CodexCli(Backend):
    name = "codex"

    def run(self, prompt: str, mcp_config: dict | None, model: str | None,
            timeout: int = 180, profile: dict | None = None) -> dict:
        cmd = [executable("codex"), "exec"]
        if model:
            cmd += ["--model", model]
        return _invoke(cmd, prompt, timeout)

    def judge(self, prompt: str, model: str | None, timeout: int = 60,
              profile: dict | None = None) -> dict:
        cmd = [executable("codex"), "exec"]
        if model:
            cmd += ["--model", model]
        return _invoke(cmd, prompt, timeout)
