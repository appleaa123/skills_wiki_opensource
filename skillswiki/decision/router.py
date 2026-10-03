"""Skill router (JEV Phase 3). Pure and vendor-neutral.

route(request, catalog, backend, config) walks a routing catalog (skillswiki/route/jev_route.py builds a flat one):
  1. theme descent: a Choice over the tree's lines (plus 3 gating Nouls in the first request),
     keeping a beam of up to 2 paths while the runner-up is close enough, until leaves;
  2. skill choice: one Choice over the routing lines of every skill in the beam's leaves
     (filtered to the caller's allowed packs), top-k kept;
  3. verify: one request asking, per top-k candidate, whether it would accomplish the request; with
     config "use_scopes", a tool/place-only candidate is shown as "For <scope> only: ..." and its fit is
     capped by a Noul "the request involves <scope>, named or clearly implied" (scopes are catalog data).
No pack, skill or theme name appears here: all routing knowledge is data.
Suggestion-shaped: the caller may ignore the suggestion. DecisionUnavailable -> no
suggestion with reason "unavailable"; the caller falls back (Phase 4).
"""
from dataclasses import dataclass, field

from skillswiki.decision.base import ChoiceQ, DecisionUnavailable, NoulQ

DEFAULTS = {
    "beam_width": 2,
    "beam_ratio": 0.5,       # keep the runner-up path while its score >= ratio x the best
    "top_k": 3,
    "max_options": 255,      # JEV Choice limit
    "gate_min": 0.30,        # mean of the gating Nouls below this -> no skill
    "fit_min": 0.30,         # best candidate fit below this -> no skill
    "detail_chars": 1500,
    "max_depth": 6,
    "use_scopes": False,     # tool/place-only skills (catalog "scopes") need the request to involve their tool
}
THEME_INSTRUCTIONS = "Which area best matches what the user is asking for?"
SKILL_INSTRUCTIONS = "Which skill is the right one to load for what the user is asking for?"
GATES = {
    "gate_procedure": "Doing this request well means following a specialised, documented procedure rather than "
                      "giving a quick general answer.",
    "gate_expertise": "Doing this request well needs domain know-how beyond general knowledge.",
    "gate_artifact": "The request asks to produce, change or analyse something concrete: a document, code, data, a "
                     "plan, a campaign or a system.",
}
SCOPE_INSTRUCTIONS = "The request in state.request involves {scope}, named or clearly implied."
FIT_INSTRUCTIONS = ("The skill described in state.candidates[{cid}] would accomplish what the user is asking for in "
                    "state.request.")


class RoutingError(ValueError):
    """The catalog breaks a routing invariant (e.g. more options than one Choice allows)."""


@dataclass(frozen=True)
class Suggestion:
    skill: str | None
    reason: str                      # suggested | gated | low_fit | no_candidates | unavailable
    confidence: float = 0.0          # the suggested skill's fit (0 when none)
    shortlist: list = field(default_factory=list)   # [(skill, fit)] in skill-choice order
    path: list = field(default_factory=list)        # [[node ids root->leaf], ...] for the beam
    gates: dict = field(default_factory=dict)
    separation: float | None = None  # top / runner-up skill-choice probability
    input_tokens: int = 0
    equivalents: list = field(default_factory=list)  # other skills with the suggested skill's identical line


def _line(catalog: dict, sid: str) -> str:
    return catalog["overrides"].get(sid) or catalog["skills"][sid]["line"]


def _scope(catalog: dict, sid: str, config: dict) -> str | None:
    return catalog.get("scopes", {}).get(sid) if config.get("use_scopes") else None


def _shown(catalog: dict, sid: str, config: dict) -> str:
    """The routing line as JEV sees it: a scoped skill says which tool or place it is for."""
    scope = _scope(catalog, sid, config)
    return f"For {scope} only: {_line(catalog, sid)}" if scope else _line(catalog, sid)


def _choice(options: dict[str, str]) -> tuple[ChoiceQ, dict[str, str]]:
    """ChoiceQ with neutral option keys (o0, o1, ...) and the key -> item map."""
    keys = {f"o{i}": item for i, item in enumerate(options)}
    return ChoiceQ(instructions="", criteria={k: options[v] for k, v in keys.items()}), keys


def _theme_question(nodes: list[dict]) -> tuple[ChoiceQ, dict[str, dict]]:
    q, keys = _choice({i: n["line"] for i, n in enumerate(nodes)})
    return ChoiceQ(THEME_INSTRUCTIONS, q.criteria), {k: nodes[i] for k, i in keys.items()}


def _prune(paths: list[tuple[list[dict], float]], config: dict) -> list[tuple[list[dict], float]]:
    paths = sorted(paths, key=lambda p: -p[1])[: config["beam_width"]]
    return [p for p in paths if p[1] >= config["beam_ratio"] * paths[0][1]]


