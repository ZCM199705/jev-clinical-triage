import asyncio
from contextlib import redirect_stdout
from decimal import Decimal
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

import httpx

from runtime.adapters import payload, reservation
from runtime.formal_runner import FirstRoundRunner, digest, validate_software
from runtime.project_ledger import ProjectLedger, LedgerCorruption


class FormalRunnerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.model = next(m for m in json.loads(Path("configs/pilot_candidate.json").read_text())["models"] if m["role"] == "luna")
        self.case = {"case_text": "Synthetic engineering test: mild, resolved symptom.",
                     "acceptable_labels": ["D"], "diagnosis": "DO_NOT_SEND", "variant_id": "toy01"}
        self.body = payload(self.model, self.case)
        self.amount = reservation(self.model, self.body)
        base = {"role": "luna", "variant_id": "toy01", "condition": "structured", "repeat_id": 1,
                "request_hash": digest(self.body)}
        self.job = {**base, "key": digest(base), "reserved_usd": str(self.amount)}
        self.manifest = {"freeze_digest": "test-freeze", "max_output_tokens": 4096, "total_timeout_seconds": 0.1}
        self.ledger = ProjectLedger(self.root / "ledger.sqlite", "test", "1", "0.4", "prior-hash")
        self.calls = 0

    def tearDown(self):
        self.temp.cleanup()

    def response(self, **overrides):
        data = {"id": "fixture", "model": "openai/gpt-5.6-luna", "provider": "OpenAI",
                "usage": {"cost": 0.00001},
                "choices": [{"finish_reason": "stop", "message": {"content": '{"triage":"A"}'}}]}
        data.update(overrides)
        return data

    def runner(self, client, **kwargs):
        return FirstRoundRunner(self.manifest, {"luna": self.model}, {"toy01": self.case},
                                [self.job], self.ledger, self.root / "run", {"luna": "sk-test-secret"}, client, **kwargs)

    async def test_reserve_before_http_and_resume_without_duplicate(self):
        async def handler(request):
            self.calls += 1
            self.assertEqual(self.ledger.get(self.job["key"])["status"], "reserved")
            self.assertNotIn("DO_NOT_SEND", request.content.decode())
            self.assertNotIn("acceptable_labels", request.content.decode())
            return httpx.Response(200, json=self.response(debug="sk-test-secret"))
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with redirect_stdout(io.StringIO()):
                first = await self.runner(client).run()
                second = await self.runner(client).run()
        self.assertEqual(self.calls, 1)
        self.assertEqual(first["status_counts"], {"success": 1})
        self.assertEqual(second["new_attempts_this_invocation"], 0)
        for path in (self.root / "run/responses").glob("*.json"):
            self.assertNotIn("sk-test-secret", path.read_text())
        self.assertEqual(Decimal(self.ledger.snapshot()["reserved_usd"]), Decimal("0.4") + self.amount)

    async def test_budget_exhaustion_prevents_network(self):
        self.ledger = ProjectLedger(self.root / "tiny.sqlite", "test", "0.400001", "0.4", "prior-hash")
        async def handler(request):
            self.calls += 1
            return httpx.Response(200, json=self.response())
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with redirect_stdout(io.StringIO()):
                outcome = await self.runner(client).run()
        self.assertEqual(self.calls, 0)
        self.assertEqual(outcome["new_attempts_this_invocation"], 0)
        self.assertEqual(outcome["halt_reason"], "project_budget_exhausted")

    async def test_total_timeout_keeps_reservation_and_is_not_resent(self):
        async def handler(request):
            self.calls += 1
            await asyncio.sleep(0.3)
            return httpx.Response(200, json=self.response())
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with redirect_stdout(io.StringIO()):
                await self.runner(client).run()
                await self.runner(client).run()
        self.assertEqual(self.calls, 1)
        self.assertEqual(self.ledger.get(self.job["key"])["result"]["status"], "timeout")
        self.assertEqual(Decimal(self.ledger.snapshot()["reserved_usd"]), Decimal("0.4") + self.amount)

    async def test_identity_and_unknown_or_excess_cost_halt(self):
        fixtures = [(self.response(provider="unexpected"), "identity_error"),
                    (self.response(usage={}), "cost_error"),
                    (self.response(usage={"cost": 0.9}), "cost_error")]
        for i, (data, expected) in enumerate(fixtures):
            self.ledger = ProjectLedger(self.root / f"error{i}.sqlite", "test", "1", "0.4", "prior-hash")
            async def handler(request):
                return httpx.Response(200, json=data)
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                runner = self.runner(client)
                runner.directory = self.root / f"error_run{i}"
                with redirect_stdout(io.StringIO()):
                    outcome = await runner.run()
            self.assertEqual(outcome["status_counts"], {expected: 1})
            self.assertTrue(outcome["halt_reason"])

    async def test_crash_recovery_marks_unknown_and_never_resends(self):
        async def handler(request):
            self.fail("Interrupted request must not be sent again")
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            runner = self.runner(client)
            metadata = {**self.job, "freeze_digest": self.manifest["freeze_digest"],
                        "model_requested": self.model["model_requested"], "endpoint": self.model["endpoint"],
                        "software_hashes": runner.software_hashes, "request_time": "fixture"}
            self.ledger.reserve(self.job["key"], self.amount, metadata)
            with redirect_stdout(io.StringIO()):
                outcome = await runner.run()
        self.assertEqual(outcome["status_counts"], {"interrupted_unknown": 1})

    def test_missing_reservations_table_cannot_reset_budget(self):
        self.ledger.reserve("spent", "0.1", {})
        with sqlite3.connect(self.root / "ledger.sqlite") as con:
            con.execute("DROP TABLE reservations")
        with self.assertRaises(LedgerCorruption):
            ProjectLedger(self.root / "ledger.sqlite", "test", "1", "0.4", "prior-hash")

    async def test_resume_rejects_inconsistent_metadata_before_network(self):
        async def handler(request):
            self.fail("corrupt attempt metadata must not reach network")
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            runner = self.runner(client)
            metadata = {**self.job, "role": "gemini", "freeze_digest": self.manifest["freeze_digest"],
                        "model_requested": self.model["model_requested"], "endpoint": self.model["endpoint"],
                        "software_hashes": runner.software_hashes}
            self.ledger.reserve(self.job["key"], self.amount, metadata)
            self.ledger.finish(self.job["key"], {"status": "success"})
            with self.assertRaises(ValueError):
                await runner.initialize()

    def test_software_change_is_blocked(self):
        path = self.root / "module.py"
        path.write_text("original")
        import hashlib
        manifest = {"software_hashes": {"module.py": hashlib.sha256(path.read_bytes()).hexdigest()}}
        validate_software(manifest, self.root)
        path.write_text("changed")
        with self.assertRaises(ValueError):
            validate_software(manifest, self.root)


if __name__ == "__main__":
    unittest.main()
