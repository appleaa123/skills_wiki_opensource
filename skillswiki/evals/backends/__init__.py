"""Pluggable CLI backends for skillswiki/evals/runner.py — BYO-model (§3.5 model-cost
rule: Skills Wiki pays for no inference; every eval run spends the caller's
own subscription).

Contract (P1.4c, 2026-09-09): `run`/`judge` return `{"text": str, "usage":
dict | None}` on success and RAISE `BackendUnavailable` on failure — a
failed call (non-zero exit, an error envelope, empty output, or a detected
quota/rate-limit message) must never be silently scored as if it were model
output. Before this change, a failed `claude -p` call quietly returned "" or
the CLI's own error string, which the judge then scored like any other
response (in one real run, 30/30
`no_skill` records and 2/30 `skill` records were the literal string "You've
hit your session limit...", scored and published as a real result). `usage`
is populated only for backends whose CLI exposes a structured token count
(`claude -p --output-format json`); other backends return `usage: None` and
are not included in a run's token ledger.

`profile` (an optional dict, see skillswiki/evals/config.json's "invocation_profile")
lets a backend run leaner than its interactive default — no CLAUDE.md
discovery, no built-in tools, a short system prompt, a pinned model. Only
ClaudeCli applies it: the flags are Claude-CLI-specific and do not carry
over to codex/agy."""


import shutil


def executable(name: str) -> str:
    """Full path of a CLI on PATH (on Windows this finds claude.cmd / agy.exe, which a bare name does not
    start), or the name itself so the error message stays readable when it is missing."""
    return shutil.which(name) or name


# Text settings for every CLI subprocess: decode as UTF-8 on every OS (Windows defaults to a legacy code page).
TEXT_IO = {"text": True, "encoding": "utf-8", "errors": "replace"}


class BackendUnavailable(RuntimeError):
    """A backend call failed outright — non-zero exit, an error envelope, an
    empty response, or a detected quota/rate-limit message. Callers must let
    this propagate (abort the sweep) rather than score it as output."""


class Backend:
    name = "base"

    def run(self, prompt: str, mcp_config: dict | None, model: str | None,
            timeout: int = 180, profile: dict | None = None) -> dict:
        raise NotImplementedError

    def judge(self, prompt: str, model: str | None, timeout: int = 60,
              profile: dict | None = None) -> dict:
        raise NotImplementedError


def get_backend(name: str) -> Backend:
    if name == "jev":
        # Imported only on request: JEV is optional (needs the user's own TYPESAFE_API_KEY).
        from .jev import JevJudge
        return JevJudge()
    from . import claude_cli, codex_cli, gemini_cli
    return {
        "claude": claude_cli.ClaudeCli(),
        "codex": codex_cli.CodexCli(),
        "gemini": gemini_cli.GeminiCli(),
    }[name]
