import tempfile
import unittest
from pathlib import Path

from runtime.formal_plan import build_freeze, load_freeze


class FormalPlanTests(unittest.TestCase):
    def test_build_load_and_immutable_hashes(self):
        with tempfile.TemporaryDirectory() as temp:
            destination = Path(temp) / "freeze"
            manifest = build_freeze(destination)
            self.assertEqual(manifest["n_jobs"], 4352)
            loaded = load_freeze(destination)
            self.assertEqual(len(loaded[1]), 4)
            self.assertEqual(len(loaded[2]), 1088)
            self.assertEqual(len(loaded[3]), 4352)
            with self.assertRaises(FileExistsError): build_freeze(destination)

    def test_load_rejects_tampered_jobs(self):
        with tempfile.TemporaryDirectory() as temp:
            destination = Path(temp) / "freeze"
            build_freeze(destination)
            jobs = destination / "jobs.jsonl"
            jobs.write_text(jobs.read_text().replace('"repeat_id": 1', '"repeat_id": 2', 1))
            with self.assertRaises(ValueError): load_freeze(destination)


if __name__ == "__main__":
    unittest.main()
