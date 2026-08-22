#!/usr/bin/env python3
"""
Microsoft Bookings Automation CLI
Automate booking GTA Office Hours for an entire semester.
Driven by .env parameters.
"""
import click
import os
import sys
from pathlib import Path
from datetime import date
from dotenv import load_dotenv

from date_utils import WEEKDAY_NAMES, format_date_for_display, get_weekdays_in_range
from booker import BookingConfig, create_booking_automation


BOOKING_BACKENDS = ("http", "playwright")
REQUIRED_BOOKING_FIELDS = (
    ("BOOKING_URL", "booking", "url"),
    ("BOOKING_SERVICE", "booking", "service"),
    ("BOOKING_STAFF", "booking", "staff"),
    ("BOOKING_TIME_SLOT", "booking", "time_slot"),
    ("USER_NAME", "user", "name"),
    ("USER_EMAIL", "user", "email"),
)


def _positive_float(raw_value, name: str, default: float) -> float:
    if raw_value is None or not str(raw_value).strip():
        return default
    try:
        value = float(raw_value)
    except ValueError as exc:
        raise click.ClickException(f"{name} must be a number") from exc
    if value <= 0:
        raise click.ClickException(f"{name} must be greater than zero")
    return value


def _nonnegative_int(raw_value, name: str, default: int) -> int:
    if raw_value is None or not str(raw_value).strip():
        return default
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise click.ClickException(f"{name} must be an integer") from exc
    if value < 0:
        raise click.ClickException(f"{name} cannot be negative")
    return value


def load_config() -> dict:
    """Load configuration without validating command-specific fields."""
    # Load .env file
    load_dotenv()

    # Parse skip dates (comma separated)
    skip_dates_str = os.getenv("SKIP_DATES", "")
    skip_dates = [d.strip() for d in skip_dates_str.split(",")] if skip_dates_str else []

    return {
        "booking": {
            "url": os.getenv("BOOKING_URL"),
            "service": os.getenv("BOOKING_SERVICE"),
            "staff": os.getenv("BOOKING_STAFF"),
            "time_slot": os.getenv("BOOKING_TIME_SLOT"),
            "weekday": os.getenv("BOOKING_WEEKDAY", "friday"),
            "backend": os.getenv("BOOKING_BACKEND", "http"),
            "http_timeout": os.getenv("BOOKING_HTTP_TIMEOUT"),
            "http_max_retries": os.getenv("BOOKING_HTTP_MAX_RETRIES"),
            "ledger_path": os.getenv(
                "BOOKING_LEDGER_PATH", ".bookings/bookings.sqlite3"
            ),
        },
        "user": {
            "name": os.getenv("USER_NAME"),
            "email": os.getenv("USER_EMAIL"),
            "address": os.getenv("USER_ADDRESS", ""),
            "phone": os.getenv("USER_PHONE", ""),
            "notes": os.getenv("USER_NOTES", ""),
        },
        "semester": {
            "start_date": os.getenv("SEMESTER_START_DATE"),
            "end_date": os.getenv("SEMESTER_END_DATE"),
            "skip_dates": skip_dates,
        }
    }


def create_booking_config(config: dict, backend: str | None = None) -> BookingConfig:
    """Validate write-path settings and create a BookingConfig."""
    missing = [
        env_name
        for env_name, section, field in REQUIRED_BOOKING_FIELDS
        if not str(config[section].get(field) or "").strip()
    ]
    if missing:
        raise click.ClickException(
            f"Missing required environment variables: {', '.join(missing)}"
        )

    selected_backend = str(
        backend or config["booking"].get("backend") or "http"
    ).strip().casefold()
    if selected_backend not in BOOKING_BACKENDS:
        raise click.ClickException(
            f"BOOKING_BACKEND must be one of: {', '.join(BOOKING_BACKENDS)}"
        )

    http_timeout = 20.0
    http_max_retries = 2
    if selected_backend == "http":
        http_timeout = _positive_float(
            config["booking"].get("http_timeout"),
            "BOOKING_HTTP_TIMEOUT",
            20.0,
        )
        http_max_retries = _nonnegative_int(
            config["booking"].get("http_max_retries"),
            "BOOKING_HTTP_MAX_RETRIES",
            2,
        )

    return BookingConfig(
        url=config["booking"]["url"],
        service=config["booking"]["service"],
        staff=config["booking"]["staff"],
        time_slot=config["booking"]["time_slot"],
        name=config["user"]["name"],
        email=config["user"]["email"],
        address=config["user"]["address"],
        phone=config["user"]["phone"],
        notes=config["user"]["notes"],
        backend=selected_backend,
        http_timeout=http_timeout,
        http_max_retries=http_max_retries,
    )


