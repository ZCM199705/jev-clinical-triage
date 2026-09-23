"""Create a frozen structured repeat round from the completed first-round plan."""
from __future__ import annotations

import argparse, hashlib, json, random, shutil
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()

def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()

def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeat-id", type=int, required=True, choices=(2, 3))
    ap.add_argument("--destination", type=Path, required=True)
    args = ap.parse_args()
    source = ROOT / "freezes/first_round_v1"
    dest = args.destination if args.destination.is_absolute() else ROOT / args.destination
    if dest.exists() and any(dest.iterdir()):
        raise SystemExit("freeze_destination_not_empty")
    dest.mkdir(parents=True, exist_ok=True)
    for name in ("models.json", "cases.jsonl", "DATA_LICENSE.txt"):
        shutil.copy2(source / name, dest / name)
    protocol = (source / "protocol.md").read_text()
    protocol += f"\n\n## Structured repeat round {args.repeat_id}\n\nThis is a pre-specified repeat of the frozen structured first round. Inputs, models, endpoints, prompts, scoring, and schedule policy are unchanged; only repeat_id and request keys differ.\n"
    (dest / "protocol.md").write_text(protocol)
    cases = [json.loads(x) for x in (source / "cases.jsonl").read_text().splitlines() if x]
    old_jobs = [json.loads(x) for x in (source / "jobs.jsonl").read_text().splitlines() if x]
    jobs = []
    for old in old_jobs:
        row = dict(old)
        row["repeat_id"] = args.repeat_id
        row["key"] = digest({"role": row["role"], "variant_id": row["variant_id"],
                              "condition": row["condition"], "repeat_id": args.repeat_id,
                              "request_hash": row["request_hash"]})
        jobs.append(row)
    random.Random(20260922 + args.repeat_id).shuffle(jobs)
    (dest / "jobs.jsonl").write_text("".join(json.dumps(x, ensure_ascii=False, sort_keys=True) + "\n" for x in jobs))
    (dest / "rules.json").write_text(json.dumps({
        "condition": "structured", "round": args.repeat_id,
        "repeat_of": "first_round_v1", "repeat_id": args.repeat_id,
        "purpose": "pre-specified stability repeat; no post-result prompt/model changes",
        "first_attempt_policy": "one attempt per job; no automatic retries",
        "primary_subset": {"dataset_id": "nm_main", "label_type": "clear", "repeat_id": args.repeat_id},
        "inherits": "first_round_v1/rules.json"
    }, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    total = sum((Decimal(x["reserved_usd"]) for x in jobs), Decimal(0))
    manifest = json.loads((source / "manifest.json").read_text())
    manifest.update({"repeat_id": args.repeat_id, "round_label": f"structured_repeat_{args.repeat_id}",
                     "schedule_seed": 20260922 + args.repeat_id,
                     "created_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
                     "total_reservation_usd": str(total), "file_hashes": {}})
    manifest["software_hashes"] = {
        str(p.relative_to(ROOT)): file_hash(p)
        for pattern in ("runtime/*.py", "analysis/*.py") for p in ROOT.glob(pattern)
    }
    manifest["n_jobs"] = len(jobs)
    manifest["file_hashes"] = {name: file_hash(dest / name) for name in
                                ("models.json", "cases.jsonl", "jobs.jsonl", "rules.json", "protocol.md")}
    manifest.pop("freeze_digest", None)
    manifest["freeze_digest"] = digest(manifest)
    (dest / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    print(json.dumps({"destination": str(dest), "repeat_id": args.repeat_id,
                      "jobs": len(jobs), "reservation_usd": str(total),
                      "freeze_digest": manifest["freeze_digest"]}, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
