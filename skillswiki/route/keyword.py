"""Keyword routing without a TypeSafe key: BM25 over each adopted skill's own description plus its routing
card (examples + keywords). It never auto-picks: it returns a shortlist and the agent decides.

Heuristic: a skill whose card `not_for` text shares >= NOT_FOR_MIN_OVERLAP tokens with the request has its
score halved, so near-miss requests the user explicitly excluded rank lower.
"""
import json
import math
import re
import unicodedata
from collections import Counter

from skillswiki import store, usage

K1 = 1.5
B = 0.75
DEFAULT_K = 5
NOT_FOR_MIN_OVERLAP = 2
NOT_FOR_PENALTY = 0.5
EXCERPT_MAX_CHARS = 200
MIN_TOKEN_LEN = 2
# kana (without the middle dot U+30FB, a separator), CJK, Hangul
_CJK_CHARS = "\u3040-\u30fa\u30fc-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af\uf900-\ufaff"
_SPLIT = re.compile(rf"[^a-z0-9{_CJK_CHARS}]+")
_CJK_RUN = re.compile(rf"([{_CJK_CHARS}]+)")
# Character pairs built on these everyday characters (or listed whole) match almost any Chinese text, so they are not
# indexed: "帮我", "一个", "我想" made unrelated requests match long Chinese descriptions (Phase 12 review).
CJK_STOP_CHARS = frozenset("一我你他她它的了是在个这那么吗呢吧啊和也就都把被给帮想让")
CJK_STOP_PAIRS = frozenset({"用户"})
STOPWORDS = frozenset("""
a an and are as at be but by can could do for from has have how i if in into is it its make me my of on or our
please so that the their them then there these this to us was we what when which will with would you your
""".split())

NOTE_SHORTLIST = ("Keyword shortlist from the user's adopted skills. Load the top match with load_skill and follow it "
                  "if it fits; if none fits, answer normally.")
NOTE_NO_MATCH = "No adopted skill matches this request. Proceed without a skill."


def _stem(token: str) -> str:
    """Light plural folding: emails -> email, but class / address stay."""
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def has_cjk(text: str) -> bool:
    return bool(_CJK_RUN.search(text or ""))


def _cjk_pairs(run: str) -> list[str]:
    """Chinese and Japanese have no spaces between words: index overlapping character pairs instead."""
    pairs = [run] if len(run) == 1 else [run[i:i + 2] for i in range(len(run) - 1)]
    return [p for p in pairs if p not in CJK_STOP_PAIRS and not CJK_STOP_CHARS.intersection(p)]


def tokenize(text: str) -> list[str]:
    tokens = []
    for chunk in _SPLIT.split(unicodedata.normalize("NFKC", text or "").lower()):  # NFKC: ＡＩ -> AI
        for part in _CJK_RUN.split(chunk):
            if _CJK_RUN.fullmatch(part):
                tokens += _cjk_pairs(part)
            elif part:
                word = _stem(part)
                if len(word) >= MIN_TOKEN_LEN and word not in STOPWORDS:
                    tokens.append(word)
    return tokens


def _documents() -> dict[str, tuple[list[str], set[str]]]:
    """{slug: (indexed tokens, not_for tokens)} for adopted skills."""
    with store.connect() as conn:
        rows = conn.execute(
            "SELECT s.slug, s.name, s.description, c.examples, c.keywords, c.not_for FROM skills s "
            "LEFT JOIN cards c ON c.slug = s.slug WHERE s.status = 'adopted'").fetchall()
    docs = {}
    for r in rows:
        card_text = " ".join(json.loads(r["examples"] or "[]") + json.loads(r["keywords"] or "[]"))
        tokens = tokenize(f"{r['name']} {r['description']} {card_text}")
        not_for = set(tokenize(" ".join(json.loads(r["not_for"] or "[]"))))
        docs[r["slug"]] = (tokens, not_for)
    return docs


def shortlist(request: str, k: int = DEFAULT_K) -> list[tuple[str, float]]:
    """[(slug, score)] with score > 0, best first."""
    query = set(tokenize(request))
    docs = _documents()
    if not query or not docs:
        return []
    n_docs = len(docs)
    avg_len = sum(len(t) for t, _ in docs.values()) / n_docs or 1.0
    doc_freq = Counter(term for tokens, _ in docs.values() for term in set(tokens))
    scored = []
    for slug, (tokens, not_for) in docs.items():
        tf = Counter(tokens)
        score = 0.0
        for term in query:
            if tf[term]:
                idf = math.log(1 + (n_docs - doc_freq[term] + 0.5) / (doc_freq[term] + 0.5))
                score += idf * tf[term] * (K1 + 1) / (tf[term] + K1 * (1 - B + B * len(tokens) / avg_len))
        if score > 0 and len(query & not_for) >= NOT_FOR_MIN_OVERLAP:
            score *= NOT_FOR_PENALTY
        if score > 0:
            scored.append((slug, score))
    return sorted(scored, key=lambda s: (-s[1], s[0]))[:k]


def log_routing(request: str, mode: str, suggested: str | None, shortlist_: list[dict], confidence: float | None,
                reason: str) -> None:
    with store.connect() as conn:
        conn.execute("INSERT INTO routing_log (request_excerpt, mode, suggested, shortlist, confidence, reason, "
                     "created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                     ((request or "")[:EXCERPT_MAX_CHARS], mode, suggested, json.dumps(shortlist_), confidence,
                      reason, store.now()))
    for item in shortlist_:
        usage.log(item["skill"], "suggest")


def suggest_keyword(request: str) -> dict:
    ranked = [{"skill": slug, "score": round(score, 3)} for slug, score in shortlist(request)]
    reason = "shortlist" if ranked else "no_match"
    log_routing(request, "keyword", None, ranked, None, reason)
    return {"mode": "keyword", "skill": None, "shortlist": ranked, "reason": reason,
            "note": NOTE_SHORTLIST if ranked else NOTE_NO_MATCH}
