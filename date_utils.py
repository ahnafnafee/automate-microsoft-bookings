"""Date utilities for generating recurring booking dates."""
from datetime import date, timedelta
from typing import List
from dateutil.parser import parse


WEEKDAY_NAMES = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)


def get_weekdays_in_range(
    start_date: str | date,
    end_date: str | date,
    weekday: int,
    skip_dates: List[str] | None = None,
) -> List[date]:
    """Generate one recurring weekday in an inclusive date range."""
    if weekday not in range(7):
        raise ValueError("weekday must be an integer from 0 (Monday) to 6 (Sunday)")

    if isinstance(start_date, str):
        start_date = parse(start_date).date()
    if isinstance(end_date, str):
        end_date = parse(end_date).date()

    skip_set = set()
    if skip_dates:
        for skipped_date in skip_dates:
            if isinstance(skipped_date, str):
                skip_set.add(parse(skipped_date).date())
            else:
                skip_set.add(skipped_date)

    matching_dates = []
    current = start_date
    days_until_weekday = (weekday - current.weekday()) % 7
    current += timedelta(days=days_until_weekday)

    while current <= end_date:
        if current not in skip_set:
            matching_dates.append(current)
        current += timedelta(days=7)

    return matching_dates


def get_fridays_in_range(
    start_date: str | date, 
    end_date: str | date, 
    skip_dates: List[str] | None = None
) -> List[date]:
    """
    Generate all Fridays between start_date and end_date (inclusive),
    excluding any dates in skip_dates.
    
    Args:
        start_date: Start of date range (YYYY-MM-DD string or date object)
        end_date: End of date range (YYYY-MM-DD string or date object)
        skip_dates: List of dates to skip (YYYY-MM-DD strings)
    
    Returns:
        List of date objects for each Friday in the range
    """
    return get_weekdays_in_range(start_date, end_date, 4, skip_dates)


def format_date_for_display(d: date) -> str:
    """Format a date for human-readable display."""
    return d.strftime("%B %d, %Y")  # e.g., "January 24, 2026"
