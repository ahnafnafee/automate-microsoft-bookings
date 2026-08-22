"""SQLite history for booking runs and their appointments."""

from __future__ import annotations

import sqlite3
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping


DEFAULT_LEDGER_PATH = Path(".bookings") / "bookings.sqlite3"
SCHEMA = """
CREATE TABLE IF NOT EXISTS booking_runs (
    run_id TEXT PRIMARY KEY,
    command TEXT NOT NULL,
    created_at TEXT NOT NULL,
    completed_at TEXT,
    booking_url TEXT NOT NULL,
    service TEXT NOT NULL,
    staff TEXT NOT NULL,
    time_slot TEXT NOT NULL,
    weekday TEXT,
    backend TEXT NOT NULL,
    requested_count INTEGER NOT NULL,
    successful_count INTEGER NOT NULL DEFAULT 0,
    failed_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS appointments (
    record_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES booking_runs(run_id),
    booking_date TEXT NOT NULL,
    status TEXT NOT NULL,
    backend TEXT NOT NULL,
    appointment_id TEXT,
    self_service_appointment_id TEXT,
    message TEXT,
    created_at TEXT NOT NULL,
    cancelled_at TEXT
);

CREATE TABLE IF NOT EXISTS cancellation_attempts (
    attempt_id TEXT PRIMARY KEY,
    record_id TEXT NOT NULL REFERENCES appointments(record_id),
    attempted_at TEXT NOT NULL,
    success INTEGER NOT NULL,
    message TEXT
);

CREATE INDEX IF NOT EXISTS idx_appointments_run_id
ON appointments(run_id);

CREATE INDEX IF NOT EXISTS idx_appointments_status_date
ON appointments(status, booking_date);
"""


class BookingLedgerError(RuntimeError):
    """Raised when the local booking database cannot be read or written."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def _database(path: str | Path) -> Iterator[sqlite3.Connection]:
    database_path = Path(path)
    connection: sqlite3.Connection | None = None
    try:
        database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(SCHEMA)
        yield connection
        connection.commit()
    except BookingLedgerError:
        if connection is not None:
            connection.rollback()
        raise
    except (OSError, sqlite3.Error) as exc:
        if connection is not None:
            connection.rollback()
        raise BookingLedgerError(
            f"Could not use booking database {database_path}: {exc}"
        ) from exc
    finally:
        if connection is not None:
            connection.close()


def begin_run(
    path: str | Path,
    config: Any,
    *,
    command: str,
    booking_dates: Iterable[str | date],
    weekday: str | None,
) -> str:
    """Create a booking run before any appointment writes are attempted."""
    run_id = str(uuid.uuid4())
    requested_count = len(list(booking_dates))
    with _database(path) as connection:
        connection.execute(
            """
            INSERT INTO booking_runs (
                run_id, command, created_at, booking_url, service, staff,
                time_slot, weekday, backend, requested_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                command,
                _utc_now(),
                config.url,
                config.service,
                config.staff,
                config.time_slot,
                weekday,
                config.backend,
                requested_count,
            ),
        )
    return run_id


def record_booking_result(
    path: str | Path,
    run_id: str,
    result: Mapping[str, Any],
) -> str:
    """Store one booking success or failure under its run."""
    record_id = str(uuid.uuid4())
    status = "booked" if result.get("success") else "failed"
    with _database(path) as connection:
        connection.execute(
            """
            INSERT INTO appointments (
                record_id, run_id, booking_date, status, backend,
                appointment_id, self_service_appointment_id, message, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record_id,
                run_id,
                str(result.get("date") or ""),
                status,
                str(result.get("backend") or "unknown"),
                result.get("appointment_id"),
                result.get("self_service_appointment_id"),
                result.get("message"),
                _utc_now(),
            ),
        )
        connection.execute(
            """
            UPDATE booking_runs
            SET
                successful_count = (
                    SELECT COUNT(*)
                    FROM appointments
                    WHERE run_id = ? AND status = 'booked'
                ),
                failed_count = (
                    SELECT COUNT(*)
                    FROM appointments
                    WHERE run_id = ? AND status = 'failed'
                )
            WHERE run_id = ?
            """,
            (run_id, run_id, run_id),
        )
    return record_id


def finish_run(path: str | Path, run_id: str) -> None:
    """Finalize aggregate counts for a completed booking run."""
    with _database(path) as connection:
        counts = connection.execute(
            """
            SELECT
                SUM(CASE WHEN status = 'booked' THEN 1 ELSE 0 END) AS successful,
                SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed
            FROM appointments
            WHERE run_id = ?
            """,
            (run_id,),
        ).fetchone()
        connection.execute(
            """
            UPDATE booking_runs
            SET completed_at = ?, successful_count = ?, failed_count = ?
            WHERE run_id = ?
            """,
            (
                _utc_now(),
                int(counts["successful"] or 0),
                int(counts["failed"] or 0),
                run_id,
            ),
        )


def list_runs(path: str | Path) -> list[dict[str, Any]]:
    """Return booking runs newest first."""
    with _database(path) as connection:
        rows = connection.execute(
            """
            SELECT *
            FROM booking_runs
            ORDER BY created_at DESC
            """
        ).fetchall()
    return [dict(row) for row in rows]


def active_bookings(
    path: str | Path,
    *,
    run_id: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
) -> list[dict[str, Any]]:
    """Return active appointments with their parent-run configuration."""
    conditions = ["appointments.status = 'booked'"]
    parameters: list[Any] = []
    if run_id:
        conditions.append("appointments.run_id = ?")
        parameters.append(run_id)
    if from_date:
        conditions.append("appointments.booking_date >= ?")
        parameters.append(from_date)
    if to_date:
        conditions.append("appointments.booking_date <= ?")
        parameters.append(to_date)

    query = f"""
        SELECT
            appointments.*,
            booking_runs.booking_url,
            booking_runs.service,
            booking_runs.staff,
            booking_runs.time_slot,
            booking_runs.weekday
        FROM appointments
        JOIN booking_runs USING (run_id)
        WHERE {' AND '.join(conditions)}
        ORDER BY appointments.booking_date, booking_runs.time_slot
    """
    with _database(path) as connection:
        rows = connection.execute(query, parameters).fetchall()
    return [dict(row) for row in rows]


def record_cancellation_result(
    path: str | Path,
    record_id: str,
    result: Mapping[str, Any],
) -> None:
    """Audit a cancellation attempt and mark confirmed cancellations."""
    succeeded = bool(result.get("success"))
    attempted_at = _utc_now()
    with _database(path) as connection:
        connection.execute(
            """
            INSERT INTO cancellation_attempts (
                attempt_id, record_id, attempted_at, success, message
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                record_id,
                attempted_at,
                int(succeeded),
                result.get("message"),
            ),
        )
        if succeeded:
            cursor = connection.execute(
                """
                UPDATE appointments
                SET status = 'cancelled', cancelled_at = ?
                WHERE record_id = ?
                """,
                (attempted_at, record_id),
            )
            if cursor.rowcount != 1:
                raise BookingLedgerError(
                    f"Booking record {record_id} was not found"
                )
