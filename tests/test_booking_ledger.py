from datetime import date

from booker import BookingConfig
from booking_ledger import (
    active_bookings,
    begin_run,
    finish_run,
    list_runs,
    record_booking_result,
    record_cancellation_result,
)


def booking_config():
    return BookingConfig(
        url="https://bookings.example.edu/book/office-hours/",
        service="Office Hours 2 Hours",
        staff="Room 101",
        time_slot="4:00 PM",
        name="Jane Doe",
        email="jane@example.edu",
    )


def test_run_and_appointment_are_recorded_and_can_be_cancelled(tmp_path):
    database_path = tmp_path / "bookings.sqlite3"
    config = booking_config()
    run_id = begin_run(
        database_path,
        config,
        command="book-semester fall 2026",
        booking_dates=[date(2026, 8, 27)],
        weekday="thursday",
    )
    record_booking_result(
        database_path,
        run_id,
        {
            "success": True,
            "date": "2026-08-27",
            "backend": "http",
            "appointment_id": "appointment-123",
            "self_service_appointment_id": "self-service-123",
            "message": "Booked",
        },
    )

    # Counts remain useful even if the process exits before finalization.
    in_progress_run = list_runs(database_path)[0]
    assert in_progress_run["successful_count"] == 1
    assert in_progress_run["failed_count"] == 0

    finish_run(database_path, run_id)

    runs = list_runs(database_path)
    assert len(runs) == 1
    assert runs[0]["run_id"] == run_id
    assert runs[0]["successful_count"] == 1
    assert runs[0]["failed_count"] == 0

    active = active_bookings(database_path, run_id=run_id)
    assert len(active) == 1
    assert active[0]["booking_date"] == "2026-08-27"
    assert active[0]["self_service_appointment_id"] == "self-service-123"

    record_cancellation_result(
        database_path,
        active[0]["record_id"],
        {"success": True, "message": "Cancelled"},
    )

    assert active_bookings(database_path) == []


def test_failed_booking_is_kept_in_run_history(tmp_path):
    database_path = tmp_path / "bookings.sqlite3"
    run_id = begin_run(
        database_path,
        booking_config(),
        command="book-single",
        booking_dates=["2026-08-27"],
        weekday="thursday",
    )
    record_booking_result(
        database_path,
        run_id,
        {
            "success": False,
            "date": "2026-08-27",
            "backend": "http",
            "message": "Unavailable",
        },
    )
    finish_run(database_path, run_id)

    runs = list_runs(database_path)
    assert runs[0]["successful_count"] == 0
    assert runs[0]["failed_count"] == 1
    assert active_bookings(database_path) == []


def test_active_bookings_can_be_filtered_by_run_and_date(tmp_path):
    database_path = tmp_path / "bookings.sqlite3"
    config = booking_config()
    run_id = begin_run(
        database_path,
        config,
        command="book-semester fall 2026",
        booking_dates=["2026-08-27", "2026-09-03"],
        weekday="thursday",
    )
    for booking_date, self_service_id in (
        ("2026-08-27", "self-service-1"),
        ("2026-09-03", "self-service-2"),
    ):
        record_booking_result(
            database_path,
            run_id,
            {
                "success": True,
                "date": booking_date,
                "backend": "http",
                "self_service_appointment_id": self_service_id,
            },
        )
    finish_run(database_path, run_id)

    selected = active_bookings(
        database_path,
        run_id=run_id,
        from_date="2026-09-01",
        to_date="2026-09-30",
    )

    assert [record["booking_date"] for record in selected] == ["2026-09-03"]
