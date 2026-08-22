import os
import pytest
from click.testing import CliRunner
from booker import BookingConfig
from booking_ledger import (
    active_bookings,
    begin_run,
    finish_run,
    list_runs,
    record_booking_result,
)
from main import cli


REQUIRED_BOOKING_ENV_VARS = (
    "BOOKING_URL",
    "BOOKING_SERVICE",
    "BOOKING_STAFF",
    "BOOKING_TIME_SLOT",
    "USER_NAME",
    "USER_EMAIL",
)

@pytest.fixture
def runner():
    return CliRunner()

@pytest.fixture
def mock_env(monkeypatch, tmp_path):
    monkeypatch.setenv("BOOKING_URL", "http://test.com")
    monkeypatch.setenv("BOOKING_SERVICE", "Service")
    monkeypatch.setenv("BOOKING_STAFF", "Staff")
    monkeypatch.setenv("BOOKING_TIME_SLOT", "12:00 PM")
    monkeypatch.setenv("BOOKING_WEEKDAY", "friday")
    monkeypatch.setenv(
        "BOOKING_LEDGER_PATH", str(tmp_path / "bookings.sqlite3")
    )
    monkeypatch.setenv("USER_NAME", "Jane Doe")
    monkeypatch.setenv("USER_EMAIL", "jane@doe.com")
    monkeypatch.delenv("BOOKING_BACKEND", raising=False)

def test_book_all_requires_env_dates(runner, mock_env, monkeypatch):
    # book-all requires SEMESTER_START_DATE and SEMESTER_END_DATE to be set
    # The .env file in the root is loaded by load_dotenv(), so we need to override the values
    monkeypatch.setenv("SEMESTER_START_DATE", "")
    monkeypatch.setenv("SEMESTER_END_DATE", "")
    result = runner.invoke(cli, ["book-all", "--dry-run"])
    assert result.exit_code != 0
    assert "SEMESTER_START_DATE and SEMESTER_END_DATE are required" in result.output

def test_book_all_success(runner, mock_env, monkeypatch, mocker):
    monkeypatch.setenv("SEMESTER_START_DATE", "2026-01-23")
    monkeypatch.setenv("SEMESTER_END_DATE", "2026-05-01")
    monkeypatch.setenv("SKIP_DATES", "2026-03-13")

    mock_execute = mocker.patch("main.execute_booking_run")

    result = runner.invoke(cli, ["book-all", "--dry-run"])
    assert result.exit_code == 0
    mock_execute.assert_called_once()
    args = mock_execute.call_args[0]
    fridays = args[1]
    assert len(fridays) == 14 # Total 14 fridays in this range with 1 skip

def test_book_semester_success(runner, mock_env, monkeypatch, mocker):
    monkeypatch.setenv("SKIP_DATES", "2026-03-13")
    mock_fetch = mocker.patch("calendar_parser.fetch_and_parse_calendar")
    mock_fetch.return_value = {
        "start_date": "2026-01-20",
        "end_date": "2026-05-04",
        "skip_dates": ["2026-03-09", "2026-03-10", "2026-03-11", "2026-03-12", "2026-03-13"]
    }
    mock_execute = mocker.patch("main.execute_booking_run")

    result = runner.invoke(cli, ["book-semester", "spring", "2026", "--dry-run"])
    assert result.exit_code == 0
    assert "Fetching academic calendar from: https://registrar.gmu.edu/calendars/spring_2026/" in result.output

    mock_execute.assert_called_once()
    args = mock_execute.call_args[0]
    fridays = args[1]
    # Jan 20 to May 4 is 15 weeks. March 13 is skipped.
    assert len(fridays) == 14
    config = args[0]
    assert config["semester"]["skip_dates"].count("2026-03-13") == 1


def test_book_semester_dry_run_does_not_require_booking_config(
    runner, monkeypatch, mocker
):
    for variable in REQUIRED_BOOKING_ENV_VARS:
        monkeypatch.setenv(variable, "")
    monkeypatch.setenv("BOOKING_BACKEND", "not-a-backend")
    monkeypatch.setenv("BOOKING_HTTP_TIMEOUT", "not-a-number")
    monkeypatch.setenv("BOOKING_HTTP_MAX_RETRIES", "not-an-integer")
    monkeypatch.setenv("SKIP_DATES", "")

    mocker.patch(
        "calendar_parser.fetch_and_parse_calendar",
        return_value={
            "start_date": "2026-08-24",
            "end_date": "2026-12-07",
            "skip_dates": ["2026-11-27"],
        },
    )
    run_single_booking = mocker.patch("booker.run_single_booking")

    result = runner.invoke(
        cli, ["book-semester", "fall", "2026", "--dry-run"]
    )

    assert result.exit_code == 0
    assert "DRY RUN - No bookings will be made." in result.output
    run_single_booking.assert_not_called()


def test_book_semester_uses_configured_thursday(
    runner, mock_env, monkeypatch, mocker
):
    monkeypatch.setenv("BOOKING_WEEKDAY", "thursday")
    monkeypatch.setenv("BOOKING_TIME_SLOT", "4:00 PM")
    monkeypatch.setenv("SKIP_DATES", "")
    mocker.patch(
        "calendar_parser.fetch_and_parse_calendar",
        return_value={
            "start_date": "2026-08-24",
            "end_date": "2026-12-07",
            "skip_dates": ["2026-11-26"],
        },
    )
    mock_execute = mocker.patch("main.execute_booking_run")

    result = runner.invoke(
        cli, ["book-semester", "fall", "2026", "--dry-run"]
    )

    assert result.exit_code == 0
    booking_dates = mock_execute.call_args.args[1]
    assert booking_dates[0].isoformat() == "2026-08-27"
    assert booking_dates[-1].isoformat() == "2026-12-03"
    assert len(booking_dates) == 14
    assert all(booking_date.weekday() == 3 for booking_date in booking_dates)
    assert mock_execute.call_args.kwargs["weekday"] == "thursday"