def require_booking_url(config: dict) -> str:
    """Return the booking URL required by browser utility commands."""
    url = config["booking"].get("url")
    if not str(url or "").strip():
        raise click.ClickException(
            "Missing required environment variable: BOOKING_URL"
        )
    return url


def resolve_booking_weekday(
    config: dict, weekday: str | None = None
) -> tuple[str, int]:
    """Resolve a CLI or environment weekday to its Python weekday index."""
    selected_weekday = str(
        weekday or config["booking"].get("weekday") or "friday"
    ).strip().casefold()
    if selected_weekday not in WEEKDAY_NAMES:
        raise click.ClickException(
            f"BOOKING_WEEKDAY must be one of: {', '.join(WEEKDAY_NAMES)}"
        )
    return selected_weekday, WEEKDAY_NAMES.index(selected_weekday)


def persist_booking_result(config: dict, run_id: str, result: dict) -> None:
    """Persist one result without changing its booking outcome."""
    from booking_ledger import BookingLedgerError, record_booking_result

    try:
        record_booking_result(
            config["booking"]["ledger_path"],
            run_id,
            result,
        )
    except BookingLedgerError as exc:
        click.echo(f"⚠️  Could not save the appointment result locally: {exc}")


@click.group()
@click.pass_context
def cli(ctx):
    """Microsoft Bookings Automation for GTA Office Hours."""
    ctx.ensure_object(dict)
    try:
        ctx.obj["config"] = load_config()
    except Exception as e:
        click.echo(f"Error loading configuration: {e}")
        sys.exit(1)


