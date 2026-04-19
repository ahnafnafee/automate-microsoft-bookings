from datetime import date
from bs4 import BeautifulSoup
from calendar_parser import get_year_from_title, parse_date_str, parse_date_range, fetch_and_parse_calendar

def test_get_year_from_title():
    soup1 = BeautifulSoup("<title>Fall 2026 Academic Calendar</title>", "html.parser")
    assert get_year_from_title(soup1) == "2026"

    soup2 = BeautifulSoup("<title>Spring 2027 Schedule</title>", "html.parser")
    assert get_year_from_title(soup2) == "2027"

    # Defaults to current year if no match, we can check it returns a 4-digit string
    soup3 = BeautifulSoup("<title>Academic Calendar</title>", "html.parser")
    assert len(get_year_from_title(soup3)) == 4

def test_parse_date_str():
    assert parse_date_str("Mon. Aug 24", "2026") == date(2026, 8, 24)
    assert parse_date_str("Sept 7", "2026") == date(2026, 9, 7)
    assert parse_date_str("Friday, March 13", "2026") == date(2026, 3, 13)
    assert parse_date_str("Invalid Date", "2026") is None

def test_parse_date_range():
    # Regular range
    dates = parse_date_range("Wed. Nov 25 - Sun. Nov 29", "2026")
    assert len(dates) == 5
    assert dates[0] == date(2026, 11, 25)
    assert dates[-1] == date(2026, 11, 29)

    # Range crossing years
    dates2 = parse_date_range("Dec 30 - Jan 2", "2026")
    assert len(dates2) == 4
    assert dates2[0] == date(2026, 12, 30)
    assert dates2[-1] == date(2027, 1, 2)

def test_fetch_and_parse_calendar(mocker):
    # Mock Playwright to return a static HTML snippet
    mock_html = """
    <html>
      <head><title>Fall 2026 Academic Calendar</title></head>
      <body>
        <table><tr><td>Ignored</td></tr></table>
        <table>
          <tr>
            <td>First Day of Classes</td>
            <td>Mon. Aug 24</td>
          </tr>
          <tr>
            <td>Labor Day (University Closed)</td>
            <td>Mon. Sept 7</td>
          </tr>
          <tr>
            <td>Thanksgiving Recess</td>
            <td>Wed. Nov 25 - Sun. Nov 29</td>
          </tr>
          <tr>
            <td>Last Day of Class</td>
            <td>Mon. Dec 7</td>
          </tr>
        </table>
      </body>
    </html>
    """

    mock_sync_playwright = mocker.patch("calendar_parser.sync_playwright")
    mock_context = mock_sync_playwright.return_value.__enter__.return_value
    mock_browser = mock_context.chromium.launch.return_value
    mock_page = mock_browser.new_page.return_value
    mock_page.content.return_value = mock_html

    result = fetch_and_parse_calendar("http://fake-url.com")

    assert result is not None
    assert result["start_date"] == "2026-08-24"
    assert result["end_date"] == "2026-12-07"
    assert "2026-09-07" in result["skip_dates"]
    assert "2026-11-25" in result["skip_dates"]
    assert "2026-11-29" in result["skip_dates"]
    assert len(result["skip_dates"]) == 6 # Sept 7 + Nov 25, 26, 27, 28, 29
