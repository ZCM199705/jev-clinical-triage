"""Durable, cross-process project budget reservations."""

from __future__ import annotations

import json
import fcntl
import os
import math
import sqlite3
import time
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


class BudgetExceeded(RuntimeError):
    """The reservation would exceed the configured project cap."""


class LedgerCorruption(RuntimeError):
    """The ledger is missing, malformed, or has drifted fixed metadata."""


def _money(value: Any, *, allow_zero: bool = True) -> Decimal:
    if isinstance(value, bool):
        raise ValueError("amount must be a finite number")
    try:
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError("amount must be a finite number") from None
    if not result.is_finite() or (result < 0 if allow_zero else result <= 0):
        raise ValueError("amount must be positive and finite" if not allow_zero else "amount must be non-negative and finite")
    return result


def _money_text(value: Decimal) -> str:
    return format(value, "f")


def _json(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("value must be JSON-serializable") from exc


class ProjectLedger:
    def __init__(self, path, project_id, cap_usd, prior_reservation_usd, prior_evidence_sha256):
        self.path = Path(path)
        self.project_id = str(project_id)
        if not self.project_id:
            raise ValueError("project_id is required")
        self.cap = _money(cap_usd, allow_zero=False)
        self.prior = _money(prior_reservation_usd)
        if self.prior > self.cap:
            raise ValueError("prior reservation exceeds cap")
        self.evidence = str(prior_evidence_sha256)
        if not self.evidence:
            raise ValueError("prior evidence hash is required")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._marker = Path(str(self.path) + ".initialized")
        # Serialize initialization, including marker contents, across processes.
        with Path(str(self.path) + ".init.lock").open("a") as init_lock:
            fcntl.flock(init_lock, fcntl.LOCK_EX)
            self._new_file = not self.path.exists() and not self._marker.exists()
            if self._new_file:
                with self._marker.open("x", encoding="ascii") as marker:
                    marker.write("initializing\n")
                    marker.flush()
                    os.fsync(marker.fileno())
            else:
                if not self.path.exists() or not self._marker.exists():
                    raise LedgerCorruption("existing ledger or initialization marker missing")
                if self._marker.read_text(encoding="ascii").strip() != "project-ledger-v1":
                    raise LedgerCorruption("ledger initialization incomplete")
            self._ensure()
            if self._new_file:
                temporary = Path(str(self._marker) + ".tmp")
                with temporary.open("w", encoding="ascii") as marker:
                    marker.write("project-ledger-v1\n")
                    marker.flush()
                    os.fsync(marker.fileno())
                os.replace(temporary, self._marker)

    def _connect(self):
        deadline = time.monotonic() + 30
        while True:
            con = None
            try:
                con = sqlite3.connect(str(self.path), timeout=30, isolation_level=None)
                con.execute("PRAGMA busy_timeout=30000")
                con.execute("PRAGMA journal_mode=WAL")
                con.execute("PRAGMA synchronous=FULL")
                con.execute("PRAGMA foreign_keys=ON")
                return con
            except sqlite3.OperationalError as exc:
                if con is not None:
                    con.close()
                if "locked" in str(exc).lower() and time.monotonic() < deadline:
                    time.sleep(0.05)
                    continue
                raise LedgerCorruption("cannot open ledger") from exc
            except sqlite3.Error as exc:
                if con is not None:
                    con.close()
                raise LedgerCorruption("cannot open ledger") from exc

    def _ensure(self):
        con = self._connect()
        try:
            con.execute("BEGIN IMMEDIATE")
            if not self._new_file:
                tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if not {"ledger_meta", "reservations"}.issubset(tables):
                    raise LedgerCorruption("existing ledger tables missing")
            con.execute("CREATE TABLE IF NOT EXISTS ledger_meta (id INTEGER PRIMARY KEY CHECK(id=1), project_id TEXT NOT NULL, cap_usd TEXT NOT NULL, prior_reservation_usd TEXT NOT NULL, prior_evidence_sha256 TEXT NOT NULL)")
            con.execute("CREATE TABLE IF NOT EXISTS reservations (key TEXT PRIMARY KEY, amount_usd TEXT NOT NULL, metadata_json TEXT NOT NULL, status TEXT NOT NULL, result_json TEXT, CHECK(status IN ('reserved','terminal')), CHECK(length(key)>0))")
            row = con.execute("SELECT project_id, cap_usd, prior_reservation_usd, prior_evidence_sha256 FROM ledger_meta WHERE id=1").fetchone()
            expected = (self.project_id, _money_text(self.cap), _money_text(self.prior), self.evidence)
            if row is None:
                if not self._new_file:
                    raise LedgerCorruption("existing ledger has no fixed metadata")
                con.execute("INSERT INTO ledger_meta VALUES (1,?,?,?,?)", expected)
            elif tuple(row) != expected:
                raise LedgerCorruption("fixed ledger metadata drift")
            self._check_rows(con)
            con.commit()
        except LedgerCorruption:
            con.rollback()
            raise
        except sqlite3.Error as exc:
            con.rollback()
            raise LedgerCorruption("invalid ledger") from exc
        finally:
            con.close()

    def _check_rows(self, con):
        try:
            if con.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise LedgerCorruption("sqlite integrity check failed")
            for key, amount, metadata, status, result in con.execute("SELECT key, amount_usd, metadata_json, status, result_json FROM reservations"):
                d = _money(Decimal(amount), allow_zero=False)
                if not key or _json(json.loads(metadata)) != metadata or status not in ("reserved", "terminal"):
                    raise LedgerCorruption("invalid reservation row")
                if status == "reserved" and result is not None:
                    raise LedgerCorruption("reserved row has result")
                if status == "terminal" and not isinstance(json.loads(result or "null"), dict):
                    raise LedgerCorruption("terminal row has invalid result")
                if d <= 0:
                    raise LedgerCorruption("invalid reservation amount")
        except (sqlite3.Error, ValueError, json.JSONDecodeError, InvalidOperation) as exc:
            if isinstance(exc, LedgerCorruption):
                raise
            raise LedgerCorruption("invalid reservation data") from exc

    def _open_checked(self):
        con = self._connect()
        try:
            row = con.execute("SELECT project_id, cap_usd, prior_reservation_usd, prior_evidence_sha256 FROM ledger_meta WHERE id=1").fetchone()
            if row != (self.project_id, _money_text(self.cap), _money_text(self.prior), self.evidence):
                raise LedgerCorruption("fixed ledger metadata drift")
            return con
        except Exception:
            con.close()
            raise

    def reserve(self, key, amount_usd, metadata) -> bool:
        key = str(key)
        if not key:
            raise ValueError("key is required")
        amount = _money(amount_usd, allow_zero=False)
        metadata_json = _json(metadata)
        con = self._open_checked()
        try:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT amount_usd, metadata_json FROM reservations WHERE key=?", (key,)).fetchone()
            if row is not None:
                try:
                    _money(Decimal(row[0]), allow_zero=False)
                    if _json(json.loads(row[1])) != row[1]:
                        raise LedgerCorruption("invalid reservation metadata")
                except (InvalidOperation, ValueError, json.JSONDecodeError) as exc:
                    if isinstance(exc, LedgerCorruption):
                        raise
                    raise LedgerCorruption("invalid reservation row") from exc
                if row != (_money_text(amount), metadata_json):
                    raise ValueError("reservation key metadata or amount mismatch")
                con.commit()
                return False
            try:
                amounts = [Decimal(r[0]) for r in con.execute("SELECT amount_usd FROM reservations")]
                if any(not x.is_finite() or x <= 0 for x in amounts):
                    raise LedgerCorruption("invalid reservation amount")
                used = self.prior + sum(amounts, Decimal(0))
            except (InvalidOperation, ValueError) as exc:
                if isinstance(exc, LedgerCorruption):
                    raise
                raise LedgerCorruption("invalid reservation amount") from exc
            if used + amount > self.cap:
                raise BudgetExceeded("project budget exceeded")
            con.execute("INSERT INTO reservations(key,amount_usd,metadata_json,status,result_json) VALUES(?,?,?,?,NULL)", (key, _money_text(amount), metadata_json, "reserved"))
            con.commit()
            return True
        except (BudgetExceeded, ValueError, LedgerCorruption):
            con.rollback()
            raise
        except sqlite3.Error as exc:
            con.rollback()
            raise LedgerCorruption("reservation failed") from exc
        finally:
            con.close()

    def finish(self, key, result) -> None:
        key = str(key)
        result_json = _json(result)
        if not isinstance(result, dict) or not isinstance(result.get("status"), str):
            raise ValueError("result must be a JSON object containing status")
        con = self._open_checked()
        try:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT status, result_json FROM reservations WHERE key=?", (key,)).fetchone()
            if row is None:
                raise KeyError(key)
            if row[0] not in ("reserved", "terminal"):
                raise LedgerCorruption("invalid reservation status")
            if row[0] == "terminal":
                try:
                    if not isinstance(json.loads(row[1] or "null"), dict):
                        raise LedgerCorruption("invalid terminal result")
                except json.JSONDecodeError as exc:
                    raise LedgerCorruption("invalid terminal result") from exc
                if row[1] != result_json:
                    raise ValueError("terminal result mismatch")
            else:
                con.execute("UPDATE reservations SET status='terminal', result_json=? WHERE key=?", (result_json, key))
            con.commit()
        except (KeyError, ValueError, LedgerCorruption):
            con.rollback()
            raise
        except sqlite3.Error as exc:
            con.rollback()
            raise LedgerCorruption("finish failed") from exc
        finally:
            con.close()

    def mark_interrupted(self, key) -> None:
        self.finish(key, {"status": "interrupted_unknown"})

    def get(self, key):
        con = self._open_checked()
        try:
            row = con.execute("SELECT metadata_json, amount_usd, result_json, status FROM reservations WHERE key=?", (str(key),)).fetchone()
            if row is None:
                return None
            try:
                metadata = json.loads(row[0])
                result = json.loads(row[2]) if row[2] else None
                _money(Decimal(row[1]), allow_zero=False)
            except (InvalidOperation, ValueError, json.JSONDecodeError) as exc:
                raise LedgerCorruption("invalid reservation row") from exc
            if row[3] not in ("reserved", "terminal") or (row[3] == "reserved" and result is not None) or (row[3] == "terminal" and not isinstance(result, dict)):
                raise LedgerCorruption("invalid reservation row")
            return {"metadata": metadata, "reserved_usd": row[1], "result": result, "status": row[3]}
        finally:
            con.close()

    def snapshot(self):
        con = self._open_checked()
        try:
            rows = con.execute("SELECT amount_usd, status, key FROM reservations").fetchall()
            try:
                amounts = [Decimal(r[0]) for r in rows]
            except InvalidOperation as exc:
                raise LedgerCorruption("invalid reservation amount") from exc
            if any(not x.is_finite() or x <= 0 or r[1] not in ("reserved", "terminal") or not r[2] for x, r in zip(amounts, rows)):
                raise LedgerCorruption("invalid reservation row")
            counts = {}
            for _, status, _ in rows:
                counts[status] = counts.get(status, 0) + 1
            return {"reserved_usd": _money_text(self.prior + sum(amounts, Decimal(0))), "attempts": len(rows), "completed": sum(1 for r in rows if r[1] == "terminal"), "status_counts": counts, "unresolved_keys": sorted(r[2] for r in rows if r[1] == "reserved")}
        finally:
            con.close()

    def close(self):
        return None