def execute_booking_run(
    config,
    booking_dates,
    dry_run,
    headed,
    workers,
    backend=None,
    weekday="friday",
    run_command="book-all",
):
    """Helper to execute the booking loop using a list of dates."""
    import concurrent.futures
    from booker import run_single_booking

    weekday_label = f"{weekday.capitalize()}s"
    click.echo(f"\n📅 Found {len(booking_dates)} {weekday_label} to book:")
    time_slot = config["booking"].get("time_slot")
    service = config["booking"].get("service")
    if time_slot:
        service_label = f" ({service})" if service else ""
        click.echo(f"🕓 Start time: {time_slot}{service_label}")
    click.echo("-" * 40)
    for i, booking_date in enumerate(booking_dates, 1):
        click.echo(f"  {i:2}. {format_date_for_display(booking_date)}")
    click.echo("-" * 40)
    
    if dry_run:
        click.echo("\n🔍 DRY RUN - No bookings will be made.")
        return
    
    booking_config = create_booking_config(config, backend)
    selected_backend = booking_config.backend

    # Confirm with user
    if not click.confirm(
        f"\nProceed with booking {len(booking_dates)} dates through the "
        f"{selected_backend} backend using {workers} parallel workers?"
    ):
        click.echo("Cancelled.")
        return

    from booking_ledger import BookingLedgerError, begin_run, finish_run

    ledger_path = config["booking"]["ledger_path"]
    try:
        run_id = begin_run(
            ledger_path,
            booking_config,
            command=run_command,
            booking_dates=booking_dates,
            weekday=weekday,
        )
    except BookingLedgerError as exc:
        raise click.ClickException(
            f"Cannot start booking without a local cancellation record: {exc}"
        ) from exc
    click.echo(f"\n🗃️  Local run ID: {run_id}")

    if headed and selected_backend == "http":
        click.echo("\nℹ️  --headed applies only to the Playwright backend.")
    click.echo(
        f"\n🚀 Starting {selected_backend} booking with {workers} parallel workers...\n"
    )

    results = []
    
    # HTTP work is I/O-bound; Playwright remains isolated in child processes.
    executor_type = (
        concurrent.futures.ThreadPoolExecutor
        if selected_backend == "http"
        else concurrent.futures.ProcessPoolExecutor
    )
    with executor_type(max_workers=workers) as executor:
        # Submit all tasks
        future_to_date = {
            executor.submit(
                run_single_booking, booking_config, booking_date, not headed
            ): booking_date
            for booking_date in booking_dates
        }
        
        # Process results as they complete
        for i, future in enumerate(concurrent.futures.as_completed(future_to_date), 1):
            booking_date = future_to_date[future]
            try:
                result = future.result()
                results.append(result)
                persist_booking_result(config, run_id, result)
                
                status_icon = "✅" if result["success"] else "❌"
                click.echo(f"[{i}/{len(booking_dates)}] {status_icon} {format_date_for_display(booking_date)}: {result['message']}")
                
            except Exception as exc:
                click.echo(f"[{i}/{len(booking_dates)}] 💥 {format_date_for_display(booking_date)} generated an exception: {exc}")
                result = {
                    "success": False,
                    "message": str(exc),
                    "date": booking_date.isoformat(),
                    "backend": selected_backend,
                }
                results.append(result)
                persist_booking_result(config, run_id, result)

    try:
        finish_run(ledger_path, run_id)
    except BookingLedgerError as exc:
        click.echo(f"⚠️  Could not finalize local run {run_id}: {exc}")

    # Summary
    successful = sum(1 for r in results if r["success"])
    click.echo(f"\n{'='*40}")
    click.echo(f"📊 Summary: {successful}/{len(booking_dates)} bookings successful")
    
    if successful < len(booking_dates):
        click.echo("\n❌ Failed bookings:")
        for r in results:
            if not r["success"]:
                click.echo(f"  - {r['date']}: {r['message']}")

@cli.command()
@click.option("--dry-run", is_flag=True, help="Show dates without booking")
@click.option("--headed", is_flag=True, help="Run browser in headed mode (visible)")
@click.option(
    "--workers",
    "-w",
    default=4,
    type=click.IntRange(min=1, max=16),
    show_default=True,
    help="Number of parallel workers",
)
@click.option(
    "--backend",
    type=click.Choice(BOOKING_BACKENDS, case_sensitive=False),
    default=None,
    help="Override BOOKING_BACKEND",
)
@click.option(
    "--weekday",
    type=click.Choice(WEEKDAY_NAMES, case_sensitive=False),
    default=None,
    help="Override BOOKING_WEEKDAY",
)
@click.pass_context
def book_all(ctx, dry_run, headed, workers, backend, weekday):
    """Book the configured weekday throughout the semester range."""
    config = ctx.obj["config"]

    start_date = config["semester"]["start_date"]
    end_date = config["semester"]["end_date"]
    skip_dates = config["semester"]["skip_dates"]

    if not start_date or not end_date:
        raise click.ClickException("SEMESTER_START_DATE and SEMESTER_END_DATE are required in .env for book-all")

    weekday_name, weekday_index = resolve_booking_weekday(config, weekday)
    booking_dates = get_weekdays_in_range(
        start_date, end_date, weekday_index, skip_dates
    )
    execute_booking_run(
        config,
        booking_dates,
        dry_run,
        headed,
        workers,
        backend,
        weekday=weekday_name,
        run_command="book-all",
    )

