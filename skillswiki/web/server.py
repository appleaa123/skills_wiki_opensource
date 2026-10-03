"""Local management page: stdlib HTTP server bound to 127.0.0.1 only.

GET  /                                  the page (static/index.html with this server's write token)
GET  /api/skills | /api/routing | /api/unused | /api/overlaps
GET  /api/skills/<slug>/learnings | /evals | /card
POST /api/skills/<slug>/adopt | /release | /enrich | /learnings
PUT  /api/skills/<slug>/card            PUT /api/learnings/<id>        DELETE /api/learnings/<id>

Responses: {"ok": true, "data": ...} or {"ok": false, "error": "..."}. Guards: the Host header must be this
server (DNS-rebinding), and every non-GET request needs the X-Skillswiki-Token generated at start (CSRF).
"""
import json
import re
import secrets
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse

from skillswiki import cards, learnings, library, paths
from skillswiki.web import queries

HOST = "127.0.0.1"
DEFAULT_PORT = 7878
TOKEN_HEADER = "X-Skillswiki-Token"
TOKEN_PLACEHOLDER = "__SKILLSWIKI_TOKEN__"
MAX_BODY_BYTES = 64 * 1024
_SKILL = re.compile(r"^/api/skills/(?P<slug>[^/]+)/(?P<action>[a-z]+)$")
_LEARNING = re.compile(r"^/api/learnings/(?P<id>\d+)$")

GET_ROUTES = {
    "/api/skills": queries.skills_overview,
    "/api/routing": lambda: {"log": queries.routing_log(), "no_match": queries.no_match()},
    "/api/unused": queries.unused,
    "/api/overlaps": queries.overlaps,
}
GET_SKILL_ROUTES = {"learnings": queries.skill_learnings, "evals": queries.skill_evals, "card": queries.skill_card}


class NotFound(Exception):
    pass


def _post_skill(slug: str, action: str, body: dict):
    if action == "adopt":
        return library.adopt(slug)
    if action == "release":
        return library.release(slug)
    if action == "enrich":
        return cards.enrich(slug, body.get("backend") or "claude")
    if action == "learnings":
        learnings.require_adopted(slug)
        return {"id": learnings.record(slug, body.get("body", ""), supersedes=body.get("supersedes") or [])}
    raise NotFound(action)


def _put_card(slug: str, body: dict):
    return cards.set_manual(slug, body.get("examples") or [], body.get("keywords") or [], body.get("not_for") or [])


class Handler(BaseHTTPRequestHandler):
    server_version = "SkillsWiki"
    token = ""
    port = DEFAULT_PORT

    def log_message(self, fmt, *args):  # quiet: the terminal shows only the URL
        return

    def _send(self, status: int, payload: dict | None = None, html: str | None = None) -> None:
        data = html.encode() if html is not None else json.dumps(payload, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8" if html is not None else "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _ok(self, data) -> None:
        self._send(HTTPStatus.OK, {"ok": True, "data": data})

    def _error(self, status: int, message: str) -> None:
        self._send(status, {"ok": False, "error": message})

    def _host_ok(self) -> bool:
        return self.headers.get("Host", "") in {f"{HOST}:{self.port}", f"localhost:{self.port}"}

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length < 0 or length > MAX_BODY_BYTES:
            raise ValueError("request body too large")
        raw = self.rfile.read(length) if length else b"{}"
        data = json.loads(raw or b"{}")
        if not isinstance(data, dict):
            raise ValueError("request body must be a JSON object")
        return data

    def _dispatch(self, method: str) -> None:
        if not self._host_ok():
            return self._error(HTTPStatus.FORBIDDEN, "bad host")
        path = urlparse(self.path).path
        if method != "GET" and not secrets.compare_digest(self.headers.get(TOKEN_HEADER, ""), self.token):
            return self._error(HTTPStatus.FORBIDDEN, "missing or wrong token")
        try:
            if method == "GET":
                return self._get(path)
            body = self._body()
            skill, learning = _SKILL.match(path), _LEARNING.match(path)
            if method == "POST" and skill:
                return self._ok(_post_skill(unquote(skill["slug"]), skill["action"], body))
            if method == "PUT" and skill and skill["action"] == "card":
                return self._ok(_put_card(unquote(skill["slug"]), body))
            if method == "PUT" and learning:
                return self._ok({"id": learnings.edit(int(learning["id"]), body.get("body", ""))})
            if method == "DELETE" and learning:
                learnings.retire(int(learning["id"]))
                return self._ok({"retired": int(learning["id"])})
            raise NotFound(path)
        except NotFound:
            return self._error(HTTPStatus.NOT_FOUND, "not found")
        except ValueError as exc:
            return self._error(HTTPStatus.BAD_REQUEST, str(exc))
        except Exception as exc:  # noqa: BLE001 — a local tool: show the user what failed
            return self._error(HTTPStatus.INTERNAL_SERVER_ERROR, f"{type(exc).__name__}: {exc}")

    def _get(self, path: str) -> None:
        if path in ("/", "/index.html"):
            page = (paths.package_dir() / "web" / "static" / "index.html").read_text()
            return self._send(HTTPStatus.OK, html=page.replace(TOKEN_PLACEHOLDER, self.token))
        if path in GET_ROUTES:
            return self._ok(GET_ROUTES[path]())
        skill = _SKILL.match(path)
        if skill and skill["action"] in GET_SKILL_ROUTES:
            return self._ok(GET_SKILL_ROUTES[skill["action"]](unquote(skill["slug"])))
        raise NotFound(path)

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_PUT(self):
        self._dispatch("PUT")

    def do_DELETE(self):
        self._dispatch("DELETE")


def make_server(port: int = DEFAULT_PORT) -> ThreadingHTTPServer:
    """A server on 127.0.0.1:<port> (0 = any free port) with a fresh write token."""
    handler = type("BoundHandler", (Handler,), {"token": secrets.token_urlsafe(24)})
    server = ThreadingHTTPServer((HOST, port), handler)
    handler.port = server.server_address[1]
    return server