def _descend(request, catalog, backend, config) -> tuple[list[list[dict]], dict, int]:
    """Beam of root->leaf node paths, the gate answers, and tokens used."""
    tree = catalog["tree"]
    if len(tree) == 1 and not tree[0].get("children"):
        # Flat library (one leaf holding every skill): no theme Choice to ask, but the gates still decide
        # whether the request needs a skill at all.
        result = backend.decide({"request": request}, {k: NoulQ(v) for k, v in GATES.items()})
        gates = {k: result.answers[k].noul for k in GATES}
        if sum(gates.values()) / len(gates) < config["gate_min"]:
            return [], gates, result.input_tokens
        return [[tree[0]]], gates, result.input_tokens
    q, key_map = _theme_question(tree)
    result = backend.decide({"request": request}, {"themes": q, **{k: NoulQ(v) for k, v in GATES.items()}})
    tokens = result.input_tokens
    gates = {k: result.answers[k].noul for k in GATES}
    if sum(gates.values()) / len(gates) < config["gate_min"]:
        return [], gates, tokens
    probs = result.answers["themes"].probabilities
    beam = _prune([([key_map[k]], p) for k, p in probs.items()], config)
    for _ in range(config["max_depth"]):
        open_paths = [(i, path) for i, (path, _p) in enumerate(beam) if path[-1].get("children")]
        if not open_paths:
            break
        questions, maps = {}, {}
        for i, path in open_paths:
            questions[f"child_{i}"], maps[f"child_{i}"] = _theme_question(path[-1]["children"])
        result = backend.decide({"request": request}, questions)
        tokens += result.input_tokens
        grown = [(path, p) for i, (path, p) in enumerate(beam) if not path[-1].get("children")]
        for i, _path in open_paths:
            path, p = beam[i]
            for k, pc in result.answers[f"child_{i}"].probabilities.items():
                grown.append((path + [maps[f"child_{i}"][k]], p * pc))
        beam = _prune(grown, config)
    return [path for path, _p in beam], gates, tokens


def route(request: str, catalog: dict, backend, config: dict = DEFAULTS, allowed: set[str] | None = None,
          describe=None) -> Suggestion:
    """`allowed`: pack names the caller may use (None = all). `describe(skill)`: fuller text for verify."""
    try:
        paths, gates, tokens = _descend(request, catalog, backend, config)
        if not paths:
            return Suggestion(None, "gated", gates=gates, input_tokens=tokens)
        candidates = []
        for path in paths:
            for sid in path[-1].get("skills") or []:
                if sid not in candidates and (allowed is None or sid.split("/", 1)[0] in allowed):
                    candidates.append(sid)
        ids = [[n["id"] for n in path] for path in paths]
        if not candidates:
            return Suggestion(None, "no_candidates", path=ids, gates=gates, input_tokens=tokens)
        # Skills with an identical routing line are one option: JEV cannot tell them apart, so it is not
        # asked to. The first is the representative; the rest come back as equivalents.
        groups: dict[str, list[str]] = {}
        for sid in candidates:
            groups.setdefault(_shown(catalog, sid, config), []).append(sid)
        if len(groups) > config["max_options"]:
            raise RoutingError(f"{len(groups)} distinct candidate lines > {config['max_options']}: split a leaf")
        members = {sids[0]: sids for sids in groups.values()}

        q, key_map = _choice({sids[0]: line for line, sids in groups.items()})
        result = backend.decide({"request": request}, {"skill": ChoiceQ(SKILL_INSTRUCTIONS, q.criteria)})
        tokens += result.input_tokens
        ranked = sorted(result.answers["skill"].probabilities.items(), key=lambda kv: -kv[1])
        top = [(key_map[k], p) for k, p in ranked[: config["top_k"]]]
        separation = top[0][1] / top[1][1] if len(top) > 1 and top[1][1] > 0 else None

        texts = {f"c{i}": _shown(catalog, sid, config) + ("\n" + describe(sid)[: config["detail_chars"]] if describe else "")
                 for i, (sid, _p) in enumerate(top)}
        questions = {f"fit_c{i}": NoulQ(FIT_INSTRUCTIONS.format(cid=f"c{i}")) for i in range(len(top))}
        scoped = {i: _scope(catalog, sid, config) for i, (sid, _p) in enumerate(top) if _scope(catalog, sid, config)}
        questions.update({f"scope_c{i}": NoulQ(SCOPE_INSTRUCTIONS.format(scope=sc)) for i, sc in scoped.items()})
        result = backend.decide({"request": request, "candidates": texts}, questions)
        tokens += result.input_tokens
        # A scoped skill fits no better than the chance that the request involves its tool or place.
        shortlist = [(sid, min(result.answers[f"fit_c{i}"].noul,
                               result.answers[f"scope_c{i}"].noul if i in scoped else 1.0))
                     for i, (sid, _p) in enumerate(top)]
        best_sid, best_fit = max(shortlist, key=lambda s: s[1])
        if best_fit < config["fit_min"]:
            return Suggestion(None, "low_fit", shortlist=shortlist, path=ids, gates=gates,
                              separation=separation, input_tokens=tokens)
        return Suggestion(best_sid, "suggested", confidence=best_fit, shortlist=shortlist, path=ids, gates=gates,
                          separation=separation, input_tokens=tokens, equivalents=members[best_sid][1:])
    except DecisionUnavailable:
        return Suggestion(None, "unavailable")