@cli.command()
@click.argument("semester", type=click.Choice(["fall", "spring", "summer"], case_sensitive=False))
@click.argument("year", type=int)
@click.option("--dry-run", is_flag=True, help="Show dates without booking")
@click.option("--headed", is_flag=True, help="Run browser in headed mode (visible)")
@click.option(
    "--workers",
    "-w",
    default=4,
    type=click.IntRange(min=1, max=16),
    show_default=True,
    help="Number of parallel workers",
)
@click.option(
    "--backend",
    type=click.Choice(BOOKING_BACKENDS, case_sensitive=False),
    default=None,
    help="Override BOOKING_BACKEND",
)
@click.option(
    "--weekday",
    type=click.Choice(WEEKDAY_NAMES, case_sensitive=False),
    default=None,
    help="Override BOOKING_WEEKDAY",
)
@click.pass_context
def book_semester(ctx, semester, year, dry_run, headed, workers, backend, weekday):
    """Fetch semester dates and book the configured weekday."""
    from calendar_parser import fetch_and_parse_calendar

    config = ctx.obj["config"]

    url = f"https://registrar.gmu.edu/calendars/{semester.lower()}_{year}/"
    click.echo(f"\n🌐 Fetching academic calendar from: {url}")

    try:
        cal_data = fetch_and_parse_calendar(url)
    except Exception as e:
        raise click.ClickException(f"Failed to fetch or parse calendar: {e}")

    if not cal_data:
        raise click.ClickException(f"Could not parse dates from the calendar at {url}")

    start_date = cal_data['start_date']
    end_date = cal_data['end_date']
    # Merge live and configured closures without printing or processing duplicates.
    env_skip_dates = config["semester"].get("skip_dates", [])
    skip_dates = sorted(set(cal_data['skip_dates']) | set(env_skip_dates))

    # Update config for downstream functions
    config["semester"]["start_date"] = start_date
    config["semester"]["end_date"] = end_date
    config["semester"]["skip_dates"] = skip_dates

    click.echo(f"🗓️  Parsed Semester Range: {start_date} to {end_date}")
    if skip_dates:
        click.echo(f"⏭️  Skipping Dates: {', '.join(skip_dates)}")

    weekday_name, weekday_index = resolve_booking_weekday(config, weekday)
    booking_dates = get_weekdays_in_range(
        start_date, end_date, weekday_index, skip_dates
    )

    execute_booking_run(
        config,
        booking_dates,
        dry_run,
        headed,
        workers,
        backend,
        weekday=weekday_name,
        run_command=f"book-semester {semester.lower()} {year}",
    )


@cli.command()
@click.argument("date_str")
@click.option("--headed", is_flag=True, help="Run browser in headed mode (visible)")
@click.option(
    "--backend",
    type=click.Choice(BOOKING_BACKENDS, case_sensitive=False),
    default=None,
    help="Override BOOKING_BACKEND",
)
@click.pass_context
def book_single(ctx, date_str, headed, backend):
    """Book a single specific date (format: YYYY-MM-DD)."""
    config = ctx.obj["config"]
    
    # Parse the date
    try:
        from dateutil.parser import parse
        target_date = parse(date_str).date()
    except ValueError:
        raise click.ClickException(f"Invalid date format: {date_str}. Use YYYY-MM-DD")

    booking_config = create_booking_config(config, backend)

    weekday_name, weekday_index = resolve_booking_weekday(config)
    if target_date.weekday() != weekday_index:
        click.echo(
            f"⚠️  Warning: {format_date_for_display(target_date)} is not a "
            f"{weekday_name.capitalize()}!"
        )
        if not click.confirm("Continue anyway?"):
            return
    
    click.echo(f"\n📅 Booking: {format_date_for_display(target_date)}")

    from booking_ledger import BookingLedgerError, begin_run, finish_run

    ledger_path = config["booking"]["ledger_path"]
    try:
        run_id = begin_run(
            ledger_path,
            booking_config,
            command="book-single",
            booking_dates=[target_date],
            weekday=weekday_name,
        )
    except BookingLedgerError as exc:
        raise click.ClickException(
            f"Cannot book without a local cancellation record: {exc}"
        ) from exc
    click.echo(f"🗃️  Local run ID: {run_id}")

    if headed and booking_config.backend == "http":
        click.echo("ℹ️  --headed applies only to the Playwright backend.")
    automation = create_booking_automation(booking_config, headless=not headed)
    
    result = automation.book_date(booking_config, target_date)
    persist_booking_result(config, run_id, result)
    try:
        finish_run(ledger_path, run_id)
    except BookingLedgerError as exc:
        click.echo(f"⚠️  Could not finalize local run {run_id}: {exc}")

    if result["success"]:
        click.echo(f"✅ {result['message']}")
    else:
        click.echo(f"❌ {result['message']}")


