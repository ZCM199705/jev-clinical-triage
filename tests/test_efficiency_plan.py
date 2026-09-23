from collections import Counter

from supplements.efficiency_plan import make_schedule, summarize_schedule, ROLES


def test_schedule_is_balanced_identical_unique_and_reproducible():
    cases = {f"s{s:02}_v{v:02}": {"variant_id": f"s{s:02}_v{v:02}",
              "scenario_id": f"s{s:02}", "case_text": f"Synthetic case {s} {v}"}
             for s in range(34) for v in range(32)}
    models = {role: role for role in ROLES}
    cells, jobs = make_schedule(cases, models)
    assert (cells, jobs) == make_schedule(cases, models)
    assert len(cells) == 60 and len(jobs) == 12300
    assert len({j['key'] for j in jobs}) == 12300
    for wave in range(1, 4):
        sequences = []
        for cell in [c for c in cells if c['wave'] == wave]:
            sequence = [j['variant_id'] for j in jobs if j['cell_id'] == cell['cell_id'] and j['phase'] == 'measured']
            sequences.append(sequence)
            assert len(sequence) == len(set(sequence)) == 200
            counts = Counter(cases[v]['scenario_id'] for v in sequence)
            assert Counter(counts.values()) == {6: 30, 5: 4}
        assert all(s == sequences[0] for s in sequences)
    summary = summarize_schedule(cells, jobs)
    assert summary['measured_requests'] == 12000
    assert summary['warmup_requests'] == 300
