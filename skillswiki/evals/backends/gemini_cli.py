"""Gemini backend. Shells out to Antigravity's `agy` when it is installed, else the Gemini CLI `gemini`;
override with SKILLSWIKI_GEMINI_BIN. The backend keeps the "gemini" name/key either way — that's what
skillswiki/evals/runner.py's judge-priority list references.

Unlike claude_cli.py and codex_cli.py, the prompt is passed as `agy -p
<prompt>` (a direct argument value), not piped via stdin: `agy`'s flag
parser (Go's flag package) does not re-parse a flag's own value, so a
prompt starting with "---" (a skill body's YAML frontmatter) does not get
misread as another flag — verified directly against this binary, unlike the
stdin-piping fix needed for claude_cli.py's positional-arg parser.
`--disable-slash-commands` (agy only) stops prompt content that looks like a
slash command from being expanded.

Raises BackendUnavailable on a failed call (non-zero exit, empty stdout, or
a detected quota/rate-limit message) instead of returning the failure text
as output — see evals/backends/__init__.py (P1.4c, 2026-09-09). `agy` has no
confirmed structured JSON output mode, so `usage` is always None here — this
backend is not included in a run's token ledger. `profile` is accepted for
interface parity but not applied: its flags are Claude-CLI-specific."""
import os
import shutil
import subprocess
import sys

from . import TEXT_IO, Backend, BackendUnavailable, executable

BIN_ENV = "SKILLSWIKI_GEMINI_BIN"
_ANTIGRAVITY_BIN = "agy"
_GEMINI_CLI_BIN = "gemini"
_QUOTA_PATTERNS = ("session limit", "usage limit", "rate limit", "quota exceeded")


def _invoke(cmd: list[str], timeout: int) -> dict:
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=timeout, **TEXT_IO)
    except Exception as exc:
        raise BackendUnavailable(f"{binary()} -p failed to start: {exc}") from exc

    if result.returncode != 0:
        raise BackendUnavailable(f"{binary()} -p exited {result.returncode}: {(result.stderr or '').strip()[:300]}")

    text = (result.stdout or "").strip()
    if not text:
        raise BackendUnavailable(f"{binary()} -p returned empty stdout")
    if any(p in text.lower() for p in _QUOTA_PATTERNS):
        raise BackendUnavailable(f"{binary()} -p returned a quota/limit message: {text[:200]!r}")

    return {"text": text, "usage": None}


def binary() -> str:
    """SKILLSWIKI_GEMINI_BIN, else `agy` (Antigravity) if installed, else `gemini` (Gemini CLI)."""
    override = os.getenv(BIN_ENV, "").strip()
    if override:
        return override
    return _ANTIGRAVITY_BIN if shutil.which(_ANTIGRAVITY_BIN) else _GEMINI_CLI_BIN


def _program_name(path: str) -> str:
    """'agy' for agy, agy.exe, /usr/local/bin/agy or C:\\tools\\agy.exe (either separator, any OS)."""
    name = path.replace("\\", "/").rsplit("/", 1)[-1].lower()
    return name.rsplit(".", 1)[0] if name.endswith((".exe", ".cmd", ".bat")) else name


def _cmd(prompt: str, model: str | None) -> list[str]:
    bin_ = binary()
    cmd = [executable(bin_), "-p", prompt]
    if _program_name(bin_) == _ANTIGRAVITY_BIN:
        cmd.append("--disable-slash-commands")
    if model:
        cmd += ["--model", model]
    return cmd


class GeminiCli(Backend):
    name = "gemini"

    def run(self, prompt: str, mcp_config: dict | None, model: str | None,
            timeout: int = 180, profile: dict | None = None) -> dict:
        return _invoke(_cmd(prompt, model), timeout)

    def judge(self, prompt: str, model: str | None, timeout: int = 60,
              profile: dict | None = None) -> dict:
        return _invoke(_cmd(prompt, model), timeout)