@cli.command()
@click.option("--dry-run", is_flag=True, help="Show cancellations without writing")
@click.option(
    "--from-date",
    type=click.DateTime(formats=["%Y-%m-%d"]),
    help="Cancel appointments on or after YYYY-MM-DD",
)
@click.option(
    "--to-date",
    type=click.DateTime(formats=["%Y-%m-%d"]),
    help="Cancel appointments on or before YYYY-MM-DD",
)
@click.option(
    "--run-id",
    help="Cancel only appointments created by one local run",
)
@click.option(
    "--ledger-path",
    type=click.Path(path_type=Path, dir_okay=False),
    default=None,
    help="Override BOOKING_LEDGER_PATH",
)
@click.pass_context
def cancel_all(ctx, dry_run, from_date, to_date, run_id, ledger_path):
    """Cancel active appointments recorded in the private local ledger."""
    from booking_ledger import (
        BookingLedgerError,
        active_bookings,
        record_cancellation_result,
    )
    from bookings_http import HttpBookingAutomation

    config = ctx.obj["config"]
    selected_path = ledger_path or Path(config["booking"]["ledger_path"])
    from_iso = from_date.date().isoformat() if from_date else None
    to_iso = to_date.date().isoformat() if to_date else None
    if from_iso and to_iso and from_iso > to_iso:
        raise click.ClickException("--from-date cannot be after --to-date")

    try:
        records = active_bookings(
            selected_path,
            run_id=run_id,
            from_date=from_iso,
            to_date=to_iso,
        )
    except BookingLedgerError as exc:
        raise click.ClickException(str(exc)) from exc

    if not records:
        click.echo(f"No active appointments found in {selected_path}.")
        return

    click.echo(f"\n🧾 Found {len(records)} active appointments in the ledger:")
    click.echo("-" * 56)
    for index, record in enumerate(records, 1):
        automatic = bool(
            record.get("booking_url")
            and record.get("self_service_appointment_id")
        )
        mode = "automatic" if automatic else "manual cancellation required"
        click.echo(
            f"  {index:2}. {record.get('booking_date', 'unknown date')} at "
            f"{record.get('time_slot', 'unknown time')} [{mode}]"
        )
    click.echo("-" * 56)

    if dry_run:
        click.echo("\n🔍 DRY RUN - No cancellations will be made.")
        return

    cancellable = [
        record
        for record in records
        if record.get("booking_url")
        and record.get("self_service_appointment_id")
    ]
    unsupported_count = len(records) - len(cancellable)
    if unsupported_count:
        click.echo(
            f"⚠️  {unsupported_count} appointments lack a customer "
            "self-service ID and cannot be cancelled automatically."
        )
    if not cancellable:
        raise click.ClickException("No appointments can be cancelled automatically")

    if not click.confirm(
        f"\nPermanently cancel {len(cancellable)} appointments?"
    ):
        click.echo("Cancelled.")
        return

    timeout = _positive_float(
        config["booking"].get("http_timeout"),
        "BOOKING_HTTP_TIMEOUT",
        20.0,
    )
    max_retries = _nonnegative_int(
        config["booking"].get("http_max_retries"),
        "BOOKING_HTTP_MAX_RETRIES",
        2,
    )
    automation = HttpBookingAutomation(
        timeout=timeout,
        max_retries=max_retries,
    )

    successful = 0
    for index, record in enumerate(cancellable, 1):
        result = automation.cancel_appointment(
            str(record["booking_url"]),
            str(record["self_service_appointment_id"]),
        )
        if result["success"]:
            successful += 1
        try:
            record_cancellation_result(
                selected_path,
                str(record["record_id"]),
                result,
            )
        except BookingLedgerError as exc:
            raise click.ClickException(
                f"The cancellation result for {record.get('booking_date')} "
                f"could not be saved: {exc}"
            ) from exc
        status_icon = "✅" if result["success"] else "❌"
        click.echo(
            f"[{index}/{len(cancellable)}] {status_icon} "
            f"{record.get('booking_date')}: {result['message']}"
        )

    click.echo(
        f"\n📊 Cancellation summary: {successful}/{len(cancellable)} successful"
    )


