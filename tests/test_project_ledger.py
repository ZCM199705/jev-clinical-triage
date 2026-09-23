import multiprocessing
import sqlite3
from pathlib import Path

import pytest

from runtime.project_ledger import BudgetExceeded, LedgerCorruption, ProjectLedger


def make(path, prior=0):
    return ProjectLedger(path, "jev", 10, prior, "a" * 64)


def compete(args):
    path, key = args
    try:
        return make(path).reserve(key, 6, {"n": key})
    except BudgetExceeded:
        return False


def test_multiprocess_reservation_has_one_winner(tmp_path):
    path = str(tmp_path / "budget.sqlite")
    with multiprocessing.get_context("spawn").Pool(2) as pool:
        assert sorted(pool.map(compete, [(path, "a"), (path, "b")])) == [False, True]


def test_resume_dedup_and_prior_and_terminal_statuses(tmp_path):
    path = tmp_path / "budget.sqlite"
    first = make(path, prior="0.416522")
    assert first.reserve("x", "1.25", {"model": "m"})
    assert not ProjectLedger(path, "jev", 10, "0.416522", "a" * 64).reserve("x", 1.25, {"model": "m"})
    first.finish("x", {"status": "success", "cost": "1.25"})
    first.finish("x", {"status": "success", "cost": "1.25"})
    first.reserve("failed", 2, {})
    first.finish("failed", {"status": "timeout"})
    snap = first.snapshot()
    assert snap["reserved_usd"] == "3.666522"
    assert snap["attempts"] == 2 and snap["completed"] == 2
    assert snap["status_counts"] == {"terminal": 2}


def test_interrupted_remains_committed_and_unresolved_is_explicit(tmp_path):
    ledger = make(tmp_path / "budget.sqlite")
    ledger.reserve("u", 1, {})
    assert ledger.snapshot()["unresolved_keys"] == ["u"]
    ledger.mark_interrupted("u")
    assert ledger.get("u")["status"] == "terminal"
    assert ledger.get("u")["result"]["status"] == "interrupted_unknown"
    assert ledger.snapshot()["reserved_usd"] == "1"


def test_drift_corruption_and_invalid_numbers_fail_closed(tmp_path):
    path = tmp_path / "budget.sqlite"
    ledger = make(path)
    with pytest.raises((ValueError, BudgetExceeded)):
        ledger.reserve("nan", float("nan"), {})
    with pytest.raises(ValueError):
        ledger.reserve("negative", -1, {})
    with pytest.raises(ValueError):
        ledger.reserve("x", 1, {"bad": float("nan")})
    assert ledger.reserve("x", 1, {})
    with pytest.raises(ValueError):
        ledger.reserve("x", 2, {})
    with pytest.raises(LedgerCorruption):
        ProjectLedger(path, "other", 10, 0, "a" * 64)
    con = sqlite3.connect(path)
    con.execute("UPDATE ledger_meta SET cap_usd='11' WHERE id=1")
    con.commit()
    con.close()
    with pytest.raises(LedgerCorruption):
        make(path)


def test_existing_empty_or_deleted_database_cannot_be_recreated(tmp_path):
    empty = tmp_path / "existing.sqlite"
    empty.touch()
    with pytest.raises(LedgerCorruption):
        make(empty)

    deleted = tmp_path / "deleted.sqlite"
    make(deleted)
    deleted.unlink()
    with pytest.raises(LedgerCorruption):
        make(deleted)


def test_deleted_schema_fails_closed(tmp_path):
    path = tmp_path / "schema.sqlite"
    make(path)
    con = sqlite3.connect(path)
    con.execute("DROP TABLE ledger_meta")
    con.commit()
    con.close()
    with pytest.raises(LedgerCorruption):
        make(path)
