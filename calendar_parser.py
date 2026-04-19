import re
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Any
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

def get_year_from_title(soup: BeautifulSoup) -> str:
    """Extracts the year from the page title, defaults to current year."""
    title = soup.title.string if soup.title else ""
    match = re.search(r'\b(20\d{2})\b', title)
    return match.group(1) if match else str(datetime.now().year)

def parse_date_str(date_str: str, year: str) -> Optional[datetime.date]:
    """Parses a date string from the table into a python date object."""
    # Remove day of week prefixes like 'Mon. '
    date_str = re.sub(r'^[A-Za-z]+\.\s*', '', date_str)
    date_str = date_str.strip()

    # Handle abbreviation differences like 'Sept' instead of 'Sep'
    date_str = date_str.replace('Sept', 'Sep')

    # Try different formats
    for fmt in ("%b %d %Y", "%B %d %Y"):
        try:
            return datetime.strptime(f"{date_str} {year}", fmt).date()
        except ValueError:
            continue
    return None

def parse_date_range(range_str: str, year: str) -> List[datetime.date]:
    """Parses a date range string like 'Wed. Nov 27 - Sun. Dec 1' into a list of dates."""
    # Standardize dashes
    range_str = range_str.replace('–', '-').replace('—', '-')
    parts = [p.strip() for p in range_str.split('-')]

    if len(parts) == 2:
        start = parse_date_str(parts[0], year)
        end = parse_date_str(parts[1], year)

        if start and end:
            # If end date appears to be before start date, assume it crosses into the next year
            if end < start:
                end = end.replace(year=end.year + 1)

            dates = []
            curr = start
            while curr <= end:
                dates.append(curr)
                curr += timedelta(days=1)
            return dates
    return []

def fetch_and_parse_calendar(url: str) -> Optional[Dict[str, Any]]:
    """
    Fetches the GMU academic calendar from the URL using Playwright,
    parses it with BeautifulSoup, and returns start_date, end_date, and skip_dates.
    """
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(url)
        page.wait_for_load_state("networkidle")
        html_content = page.content()
        browser.close()

    soup = BeautifulSoup(html_content, 'html.parser')
    year = get_year_from_title(soup)

    tables = soup.find_all('table')
    if len(tables) < 2:
        return None

    start_date = None
    end_date = None
    skip_dates = set()

    # The full semester dates are typically in the second table and second column
    for tr in tables[1].find_all('tr'):
        cells = [td.get_text(strip=True) for td in tr.find_all(['td', 'th'])]
        if len(cells) < 2:
            continue

        desc = cells[0].lower()
        date_col = cells[1] # Full Semester column

        if date_col == 'N/A' or not date_col:
            continue

        if 'first day' in desc and 'classes' in desc:
            start_date = parse_date_str(date_col, year)

        elif 'last day of class' in desc or 'classes end' in desc:
            end_date = parse_date_str(date_col, year)

        elif any(k in desc for k in ['break', 'recess', 'reading day', 'holiday', 'election day', 'university closed']):
            if ' - ' in date_col.replace('–', ' - ').replace('—', ' - '):
                dates = parse_date_range(date_col, year)
                skip_dates.update(dates)
            else:
                d = parse_date_str(date_col, year)
                if d:
                    skip_dates.add(d)

    if not start_date or not end_date:
        return None

    return {
        'start_date': start_date.isoformat(),
        'end_date': end_date.isoformat(),
        'skip_dates': [d.isoformat() for d in sorted(skip_dates)]
    }
