"""Paired, scenario-clustered inference with only the Python standard library."""

import itertools
import math
import random
from collections import defaultdict

SEED = 20260922
BOOTSTRAP_ITERATIONS = 5000


def paired_denominators(records):
    """Return explicit planned/common-valid denominators for paired requests."""
    rows = list(records)
    common = [r for r in rows if (r.get("model_a") or {}).get("valid_decision")
              and (r.get("model_b") or {}).get("valid_decision")]
    planned = len(rows)
    return {
        "planned_requests": planned,
        "common_valid_requests": len(common),
        "missing_or_invalid_pairs": len(rows) - len(common),
        "common_valid_correct_a": sum(bool((r.get("model_a") or {}).get("correct")) for r in common),
        "common_valid_correct_b": sum(bool((r.get("model_b") or {}).get("correct")) for r in common),
        "full_plan_valid_correct_a": sum(
            bool(r.get("model_a", {}).get("valid_decision") and r.get("model_a", {}).get("correct"))
            for r in rows
        ),
        "full_plan_valid_correct_b": sum(
            bool(r.get("model_b", {}).get("valid_decision") and r.get("model_b", {}).get("correct"))
            for r in rows
        ),
        "common_valid_correct_rate_a": (
            sum(bool(r["model_a"].get("correct")) for r in common) / len(common)
            if common else None
        ),
        "common_valid_correct_rate_b": (
            sum(bool(r["model_b"].get("correct")) for r in common) / len(common)
            if common else None
        ),
        "full_plan_correct_rate_a": (
            sum(bool(r.get("model_a", {}).get("valid_decision")
                    and r.get("model_a", {}).get("correct")) for r in rows) / planned
            if planned else None
        ),
        "full_plan_correct_rate_b": (
            sum(bool(r.get("model_b", {}).get("valid_decision")
                    and r.get("model_b", {}).get("correct")) for r in rows) / planned
            if planned else None
        ),
    }


def _scenario_differences(records, value_a="correct", value_b="correct", common_only=True):
    groups = defaultdict(list)
    for row in records:
        a = row.get("model_a") or {}
        b = row.get("model_b") or {}
        if common_only and not (a.get("valid_decision") and b.get("valid_decision")):
            continue
        groups[row["scenario_id"]].append((
            float(bool(a.get(value_a))) if a.get("valid_decision") else 0.0,
            float(bool(b.get(value_b))) if b.get("valid_decision") else 0.0,
        ))
    differences = {}
    for scenario, values in groups.items():
        mean_a = sum(x[0] for x in values) / len(values)
        mean_b = sum(x[1] for x in values) / len(values)
        differences[scenario] = mean_a - mean_b
    return differences


def paired_cluster_bootstrap(records, value_a="correct", value_b="correct",
                            seed=SEED, iterations=BOOTSTRAP_ITERATIONS,
                            common_only=True):
    """Bootstrap whole scenarios, retaining all within-scenario paired rows.

    The point estimate is the equal-weight mean of scenario-level differences.
    Empty input returns an explicit ``status`` instead of fabricating a zero.
    """
    if iterations <= 0:
        raise ValueError("iterations must be positive")
    rows = list(records)
    planned_scenarios = set(r.get("scenario_id") for r in rows)
    diffs = _scenario_differences(rows, value_a, value_b, common_only)
    values = list(diffs.values())
    if not values:
        return {"status": "empty", "estimate": None, "ci95": None,
                "n_scenarios": 0, "planned_scenarios": len(planned_scenarios),
                "excluded_scenarios": len(planned_scenarios),
                "iterations": iterations, "seed": seed}
    rng = random.Random(seed)
    n = len(values)
    boot = []
    for _ in range(iterations):
        sample = [values[rng.randrange(n)] for _ in range(n)]
        boot.append(sum(sample) / n)
    boot.sort()
    def quantile(q):
        position = (len(boot) - 1) * q
        lo, hi = math.floor(position), math.ceil(position)
        if lo == hi:
            return boot[lo]
        return boot[lo] + (boot[hi] - boot[lo]) * (position - lo)
    return {
        "status": "ok", "estimate": sum(values) / n,
        "ci95": (quantile(0.025), quantile(0.975)),
        "n_scenarios": n, "iterations": iterations, "seed": seed,
        "planned_scenarios": len(planned_scenarios),
        "excluded_scenarios": len(planned_scenarios - set(diffs)),
    }


def paired_sign_flip_pvalue(differences, seed=SEED, simulations=5000):
    """Two-sided paired sign-flip p-value on scenario-level differences.

    Exact enumeration is used through 20 scenarios; larger samples use a
    reproducible Monte Carlo sign-flip test.  This is a secondary comparison,
    not an equivalence/non-inferiority test.
    """
    values = [float(x) for x in differences if x is not None and math.isfinite(float(x))]
    if not values:
        return None
    observed = abs(sum(values) / len(values))
    if len(values) <= 20:
        totals = []
        for signs in itertools.product((-1.0, 1.0), repeat=len(values)):
            totals.append(abs(sum(v * s for v, s in zip(values, signs)) / len(values)))
        return sum(x >= observed - 1e-15 for x in totals) / len(totals)
    rng = random.Random(seed)
    extreme = 0
    for _ in range(simulations):
        candidate = abs(sum(v * (1 if rng.getrandbits(1) else -1) for v in values) / len(values))
        extreme += candidate >= observed - 1e-15
    return (extreme + 1) / (simulations + 1)


def holm_adjust(p_values):
    """Holm step-down adjusted p-values, preserving input order."""
    indexed = sorted((float(p), i) for i, p in enumerate(p_values))
    adjusted = [None] * len(indexed)
    running = 0.0
    for rank, (p, original) in enumerate(indexed):
        running = max(running, min(1.0, (len(indexed) - rank) * p))
        adjusted[original] = running
    return adjusted
