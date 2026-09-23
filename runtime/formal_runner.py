"""First-round transport with a fixed project ledger and immutable request plan.

No automatic retries: every planned decision has one permanently reserved
attempt. Interrupted attempts remain unknown and are never silently resent.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import ssl
import time

import certifi
import httpx

from runtime.adapters import payload, reservation, parse_response, response_cost, validate_identity
from runtime.formal_plan import load_freeze
from runtime.project_ledger import ProjectLedger, BudgetExceeded

ROOT = Path(__file__).resolve().parents[1]
PROJECT_ID = "jev_triage_20260922"
FILES = {"jev": "JevAPI_openrouter.txt", "luna": "openaiAPI_openrouter.txt",
         "gemini": "GeminiAPI_openrouter.txt", "deepseek": "deepseekAPI.txt"}
FATAL_STATUSES = {"identity_error", "cost_error", "configuration_error"}


def stamp():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode()).hexdigest()


def redact(value, keys):
    for key in keys:
        value = value.replace(key, "[REDACTED]")
    return re.sub(r"sk-[A-Za-z0-9_-]+", "[REDACTED]", value)


def write_json(path, value, *, exclusive=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"
    target = path if exclusive else path.with_suffix(path.suffix + ".tmp")
    with target.open("x" if exclusive else "w", encoding="utf-8") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    if not exclusive:
        os.replace(target, path)


def budget_evidence(root=ROOT):
    config = json.loads((root / "configs/project_budget.json").read_text())
    if not (Decimal(config["project_total_cap_usd"]) == 200
            and Decimal(config["automatic_api_allocation_usd"]) == 180
            and Decimal(config["unallocated_contingency_usd"]) == 20):
        raise ValueError("budget_authorization_changed")
    summary = json.loads((root / "reports/preflight/pilot_summary.json").read_text())
    raw = (root / "runs/pilot_20260922/ledger.jsonl").read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    if sha != summary["ledger_sha256"]:
        raise ValueError("prior_pilot_evidence_hash_mismatch")
    records = [json.loads(line) for line in raw.splitlines()]
    starts = [r for r in records if r["event"] == "attempt_started"]
    ends = [r for r in records if r["event"] == "attempt_finished"]
    prior = sum((Decimal(r["reserved_usd"]) for r in starts), Decimal(0))
    if (len(starts) != 80 or len(ends) != 80 or prior != Decimal("0.416522")
            or prior != Decimal(config["prior_pilot"]["permanent_budget_reservation_usd"])):
        raise ValueError("prior_pilot_reservation_mismatch")
    return config, str(prior), sha


def read_keys(directory, models):
    keys = {}
    for role in models:
        key = (directory / FILES[role]).read_text().strip()
        if not re.fullmatch(r"sk-[A-Za-z0-9_-]+", key):
            raise ValueError("invalid_credential_format")
        keys[role] = key
    return keys


def validate_software(manifest, root=ROOT):
    if not manifest.get("software_hashes"):
        raise ValueError("frozen_software_manifest_missing")
    for name, expected in manifest["software_hashes"].items():
        if hashlib.sha256((root / name).read_bytes()).hexdigest() != expected:
            raise ValueError("frozen_software_changed")


class FirstRoundRunner:
    def __init__(self, manifest, models, cases, jobs, ledger, directory, keys,
                 client, max_new_attempts=None):
        self.manifest, self.models, self.cases, self.jobs = manifest, models, cases, jobs
        self.ledger, self.directory, self.keys, self.client = ledger, Path(directory), keys, client
        self.max_new = max_new_attempts
        self.new_attempts = 0
        self.claimed_jobs = 0
        self.stop = asyncio.Event()
        self.permit_lock = asyncio.Lock()
        self.results = {}
        self.halt_reason = None
        self.software_hashes = {
            str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted((ROOT / "runtime").glob("*.py"))
        }

    async def initialize(self):
        # Production owns an exclusive runner lock while reconciling unknowns.
        snap = await asyncio.to_thread(self.ledger.snapshot)
        for key in snap["unresolved_keys"]:
            await asyncio.to_thread(self.ledger.mark_interrupted, key)
        for job in self.jobs:
            row = await asyncio.to_thread(self.ledger.get, job["key"])
            if row is None:
                continue
            expected = {**job, "freeze_digest": self.manifest["freeze_digest"],
                        "model_requested": self.models[job["role"]]["model_requested"],
                        "endpoint": self.models[job["role"]]["endpoint"],
                        "software_hashes": self.software_hashes}
            if (any(row["metadata"].get(k) != v for k, v in expected.items()) or
                    Decimal(row["reserved_usd"]) != Decimal(job["reserved_usd"])):
                raise ValueError("existing_attempt_request_mismatch")
            if row["result"]:
                self.results[job["key"]] = row["result"]
                if row["result"]["status"] in FATAL_STATUSES:
                    raise ValueError("prior_fatal_contract_failure_requires_technical_review")

    async def progress(self):
        snap = await asyncio.to_thread(self.ledger.snapshot)
        counts = Counter(r["status"] for r in self.results.values())
        by_model = {role: dict(Counter(self.results[j["key"]]["status"]
                    for j in self.jobs if j["role"] == role and j["key"] in self.results))
                    for role in self.models}
        costs = [Decimal(r["cost_usd"]) for r in self.results.values() if r.get("cost_usd") is not None]
        report = {
            "updated_at": stamp(), "freeze_digest": self.manifest["freeze_digest"],
            "planned": len(self.jobs), "completed": len(self.results), "status_counts": dict(counts),
            "by_model": by_model, "new_attempts_this_invocation": self.new_attempts,
            "reserved_project_usd": snap["reserved_usd"],
            "first_round_provider_reported_plus_upper_estimate_usd": str(sum(costs, Decimal(0))),
            "unknown_cost_attempts": sum(r.get("cost_usd") is None for r in self.results.values()),
            "halt_reason": self.halt_reason, "complete": len(self.results) == len(self.jobs),
            "clinical_results_computed": False,
        }
        write_json(self.directory / "progress.json", report)
        print(json.dumps(report, ensure_ascii=False), flush=True)
        return report

    async def perform(self, job):
        role = job["role"]
        model, case = self.models[role], self.cases[job["variant_id"]]
        body = payload(model, case, job["condition"], self.manifest["max_output_tokens"])
        amount = reservation(model, body, self.manifest["max_output_tokens"])
        if digest(body) != job["request_hash"] or amount != Decimal(job["reserved_usd"]):
            raise ValueError("frozen_request_or_reservation_changed")
        metadata = {**job, "freeze_digest": self.manifest["freeze_digest"],
                    "model_requested": model["model_requested"], "endpoint": model["endpoint"],
                    "software_hashes": self.software_hashes, "request_time": stamp()}
        newly_reserved = await asyncio.to_thread(self.ledger.reserve, job["key"], str(amount), metadata)
        if not newly_reserved:
            return None  # Never send an already reserved key.
        self.new_attempts += 1
        start = time.monotonic()
        result = {"status": "request_error", "role": role, "variant_id": job["variant_id"],
                  "repeat_id": job["repeat_id"], "condition": job["condition"],
                  "request_time": metadata["request_time"], "cost_usd": None, "cost_status": "unknown"}
        raw = None
        try:
            response = await asyncio.wait_for(
                self.client.post(model["endpoint"], json=body,
                                 headers={"Authorization": "Bearer " + self.keys[role],
                                          "Content-Type": "application/json"}),
                timeout=self.manifest["total_timeout_seconds"],
            )
            result["http_status"] = response.status_code
            raw = redact(response.text, list(self.keys.values()))
            try:
                data = json.loads(raw)
            except (ValueError, TypeError):
                data = None
            if isinstance(data, dict):
                result["usage"] = data.get("usage")
                result["model_returned"] = data.get("model")
                result["provider_returned"] = data.get("provider", "DeepSeek" if role == "deepseek" else None)
                result["response_id"] = data.get("id")
                result["cost_usd"], result["cost_status"] = response_cost(model, data)
            if result["cost_usd"] is not None and Decimal(result["cost_usd"]) > amount:
                result["status"] = "cost_error"
                result["error_code"] = "cost_exceeds_permanent_reservation"
            elif response.status_code != 200:
                result["status"] = "configuration_error" if response.status_code in (400, 401, 402, 403, 404, 422) else "request_error"
                result["error_code"] = "http_" + str(response.status_code)
            elif not isinstance(data, dict):
                result["status"] = "parse_error"
                result["error_code"] = "response_not_json_object"
            else:
                try:
                    validate_identity(model, data)
                except (ValueError, TypeError):
                    result["status"] = "identity_error"
                    result["error_code"] = "unexpected_model_or_provider"
                else:
                    if result["cost_usd"] is None:
                        result["status"] = "cost_error"
                        result["error_code"] = "successful_response_cost_unknown"
                    else:
                        try:
                            result["parsed"] = parse_response(model, data, job["condition"])
                            result["status"] = "success"
                        except (ValueError, TypeError, KeyError):
                            choice = (data.get("choices") or [{}])[0]
                            refused = role != "jev" and isinstance(choice, dict) and (
                                choice.get("finish_reason") == "content_filter" or
                                bool((choice.get("message") or {}).get("refusal")))
                            result["status"] = "refusal" if refused else "parse_error"
                            result["error_code"] = "no_valid_frozen_structured_decision"
        except (asyncio.TimeoutError, httpx.TimeoutException):
            result["status"] = "timeout"
        except httpx.HTTPError:
            result["status"] = "request_error"
            result["error_code"] = "transport_error"
        except Exception as exc:
            # Do not serialize exceptions that may include headers or secrets.
            result["status"] = "configuration_error"
            result["error_code"] = type(exc).__name__
        result["response_time"] = stamp()
        result["latency_ms"] = round((time.monotonic() - start) * 1000, 3)
        # Raw evidence is persisted before the terminal ledger event.
        evidence = {"job": job, "request_payload": body, "result": result, "raw_response": raw}
        response_path = self.directory / "responses" / (job["key"] + ".json")
        write_json(response_path, evidence, exclusive=True)
        result["response_file"] = str(response_path)
        result["response_file_sha256"] = hashlib.sha256(response_path.read_bytes()).hexdigest()
        await asyncio.to_thread(self.ledger.finish, job["key"], result)
        self.results[job["key"]] = result
        if result["status"] in FATAL_STATUSES:
            self.halt_reason = result.get("error_code", result["status"])
            self.stop.set()
        return result

    async def worker(self, role):
        consecutive_transport_errors = 0
        for job in self.jobs:
            if job["role"] != role or job["key"] in self.results:
                continue
            async with self.permit_lock:
                if self.stop.is_set() or (self.max_new is not None and self.claimed_jobs >= self.max_new):
                    return
                self.claimed_jobs += 1
            begin = time.monotonic()
            try:
                result = await self.perform(job)
            except BudgetExceeded:
                self.halt_reason = "project_budget_exhausted"
                self.stop.set()
                return
            except Exception as exc:
                self.halt_reason = "local_failure_" + type(exc).__name__
                self.stop.set()
                raise
            if result and result["status"] in {"timeout", "request_error"}:
                consecutive_transport_errors += 1
            else:
                consecutive_transport_errors = 0
            if consecutive_transport_errors >= 3:
                self.halt_reason = "three_consecutive_transport_errors_" + role
                self.stop.set()
            if len(self.results) % 25 == 0 or self.stop.is_set():
                await self.progress()
            # Standard baseline collection; not an efficiency/concurrency benchmark.
            await asyncio.sleep(max(0, 1.0 - (time.monotonic() - begin)))

    async def run(self):
        await self.initialize()
        await self.progress()
        await asyncio.gather(*(self.worker(role) for role in self.models))
        return await self.progress()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--credentials-directory", type=Path)
    parser.add_argument("--max-new-attempts", type=int)
    parser.add_argument("--freeze", type=Path, default=ROOT / "freezes/first_round_v1")
    parser.add_argument("--run-directory", type=Path)
    args = parser.parse_args()
    if args.max_new_attempts is not None and args.max_new_attempts <= 0:
        raise ValueError("max_new_attempts_must_be_positive")
    freeze_path = args.freeze if args.freeze.is_absolute() else ROOT / args.freeze
    manifest, models, cases, jobs = load_freeze(freeze_path)
    budget, prior, prior_sha = budget_evidence()
    total = sum((Decimal(j["reserved_usd"]) for j in jobs), Decimal(0))
    if manifest["study_id"] != PROJECT_ID or total + Decimal(prior) > Decimal("180"):
        raise ValueError("formal_round_not_within_authorized_scope")
    if {m["model_requested"] for m in models.values()} != set(budget["allowed_models"]):
        raise ValueError("authorized_model_roster_mismatch")
    validate_software(manifest)
    print(json.dumps({"execute": args.execute, "jobs": len(jobs), "frozen_reservation_usd": str(total),
                      "prior_reservation_usd": prior, "project_automatic_limit_usd": "180"}), flush=True)
    if not args.execute:
        return
    if not budget["live_enabled"] or not args.credentials_directory:
        raise ValueError("live_disabled_or_credentials_missing")
    directory = args.run_directory or (ROOT / "runs" / freeze_path.name)
    if not directory.is_absolute():
        directory = ROOT / directory
    directory.mkdir(parents=True, exist_ok=True)
    # Separate process lock prevents recovery from marking a still-live request
    # unknown. SQL transactions remain the project-wide reservation authority.
    with (ROOT / "runs/project_runner.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        ledger = ProjectLedger(ROOT / "runs/project_budget.sqlite", PROJECT_ID, "180", prior, prior_sha)
        keys = read_keys(args.credentials_directory, models)
        async def execute():
            ssl_context = ssl.create_default_context(cafile=certifi.where())
            timeout = httpx.Timeout(manifest["socket_timeout_seconds"], connect=manifest["connect_timeout_seconds"])
            async with httpx.AsyncClient(verify=ssl_context, timeout=timeout, follow_redirects=False,
                                        limits=httpx.Limits(max_connections=4, max_keepalive_connections=4)) as client:
                runner = FirstRoundRunner(manifest, models, cases, jobs, ledger, directory, keys,
                                          client, args.max_new_attempts)
                return await runner.run()
        outcome = asyncio.run(execute())
        if outcome["halt_reason"]:
            raise SystemExit(2)


if __name__ == "__main__":
    main()
