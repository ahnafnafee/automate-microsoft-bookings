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

from date_utils import get_fridays_in_range, format_date_for_display
from booker import BookingAutomation, BookingConfig


def load_config() -> dict:
    """Load configuration from environment variables."""
    # Load .env file
    load_dotenv()
    
    # Check for required variables. Note: SEMESTER_START_DATE and SEMESTER_END_DATE
    # are optional now because they can be dynamically fetched via book-semester.
    required_vars = [
        "BOOKING_URL", "BOOKING_SERVICE", "BOOKING_STAFF", 
        "BOOKING_TIME_SLOT", "USER_NAME", "USER_EMAIL"
    ]
    
    missing = [var for var in required_vars if not os.getenv(var)]
    if missing:
        raise click.ClickException(f"Missing required environment variables: {', '.join(missing)}")

    # Parse skip dates (comma separated)
    skip_dates_str = os.getenv("SKIP_DATES", "")
    skip_dates = [d.strip() for d in skip_dates_str.split(",")] if skip_dates_str else []

    return {
        "booking": {
            "url": os.getenv("BOOKING_URL"),
            "service": os.getenv("BOOKING_SERVICE"),
            "staff": os.getenv("BOOKING_STAFF"),
            "time_slot": os.getenv("BOOKING_TIME_SLOT"),
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


def create_booking_config(config: dict) -> BookingConfig:
    """Create a BookingConfig from the config dict."""
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
    )


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


def execute_booking_run(config, fridays, dry_run, headed, workers):
    """Helper to execute the booking loop using a list of dates."""
    import concurrent.futures
    from booker import run_single_booking
    
    click.echo(f"\n📅 Found {len(fridays)} Fridays to book:")
    click.echo("-" * 40)
    for i, friday in enumerate(fridays, 1):
        click.echo(f"  {i:2}. {format_date_for_display(friday)}")
    click.echo("-" * 40)
    
    if dry_run:
        click.echo("\n🔍 DRY RUN - No bookings will be made.")
        return
    
    # Confirm with user
    if not click.confirm(f"\nProceed with booking {len(fridays)} dates using {workers} parallel workers?"):
        click.echo("Cancelled.")
        return
    
    click.echo(f"\n🚀 Starting parallel booking with {workers} workers...\n")
    
    # Create booking config object once
    booking_config = create_booking_config(config)
    
    results = []
    
    # Use ProcessPoolExecutor for parallel execution
    with concurrent.futures.ProcessPoolExecutor(max_workers=workers) as executor:
        # Submit all tasks
        future_to_date = {
            executor.submit(run_single_booking, booking_config, friday, not headed): friday 
            for friday in fridays
        }
        
        # Process results as they complete
        for i, future in enumerate(concurrent.futures.as_completed(future_to_date), 1):
            friday = future_to_date[future]
            try:
                result = future.result()
                results.append(result)
                
                status_icon = "✅" if result["success"] else "❌"
                click.echo(f"[{i}/{len(fridays)}] {status_icon} {format_date_for_display(friday)}: {result['message']}")
                
            except Exception as exc:
                click.echo(f"[{i}/{len(fridays)}] 💥 {format_date_for_display(friday)} generated an exception: {exc}")
                results.append({"success": False, "message": str(exc), "date": friday.isoformat()})

    # Summary
    successful = sum(1 for r in results if r["success"])
    click.echo(f"\n{'='*40}")
    click.echo(f"📊 Summary: {successful}/{len(fridays)} bookings successful")
    
    if successful < len(fridays):
        click.echo("\n❌ Failed bookings:")
        for r in results:
            if not r["success"]:
                click.echo(f"  - {r['date']}: {r['message']}")

@cli.command()
@click.option("--dry-run", is_flag=True, help="Show dates without booking")
@click.option("--headed", is_flag=True, help="Run browser in headed mode (visible)")
@click.option("--workers", "-w", default=4, help="Number of parallel workers (default: 4)")
@click.pass_context
def book_all(ctx, dry_run, headed, workers):
    """Book all Fridays in the configured semester range."""
    config = ctx.obj["config"]

    # Get all Friday dates
    start_date = config["semester"]["start_date"]
    end_date = config["semester"]["end_date"]
    skip_dates = config["semester"]["skip_dates"]

    if not start_date or not end_date:
        raise click.ClickException("SEMESTER_START_DATE and SEMESTER_END_DATE are required in .env for book-all")

    fridays = get_fridays_in_range(start_date, end_date, skip_dates)
    execute_booking_run(config, fridays, dry_run, headed, workers)

@cli.command()
@click.argument("semester", type=click.Choice(["fall", "spring", "summer"], case_sensitive=False))
@click.argument("year", type=int)
@click.option("--dry-run", is_flag=True, help="Show dates without booking")
@click.option("--headed", is_flag=True, help="Run browser in headed mode (visible)")
@click.option("--workers", "-w", default=4, help="Number of parallel workers (default: 4)")
@click.pass_context
def book_semester(ctx, semester, year, dry_run, headed, workers):
    """Automatically fetch semester dates and book all Fridays."""
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
    skip_dates = cal_data['skip_dates']

    # Merge skip dates from env
    env_skip_dates = config["semester"].get("skip_dates", [])
    if env_skip_dates:
        skip_dates.extend(env_skip_dates)

    # Update config for downstream functions
    config["semester"]["start_date"] = start_date
    config["semester"]["end_date"] = end_date
    config["semester"]["skip_dates"] = skip_dates

    click.echo(f"🗓️  Parsed Semester Range: {start_date} to {end_date}")
    if skip_dates:
        click.echo(f"⏭️  Skipping Dates: {', '.join(skip_dates)}")

    fridays = get_fridays_in_range(start_date, end_date, skip_dates)

    execute_booking_run(config, fridays, dry_run, headed, workers)


@cli.command()
@click.argument("date_str")
@click.option("--headed", is_flag=True, help="Run browser in headed mode (visible)")
@click.pass_context
def book_single(ctx, date_str, headed):
    """Book a single specific date (format: YYYY-MM-DD)."""
    config = ctx.obj["config"]
    
    # Parse the date
    try:
        from dateutil.parser import parse
        target_date = parse(date_str).date()
    except ValueError:
        raise click.ClickException(f"Invalid date format: {date_str}. Use YYYY-MM-DD")
    
    # Validate it's a Friday
    if target_date.weekday() != 4:
        click.echo(f"⚠️  Warning: {format_date_for_display(target_date)} is not a Friday!")
        if not click.confirm("Continue anyway?"):
            return
    
    click.echo(f"\n📅 Booking: {format_date_for_display(target_date)}")
    
    # Create automation instance
    booking_config = create_booking_config(config)
    automation = BookingAutomation(headless=not headed)
    
    result = automation.book_date(booking_config, target_date)
    
    if result["success"]:
        click.echo(f"✅ {result['message']}")
    else:
        click.echo(f"❌ {result['message']}")


@cli.command()
@click.pass_context
def list_dates(ctx):
    """List all Fridays that would be booked."""
    config = ctx.obj["config"]
    
    start_date = config["semester"]["start_date"]
    end_date = config["semester"]["end_date"]
    skip_dates = config["semester"]["skip_dates"]
    
    fridays = get_fridays_in_range(start_date, end_date, skip_dates)
    
    click.echo(f"\n📅 Fridays in semester ({start_date} to {end_date}):")
    click.echo(f"   Skipping: {', '.join(skip_dates) if skip_dates else 'None'}")
    click.echo("-" * 40)
    
    for i, friday in enumerate(fridays, 1):
        click.echo(f"  {i:2}. {format_date_for_display(friday)} ({friday.isoformat()})")
    
    click.echo("-" * 40)
    click.echo(f"Total: {len(fridays)} Fridays")


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
    url = config["booking"]["url"]
    
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
    url = config["booking"]["url"]
    
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
