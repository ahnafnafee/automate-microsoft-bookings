"""
Date utilities for generating Friday booking dates.
"""
from datetime import date, timedelta
from typing import List
from dateutil.parser import parse


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
    # Parse dates if they're strings
    if isinstance(start_date, str):
        start_date = parse(start_date).date()
    if isinstance(end_date, str):
        end_date = parse(end_date).date()
    
    # Parse skip dates
    skip_set = set()
    if skip_dates:
        for d in skip_dates:
            if isinstance(d, str):
                skip_set.add(parse(d).date())
            else:
                skip_set.add(d)
    
    fridays = []
    current = start_date
    
    # Move to first Friday if start_date isn't a Friday
    days_until_friday = (4 - current.weekday()) % 7
    if days_until_friday > 0:
        current += timedelta(days=days_until_friday)
    
    # Collect all Fridays
    while current <= end_date:
        if current not in skip_set:
            fridays.append(current)
        current += timedelta(days=7)
    
    return fridays


def format_date_for_display(d: date) -> str:
    """Format a date for human-readable display."""
    return d.strftime("%B %d, %Y")  # e.g., "January 24, 2026"
