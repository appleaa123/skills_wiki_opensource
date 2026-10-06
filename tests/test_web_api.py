import json
import threading
import urllib.error
import urllib.request

import pytest
from helpers import install_fixture_skills

from skillswiki import cards, discovery, learnings, library, store
from skillswiki.route import keyword
from skillswiki.web import queries, server


@pytest.fixture
def adopted(tmp_home):
    install_fixture_skills(tmp_home / "native")
    discovery.sync_db()
    library.adopt("email-polisher")
    library.adopt("meeting-notes")


@pytest.fixture
def web(adopted):
    srv = server.make_server(0)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield srv
    srv.shutdown()
    srv.server_close()


def call(srv, method, path, body=None, token=True, host=None):
    port = srv.server_address[1]
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method,
                                 data=json.dumps(body).encode() if body is not None else None)
    if token:
        req.add_header(server.TOKEN_HEADER, srv.RequestHandlerClass.token)
    if host:
        req.add_header("Host", host)
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


# ── queries ───────────────────────────────────────────────────────────


def test_skills_overview_fields(adopted):
    cards.set_manual("email-polisher", examples=["fix my email"], keywords=[], not_for=[])
    learnings.record("email-polisher", "Sign off with Best.")
    from skillswiki import loader
    loader.load("email-polisher")
    rows = {r["slug"]: r for r in queries.skills_overview()}
    ep = rows["email-polisher"]
    assert ep["status"] == "adopted" and ep["has_card"] and ep["learnings_live"] == 1 and ep["learnings_cap"] == 8
    assert ep["loads_30d"] == 1 and ep["context_cost_30d"] == ep["context_tokens_est"] > 0
    assert ep["last_used"] and ep["changed"] is False
    assert rows["csv-cleaner"]["status"] == "native" and rows["csv-cleaner"]["has_scripts"] is True


def test_changed_flag_in_overview(adopted, tmp_home):
    from skillswiki import paths
    (paths.library_dir() / "meeting-notes" / "SKILL.md").write_text("edited")
    rows = {r["slug"]: r for r in queries.skills_overview()}
    assert rows["meeting-notes"]["changed"] is True


def test_no_match_and_overlaps(adopted):
    for _ in range(3):
        keyword.suggest_keyword("notes from my email meeting")
    keyword.suggest_keyword("zzzz qqqq")
    assert queries.no_match()[0]["request_excerpt"] == "zzzz qqqq"
    assert queries.overlaps() == [{"a": "email-polisher", "b": "meeting-notes", "count": 3}]


def test_unused_needs_age_and_no_use(adopted):
    with store.connect() as conn:
        conn.execute("UPDATE skills SET adopted_at = '2020-01-01T00:00:00+00:00'")
    keyword.suggest_keyword("fix my email draft")
    assert [r["slug"] for r in queries.unused()] == ["meeting-notes"]


# ── HTTP ──────────────────────────────────────────────────────────────


def test_page_has_token(web):
    port = web.server_address[1]
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/") as resp:
        html = resp.read().decode()
    assert web.RequestHandlerClass.token in html and server.TOKEN_PLACEHOLDER not in html


@pytest.mark.parametrize("path", ["/api/skills", "/api/routing", "/api/unused", "/api/overlaps",
                                  "/api/skills/email-polisher/learnings", "/api/skills/email-polisher/evals",
                                  "/api/skills/email-polisher/card"])
def test_read_endpoints(web, path):
    status, body = call(web, "GET", path, token=False)
    assert status == 200 and body["ok"] is True


def test_unknown_path_404(web):
    assert call(web, "GET", "/api/nope")[0] == 404


def test_bad_host_rejected(web):
    status, body = call(web, "GET", "/api/skills", host="evil.example:80")
    assert status == 403 and body["error"] == "bad host"


@pytest.mark.parametrize("method,path,body", [
    ("POST", "/api/skills/email-polisher/release", {}),
    ("PUT", "/api/skills/email-polisher/card", {"examples": ["x"]}),
    ("POST", "/api/skills/email-polisher/learnings", {"body": "x"}),
    ("DELETE", "/api/learnings/1", None),
])
def test_writes_need_token(web, method, path, body):
    status, resp = call(web, method, path, body, token=False)
    assert status == 403 and "token" in resp["error"]


def test_write_actions(web, tmp_home):
    status, body = call(web, "POST", "/api/skills/email-polisher/release", {})
    assert status == 200 and (tmp_home / "native" / "email-polisher").is_dir()
    assert call(web, "POST", "/api/skills/email-polisher/adopt", {})[0] == 200
    status, body = call(web, "PUT", "/api/skills/email-polisher/card", {"examples": ["fix my email"], "keywords": ["tone"]})
    assert status == 200 and body["data"]["keywords"] == ["tone"]
    status, body = call(web, "POST", "/api/skills/email-polisher/learnings", {"body": "Sign off with Best."})
    learning_id = body["data"]["id"]
    status, body = call(web, "PUT", f"/api/learnings/{learning_id}", {"body": "Sign off with Cheers."})
    assert [r["body"] for r in learnings.list_live("email-polisher")] == ["Sign off with Cheers."]
    assert call(web, "DELETE", f"/api/learnings/{body['data']['id']}")[0] == 200
    assert learnings.list_live("email-polisher") == []


def test_validation_errors_are_400(web):
    status, body = call(web, "PUT", "/api/skills/email-polisher/card", {"examples": "not a list"})
    assert status == 400 and "list of strings" in body["error"]
    status, body = call(web, "POST", "/api/skills/nope/adopt", {})
    assert status == 400 and "not found" in body["error"]


def test_body_too_large(web):
    status, body = call(web, "POST", "/api/skills/email-polisher/learnings", {"body": "x" * 70000})
    assert status == 400 and "too large" in body["error"]


def test_errors_carry_codes(web):
    status, body = call(web, "POST", "/api/skills/nope/adopt", {})
    assert status == 400 and body["ok"] is False and body["code"] == "NOT_FOUND" and "not found" in body["error"]
    status, body = call(web, "POST", "/api/skills/email-polisher/adopt", {})
    assert status == 400 and body["code"] == "ALREADY_ADOPTED"
    status, body = call(web, "GET", "/api/nope")
    assert status == 404 and body == {"ok": False, "error": "not found"}
