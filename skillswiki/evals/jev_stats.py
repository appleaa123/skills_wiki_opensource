"""Gate statistics for the JEV Phase 1 agreement study (stdlib only).

Pre-registration v2 (human-approved 2026-09-25, plans/phase_1_impl.md):
- A verdict passes at the >=2 cut (runner._scale_to_bool). JEV's level is
  already the P(level 2) >= 0.5 reading (evals/backends/jev.py).
- Gate metric = Cohen's kappa on that binary pass/fail, per criterion — raw
  agreement rewards a lazy always-pass judge on near-constant criteria.
- Only informative criteria count: the reference judge gave at least
  MIN_MINORITY passes AND fails.
- Uncertainty = bootstrap over task clusters (arms/runs of one task are
  near-duplicates; ~20 independent units per criterion, not 60).
- Escalation is measured on the pass/fail margin max(P2, 1-P2), not the
  3-level confidence (a {0: .5, 1: .5} verdict is a certain FAIL).

Rows are dicts with at least: llm (reference level), jev (JEV level), and
where needed margin, cluster, pack, criterion, det, key.
"""
import random
from collections import Counter, defaultdict

PASS_LEVEL = 2
MIN_MINORITY = 3
DECILES = 10
BOOTSTRAP_REPS = 1000
CI_ALPHA = 0.05


def passed(level: int) -> bool:
    return level >= PASS_LEVEL


def margin(p2: float) -> float:
    return max(p2, 1 - p2)


def qwk(a: list[int], b: list[int], k: int = 3) -> float | None:
    """Quadratic-weighted Cohen's kappa (3-level, reported alongside the gate)."""
    n = len(a)
    if n == 0:
        return None
    hist_a, hist_b = Counter(a), Counter(b)
    weight = lambda i, j: (i - j) ** 2 / (k - 1) ** 2  # noqa: E731
    obs = sum(weight(i, j) * c for (i, j), c in Counter(zip(a, b)).items()) / n
    exp = sum(weight(i, j) * hist_a[i] * hist_b[j] for i in range(k) for j in range(k)) / (n * n)
    return None if exp == 0 else 1 - obs / exp


def kappa2(ref: list[bool], jev: list[bool]) -> float | None:
    """Cohen's kappa on binary pass/fail; None when chance agreement is 1."""
    n = len(ref)
    if n == 0:
        return None
    po = sum(r == j for r, j in zip(ref, jev)) / n
    p_ref, p_jev = sum(ref) / n, sum(jev) / n
    pe = p_ref * p_jev + (1 - p_ref) * (1 - p_jev)
    return None if pe == 1 else (po - pe) / (1 - pe)


def rows_kappa2(rows: list[dict]) -> float | None:
    return kappa2([passed(r["llm"]) for r in rows], [passed(r["jev"]) for r in rows])


def binary_agreement(ref: list[int], jev: list[int]) -> float | None:
    return sum(passed(x) == passed(y) for x, y in zip(ref, jev)) / len(ref) if ref else None


def exact_agreement(ref: list[int], jev: list[int]) -> float | None:
    return sum(x == y for x, y in zip(ref, jev)) / len(ref) if ref else None


def informative(ref_levels: list[int], min_minority: int = MIN_MINORITY) -> bool:
    passes = sum(passed(v) for v in ref_levels)
    return min(passes, len(ref_levels) - passes) >= min_minority


def disagree(rows: list[dict]) -> tuple[float | None, float | None]:
    if not rows:
        return None, None
    ref, jev = [r["llm"] for r in rows], [r["jev"] for r in rows]
    return 1 - binary_agreement(ref, jev), 1 - exact_agreement(ref, jev)


def decile_table(rows: list[dict], key: str = "margin") -> list[dict]:
    ordered = sorted(rows, key=lambda r: r[key])
    n = len(ordered)
    k = min(DECILES, n)
    out = []
    for i in range(k):
        chunk = ordered[i * n // k:(i + 1) * n // k]
        dis_bin, dis_exact = disagree(chunk)
        out.append({"bin": i + 1, "n": len(chunk), "min": chunk[0][key], "max": chunk[-1][key],
                    "disagree_binary": dis_bin, "disagree_exact": dis_exact})
    return out


def threshold_table(rows: list[dict], thresholds, key: str = "margin") -> list[dict]:
    out = []
    for t in thresholds:
        kept = [r for r in rows if r[key] >= t]
        dis_bin, dis_exact = disagree(kept)
        out.append({"threshold": t, "escalation": 1 - len(kept) / len(rows) if rows else None,
                    "residual_binary": dis_bin, "residual_exact": dis_exact})
    return out


def cluster_bootstrap_ci(rows: list[dict], stat, reps: int = BOOTSTRAP_REPS, seed: int = 0,
                         alpha: float = CI_ALPHA) -> tuple[float, float] | None:
    """Percentile CI of `stat(rows)`, resampling whole task clusters."""
    clusters: dict = defaultdict(list)
    for r in rows:
        clusters[r["cluster"]].append(r)
    keys = sorted(clusters)
    rng = random.Random(seed)
    values = []
    for _ in range(reps):
        sample = [r for k in (rng.choice(keys) for _ in keys) for r in clusters[k]]
        v = stat(sample)
        if v is not None:
            values.append(v)
    if not values:
        return None
    values.sort()
    lo = values[int(alpha / 2 * (len(values) - 1))]
    hi = values[int((1 - alpha / 2) * (len(values) - 1))]
    return lo, hi


def det_cross_check(rows: list[dict], overlap: dict) -> list[dict]:
    """Rows whose deterministic check FAILED, per overlapping criterion. Not
    gate-bearing: det checks have false positives, so each row is listed for
    human adjudication. One-directional — a passed check proves nothing."""
    out = []
    for pack, checks in overlap.items():
        for det_id, criteria in checks.items():
            for crit in criteria:
                hits = [r for r in rows if r["pack"] == pack and r["criterion"] == crit
                        and (r.get("det") or {}).get(det_id) is False]
                if hits:
                    out.append({"pack": pack, "det": det_id, "criterion": crit, "n_flags": len(hits),
                                "ref_scored_2": sum(passed(r["llm"]) for r in hits) / len(hits),
                                "jev_scored_2": sum(passed(r["jev"]) for r in hits) / len(hits),
                                "keys": [r["key"] for r in hits]})
    return out