def test_book_single_still_requires_booking_config(runner, monkeypatch, mocker):
    for variable in REQUIRED_BOOKING_ENV_VARS:
        monkeypatch.setenv(variable, "")
    automation_factory = mocker.patch("main.create_booking_automation")

    result = runner.invoke(cli, ["book-single", "2026-08-21"])

    assert result.exit_code != 0
    assert "Missing required environment variables" in result.output
    for variable in REQUIRED_BOOKING_ENV_VARS:
        assert variable in result.output
    automation_factory.assert_not_called()


def test_http_is_default_backend(runner, mock_env, monkeypatch, mocker):
    automation = mocker.Mock()
    automation.book_date.return_value = {
        "success": True,
        "message": "Booked through HTTP",
        "date": "2026-08-21",
    }
    factory = mocker.patch("main.create_booking_automation", return_value=automation)

    result = runner.invoke(cli, ["book-single", "2026-08-21"])

    assert result.exit_code == 0
    booking_config = factory.call_args.args[0]
    assert booking_config.backend == "http"
    automation.book_date.assert_called_once()


def test_book_single_can_override_playwright_backend(
    runner, mock_env, mocker
):
    automation = mocker.Mock()
    automation.book_date.return_value = {
        "success": True,
        "message": "Booked through Playwright",
        "date": "2026-08-21",
    }
    factory = mocker.patch("main.create_booking_automation", return_value=automation)

    result = runner.invoke(
        cli,
        ["book-single", "2026-08-21", "--backend", "playwright", "--headed"],
    )

    assert result.exit_code == 0
    booking_config = factory.call_args.args[0]
    assert booking_config.backend == "playwright"
    assert factory.call_args.kwargs["headless"] is False


def test_cancel_all_dry_run_never_contacts_microsoft(
    runner, mock_env, monkeypatch, mocker, tmp_path
):
    ledger_path = tmp_path / "cancel-dry-run.sqlite3"
    config = BookingConfig(
        url="https://bookings.example.edu/book/office-hours/",
        service="Office Hours 2 Hours",
        staff="Room 101",
        time_slot="4:00 PM",
        name="Jane Doe",
        email="jane@example.edu",
    )
    run_id = begin_run(
        ledger_path,
        config,
        command="book-semester fall 2026",
        booking_dates=["2026-08-27"],
        weekday="thursday",
    )
    record_booking_result(
        ledger_path,
        run_id,
        {
            "success": True,
            "date": "2026-08-27",
            "backend": "http",
            "self_service_appointment_id": "self-service-123",
        },
    )
    finish_run(ledger_path, run_id)
    monkeypatch.setenv("BOOKING_LEDGER_PATH", str(ledger_path))
    cancel = mocker.patch(
        "bookings_http.HttpBookingAutomation.cancel_appointment"
    )

    result = runner.invoke(cli, ["cancel-all", "--dry-run"])

    assert result.exit_code == 0
    assert "DRY RUN - No cancellations will be made." in result.output
    cancel.assert_not_called()


def test_cancel_all_marks_successful_cancellation(
    runner, mock_env, monkeypatch, mocker, tmp_path
):
    ledger_path = tmp_path / "cancel.sqlite3"
    config = BookingConfig(
        url="https://bookings.example.edu/book/office-hours/",
        service="Office Hours 2 Hours",
        staff="Room 101",
        time_slot="4:00 PM",
        name="Jane Doe",
        email="jane@example.edu",
    )
    run_id = begin_run(
        ledger_path,
        config,
        command="book-semester fall 2026",
        booking_dates=["2026-08-27"],
        weekday="thursday",
    )
    record_booking_result(
        ledger_path,
        run_id,
        {
            "success": True,
            "date": "2026-08-27",
            "backend": "http",
            "self_service_appointment_id": "self-service-123",
        },
    )
    finish_run(ledger_path, run_id)
    monkeypatch.setenv("BOOKING_LEDGER_PATH", str(ledger_path))
    cancel = mocker.patch(
        "bookings_http.HttpBookingAutomation.cancel_appointment",
        return_value={
            "success": True,
            "message": "Successfully cancelled appointment via HTTP",
        },
    )

    result = runner.invoke(cli, ["cancel-all"], input="y\n")

    assert result.exit_code == 0
    cancel.assert_called_once_with(
        "https://bookings.example.edu/book/office-hours/",
        "self-service-123",
    )
    assert active_bookings(ledger_path) == []


def test_list_runs_displays_local_history(
    runner, mock_env, monkeypatch, tmp_path
):
    database_path = tmp_path / "history.sqlite3"
    config = BookingConfig(
        url="https://bookings.example.edu/book/office-hours/",
        service="Office Hours 2 Hours",
        staff="Room 101",
        time_slot="4:00 PM",
        name="Jane Doe",
        email="jane@example.edu",
    )
    run_id = begin_run(
        database_path,
        config,
        command="book-semester fall 2026",
        booking_dates=["2026-08-27"],
        weekday="thursday",
    )
    finish_run(database_path, run_id)
    monkeypatch.setenv("BOOKING_LEDGER_PATH", str(database_path))

    result = runner.invoke(cli, ["list-runs"])

    assert result.exit_code == 0
    assert run_id in result.output
    assert "book-semester fall 2026" in result.output
    assert list_runs(database_path)[0]["run_id"] == run_id
