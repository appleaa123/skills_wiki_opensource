"""Claude Code CLI backend.

Prompt is piped via stdin, not passed as a positional CLI argument: a skill
body commonly starts with YAML frontmatter ("---"), and `claude -p <text>`
parses a leading "-" as an unknown option rather than the prompt — confirmed
empirically (P1.2a). Piping avoids that entirely and sidesteps OS argv
length limits for large skill-prefixed prompts.

Invokes with `--output-format json` (P1.4c, 2026-09-09) so a call's success
can be verified from the CLI's own structured signal (`is_error`, `subtype`,
`api_error_status`) instead of string-matching stdout, and so its real token
usage (`usage.input_tokens` etc.) is available to the runner's ledger —
measured on real calls, both fields are present on every response. Raises
BackendUnavailable on any failure (non-zero exit, an error envelope, empty
text, or a detected quota/rate-limit message) — see skillswiki/evals/backends/__init__.py
for why a failed call must never be scored as if it were model output.

`profile` (skillswiki/evals/config.json's "invocation_profile", P1.4c) trims the
~43,000-token boot tax of Claude Code's default agent context (system
prompt, every built-in tool schema, the MCP tool lists, the whole CLAUDE.md
chain) down to ~500 tokens for a trivial prompt, measured — none of that
context is relevant to grading a skill's output. It applies `--safe-mode`
(disables CLAUDE.md/skills/plugins/hooks/MCP while keeping subscription
auth — never `--bare`, whose auth is API-key-only and forbidden by §3.5),
`--tools ""`, `--strict-mcp-config`, `--setting-sources ""`, a short
system prompt, and a pinned `--model` (an unpinned run falls back to the
caller's *saved* default, which the ratchet cannot detect changing between
two runs — P1.4c). The subprocess also runs with `cwd` set to a scratch
directory, not the repo root, so CLAUDE.md discovery finds nothing to pull
in."""
import json
import subprocess
import sys

from . import TEXT_IO, Backend, BackendUnavailable, executable

# Patterns observed in a real exhausted-quota response body — checked in addition to
# `is_error`/`subtype` in case a future CLI version reports quota exhaustion
# as a "successful" call with an apologetic message in `result`.
_QUOTA_PATTERNS = ("session limit", "usage limit", "rate limit", "quota exceeded")
# A quota message is a short one-liner; a long reply that merely mentions "rate limit" (API docs, a
# security checklist) is real model output and must not abort the sweep (found 2026-09-26).
_QUOTA_MESSAGE_MAX_CHARS = 300


def _reason(stdout: str) -> str:
    """The CLI puts a failure's reason (refusal, API error) in the JSON envelope on stdout, not in stderr."""
    try:
        envelope = json.loads(stdout)
    except (json.JSONDecodeError, TypeError):
        return (stdout or "").strip()[:300]
    if not isinstance(envelope, dict):
        return ""
    return f"stop_reason={envelope.get('stop_reason')!r}: {(envelope.get('result') or '').strip()[:300]}"


def _invoke(cmd: list[str], prompt: str, timeout: int) -> dict:
    try:
        result = subprocess.run(cmd, input=prompt, capture_output=True, timeout=timeout, **TEXT_IO)
    except Exception as exc:
        raise BackendUnavailable(f"claude -p failed to start: {exc}") from exc

    if result.returncode != 0:
        raise BackendUnavailable(
            f"claude -p exited {result.returncode}: {(result.stderr or '').strip()[:300]} {_reason(result.stdout)}"
        )

    stdout = (result.stdout or "").strip()
    if not stdout:
        raise BackendUnavailable("claude -p returned empty stdout")

    try:
        envelope = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise BackendUnavailable(f"claude -p did not return valid JSON: {exc}") from exc

    if envelope.get("is_error"):
        raise BackendUnavailable(
            f"claude -p reported is_error (subtype={envelope.get('subtype')!r}, "
            f"api_error_status={envelope.get('api_error_status')!r}) {_reason(stdout)}"
        )

    text = (envelope.get("result") or "").strip()
    if not text:
        raise BackendUnavailable("claude -p returned an empty result")
    lowered = text.lower()
    if len(text) <= _QUOTA_MESSAGE_MAX_CHARS and any(p in lowered for p in _QUOTA_PATTERNS):
        raise BackendUnavailable(f"claude -p returned a quota/limit message: {text[:200]!r}")

    return {"text": text, "usage": envelope.get("usage") or {}}


def _base_cmd(model: str | None, profile: dict | None) -> list[str]:
    cmd = [executable("claude"), "-p", "--output-format", "json"]
    if profile:
        cmd += list(profile.get("flags") or [])
        system_prompt = profile.get("system_prompt")
        if system_prompt:
            cmd += ["--system-prompt", system_prompt]
    effective_model = model or (profile.get("model") if profile else None)
    if effective_model:
        cmd += ["--model", effective_model]
    return cmd


class ClaudeCli(Backend):
    name = "claude"

    def run(self, prompt: str, mcp_config: dict | None, model: str | None,
            timeout: int = 180, profile: dict | None = None) -> dict:
        cmd = _base_cmd(model, profile)
        if mcp_config is not None:
            cmd += ["--mcp-config", json.dumps(mcp_config)]
        return _invoke(cmd, prompt, timeout)

    def judge(self, prompt: str, model: str | None, timeout: int = 60,
              profile: dict | None = None) -> dict:
        cmd = _base_cmd(model, profile)
        return _invoke(cmd, prompt, timeout)
