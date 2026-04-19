import os
import pytest
from click.testing import CliRunner
from main import cli

@pytest.fixture
def runner():
    return CliRunner()

@pytest.fixture
def mock_env(monkeypatch):
    monkeypatch.setenv("BOOKING_URL", "http://test.com")
    monkeypatch.setenv("BOOKING_SERVICE", "Service")
    monkeypatch.setenv("BOOKING_STAFF", "Staff")
    monkeypatch.setenv("BOOKING_TIME_SLOT", "12:00 PM")
    monkeypatch.setenv("USER_NAME", "Jane Doe")
    monkeypatch.setenv("USER_EMAIL", "jane@doe.com")

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

def test_book_semester_success(runner, mock_env, mocker):
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
