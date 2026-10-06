import pytest

from skillswiki import paraphrase
from skillswiki.evals.backends import BackendUnavailable

DESC = "Rewrites draft emails so they sound natural and clear."


class FakeCli:
    def __init__(self, reply=None, error=None):
        self.reply, self.error, self.prompts = reply, error, []

    def judge(self, prompt, model, timeout=60, profile=None):
        self.prompts.append(prompt)
        if self.error:
            raise self.error
        return {"text": self.reply}


def test_overlap_counts_shared_words():
    assert paraphrase.overlap("rewrite my draft email", DESC) > 0.5
    assert paraphrase.overlap("my message to the landlord sounds stiff, fix it?", DESC) < 0.5


def test_ai_paraphrase_is_used_when_it_avoids_the_description_words(monkeypatch):
    cli = FakeCli('"My note to the landlord sounds stiff — can you make it friendlier?"\n')
    monkeypatch.setattr(paraphrase, "get_backend", lambda name: cli)
    assert paraphrase.from_ai(DESC, "claude") == "My note to the landlord sounds stiff — can you make it friendlier?"
    assert DESC in cli.prompts[0] and "same language" in cli.prompts[0]


@pytest.mark.parametrize("reply", ["Rewrites draft emails so they sound natural and clear.", "", "x" * 400])
def test_copy_empty_or_long_replies_are_rejected(monkeypatch, reply):
    monkeypatch.setattr(paraphrase, "get_backend", lambda name: FakeCli(reply))
    assert paraphrase.from_ai(DESC, "claude") is None


def test_backend_failure_gives_none(monkeypatch):
    monkeypatch.setattr(paraphrase, "get_backend", lambda name: FakeCli(error=BackendUnavailable("quota")))
    assert paraphrase.from_ai(DESC, "claude") is None


def test_available_backend_follows_path(monkeypatch):
    monkeypatch.setattr(paraphrase.shutil, "which", lambda exe: "/bin/agy" if exe == "agy" else None)
    assert paraphrase.available_backend() == "gemini"
    monkeypatch.setattr(paraphrase.shutil, "which", lambda exe: None)
    assert paraphrase.available_backend() is None
