"""Rebuild the final manuscript figure tables from the archived response evidence."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


HERE = Path(__file__).resolve().parents[1]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as stream:
        return list(csv.DictReader(stream))


def check_claims(figures: Path, workspace: Path) -> dict[str, object]:
    source = figures / "source_data"
    first = {r["role"]: r for r in rows(source / "Figure1a.csv") if r["cohort"] == "main_clear"}
    assert {role: int(r["correct"]) for role, r in first.items()} == {
        "jev": 353, "luna": 301, "gemini": 285, "deepseek": 292
    }
    paired = rows(source / "Figure1b.csv")
    assert len(paired) == 3 and abs(float(paired[0]["difference_pp"]) - 10.8333333333) < 1e-6
    stable = {r["role"]: r for r in rows(source / "Figure1d.csv")}
    jev = stable["jev"]
    assert [int(jev[k]) for k in (
        "stable_correct", "stable_incorrect", "changed_all_acceptable",
        "changed_any_incorrect", "technical_failure"
    )] == [741, 145, 33, 27, 14]
    assert (int(jev["identical"]), int(jev["valid"])) == (886, 946)
    high = next(r for r in rows(source / "Figure2a_thresholds.csv") if float(r["threshold"]) == .9)
    assert (int(high["accepted"]), int(high["errors"])) == (152, 8)
    assert len(rows(source / "Figure2a_inputs.csv")) == 476
    assert len(rows(source / "FigureS1a.csv")) == 12
    assert len(rows(source / "FigureS1b.csv")) == 24
    assert len(rows(source / "FigureS3a.csv")) == 4
    assert len(rows(source / "FigureS3a_rounds.csv")) == 12
    assert all(r["mask"] == "all" for r in rows(source / "FigureS3a_rounds.csv"))
    service = rows(source / "FigureS4.csv")
    assert len(service) == 60 and sum(int(r["planned"]) for r in service) == 12000
    assert sum(int(r["warmup_requests"]) for r in service) == 300
    costs = {r["role"]: r for r in rows(figures / "Table1.csv")}
    assert all(float(costs[role]["http_p50_s_median"]) > 0 for role in first)
    report = json.loads((workspace / "reports/three_round_v1/results.json").read_text())
    assert report["audit"]["counts"] == {"success": 13041, "parse_error": 14, "request_error": 1}
    assert [r["p_holm"] for r in report["secondary_tests"]] == [.0859375, .0859375]
    return {
        "structured_requests": 13056,
        "structured_status_counts": report["audit"]["counts"],
        "jev_stability": [741, 145, 33, 27, 14],
        "high_probability_disagreement": [8, 152],
        "figure_source_tables": len(list(source.glob("*.csv"))),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-release", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    base, data, out = (p.resolve() for p in (args.base_release, args.data_dir, args.output_dir))
    if out.exists() and any(out.iterdir()):
        parser.error("output directory must be empty")
    subprocess.run([
        sys.executable, str(base / "reproduce.py"), "--data-dir", str(data),
        "--output-dir", str(out)
    ], check=True)
    target = out / "scripts/make_manuscript_figures_brief_v4.py"
    shutil.copyfile(HERE / "scripts/make_manuscript_figures_brief_v4.py", target)
    templates = out / "scripts/templates/brief_v4"
    templates.mkdir(parents=True)
    for source in (HERE / "scripts/templates/brief_v4").iterdir():
        shutil.copyfile(source, templates / source.name)
    environment = {k: v for k, v in os.environ.items() if not any(
        word in k.upper() for word in ("API_KEY", "TOKEN", "SECRET", "PASSWORD")
    )}
    environment["PYTHONPATH"] = str(out / "offline_guard")
    environment["MPLCONFIGDIR"] = str(out / ".mpl-cache")
    subprocess.run([sys.executable, str(target)], cwd=out, env=environment, check=True)
    expected = HERE / "figures/manuscript_brief_v4"
    actual = out / "reports/manuscript_figures_brief_v4"
    files = sorted(expected.rglob("*.csv"))
    assert files
    for source in files:
        destination = actual / source.relative_to(expected)
        assert destination.is_file() and digest(source) == digest(destination), str(destination)
    result = check_claims(actual, out)
    result.update({"passed": True, "matched_source_csv": len(files), "network_guard": True})
    (out / "FINAL_FIGURE_CHECKS.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
