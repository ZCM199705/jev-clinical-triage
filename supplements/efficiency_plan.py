"""Deterministic, outcome-independent controlled service-efficiency schedule."""
from collections import defaultdict
from decimal import Decimal
import random

from runtime.adapters import payload, reservation
from runtime.formal_plan import digest

SEED = 20260923
ROLES = ("jev", "luna", "gemini", "deepseek")
LEVELS = (1, 4, 8, 16, 32)


def make_schedule(cases, models):
    """Three waves; identical 200-case sequence across all cells within a wave.

    Each wave samples 5 or 6 distinct variants from each of the 34 scenarios.
    Five sequential warmups precede each cell and are excluded from timing.
    No outcome or observed performance is an input to the scheduling process.
    """
    if len(cases) != 1088 or set(models) != set(ROLES):
        raise ValueError("efficiency_source_roster")
    groups = defaultdict(list)
    for key, case in sorted(cases.items()):
        if key != case["variant_id"]:
            raise ValueError("efficiency_case_identity")
        groups[case["scenario_id"]].append(key)
    if len(groups) != 34 or any(len(v) != 32 for v in groups.values()):
        raise ValueError("efficiency_scenario_sizes")
    rng = random.Random(SEED)
    cells, jobs = [], []
    for wave in range(1, 4):
        scenario_order = sorted(groups)
        rng.shuffle(scenario_order)
        selected = []
        for index, scenario in enumerate(scenario_order):
            selected.extend(rng.sample(groups[scenario], 6 if index < 30 else 5))
        rng.shuffle(selected)
        warmup_cases = rng.sample(sorted(cases), 5)
        order = [(role, concurrency) for role in ROLES for concurrency in LEVELS]
        rng.shuffle(order)
        for role, concurrency in order:
            cell_id = f"w{wave}_{role}_c{concurrency}"
            cell = {"cell_id": cell_id, "wave": wave, "role": role,
                    "concurrency": concurrency, "order": len(cells),
                    "measured_requests": 200, "warmup_requests": 5}
            cells.append(cell)
            for phase, sequence in (("warmup", warmup_cases), ("measured", selected)):
                for position, variant in enumerate(sequence):
                    body = payload(models[role], cases[variant])
                    identity = {"stage": "efficiency_v1", "cell_id": cell_id,
                                "phase": phase, "position": position, "role": role,
                                "variant_id": variant, "request_hash": digest(body)}
                    jobs.append({**identity, "key": digest(identity),
                                 "reserved_usd": str(reservation(models[role], body))})
    return cells, jobs


def summarize_schedule(cells, jobs):
    return {"cells": len(cells), "measured_requests": sum(j["phase"] == "measured" for j in jobs),
            "warmup_requests": sum(j["phase"] == "warmup" for j in jobs),
            "total_requests": len(jobs), "seed": SEED,
            "reservation_usd": str(sum((Decimal(j["reserved_usd"]) for j in jobs), Decimal(0))),
            "by_role_usd": {role: str(sum((Decimal(j["reserved_usd"]) for j in jobs if j["role"] == role), Decimal(0))) for role in ROLES}}