@cli.command("list-runs")
@click.option(
    "--ledger-path",
    type=click.Path(path_type=Path, dir_okay=False),
    default=None,
    help="Override BOOKING_LEDGER_PATH",
)
@click.pass_context
def list_booking_runs(ctx, ledger_path):
    """List locally recorded booking runs."""
    from booking_ledger import BookingLedgerError, list_runs

    config = ctx.obj["config"]
    selected_path = ledger_path or Path(config["booking"]["ledger_path"])
    try:
        runs = list_runs(selected_path)
    except BookingLedgerError as exc:
        raise click.ClickException(str(exc)) from exc

    if not runs:
        click.echo(f"No booking runs found in {selected_path}.")
        return

    click.echo(f"\n🗃️  Booking runs in {selected_path}:")
    click.echo("-" * 72)
    for run in runs:
        click.echo(
            f"{run['run_id']}  {run['command']}  "
            f"{run['successful_count']}/{run['requested_count']} booked"
        )
        click.echo(
            f"    {run['weekday'] or 'single date'} at {run['time_slot']} · "
            f"{run['created_at']}"
        )
    click.echo("-" * 72)


@cli.command()
@click.option(
    "--weekday",
    type=click.Choice(WEEKDAY_NAMES, case_sensitive=False),
    default=None,
    help="Override BOOKING_WEEKDAY",
)
@click.pass_context
def list_dates(ctx, weekday):
    """List all recurring dates that would be booked."""
    config = ctx.obj["config"]
    
    start_date = config["semester"]["start_date"]
    end_date = config["semester"]["end_date"]
    skip_dates = config["semester"]["skip_dates"]

    if not start_date or not end_date:
        raise click.ClickException(
            "SEMESTER_START_DATE and SEMESTER_END_DATE are required in .env "
            "for list-dates"
        )
    
    weekday_name, weekday_index = resolve_booking_weekday(config, weekday)
    booking_dates = get_weekdays_in_range(
        start_date, end_date, weekday_index, skip_dates
    )
    weekday_label = f"{weekday_name.capitalize()}s"

    click.echo(f"\n📅 {weekday_label} in semester ({start_date} to {end_date}):")
    click.echo(f"   Skipping: {', '.join(skip_dates) if skip_dates else 'None'}")
    click.echo("-" * 40)
    
    for i, booking_date in enumerate(booking_dates, 1):
        click.echo(
            f"  {i:2}. {format_date_for_display(booking_date)} "
            f"({booking_date.isoformat()})"
        )
    
    click.echo("-" * 40)
    click.echo(f"Total: {len(booking_dates)} {weekday_label}")


@cli.command()
@click.pass_context
def record(ctx):
    """
    Launch Playwright codegen to record your actions.
    
    Perform the booking manually - selectors will be captured!
    Copy the generated code to update booker.py
    """
    import subprocess
    config = ctx.obj["config"]
    url = require_booking_url(config)
    
    click.echo("\n🎬 Launching Playwright Codegen...")
    click.echo("   Perform the booking manually and copy the selectors!")
    click.echo("   Close the browser when done.\n")
    
    subprocess.run(["uv", "run", "playwright", "codegen", url])


@cli.command()
@click.pass_context
def inspect(ctx):
    """
    Open the booking page for manual inspection.
    
    Use browser dev tools to find selectors.
    """
    from playwright.sync_api import sync_playwright
    config = ctx.obj["config"]
    url = require_booking_url(config)
    
    click.echo("\n🔍 Opening booking page for inspection...")
    click.echo("   Use browser DevTools (F12) to inspect elements.")
    click.echo("   Press Ctrl+C when done.\n")
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_page()
        page.goto(url)
        page.wait_for_load_state("networkidle")
        
        click.echo("   Browser is open. Press Enter to close...")
        input()
        browser.close()


if __name__ == "__main__":
    cli()
