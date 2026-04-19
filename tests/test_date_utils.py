from datetime import date
from date_utils import get_fridays_in_range, format_date_for_display

def test_get_fridays_in_range():
    # Feb 2026 has fridays on 6, 13, 20, 27
    start = "2026-02-01"
    end = "2026-02-28"
    fridays = get_fridays_in_range(start, end)

    assert len(fridays) == 4
    assert fridays[0] == date(2026, 2, 6)
    assert fridays[1] == date(2026, 2, 13)
    assert fridays[2] == date(2026, 2, 20)
    assert fridays[3] == date(2026, 2, 27)

def test_get_fridays_in_range_with_skip():
    start = "2026-02-01"
    end = "2026-02-28"
    skip = ["2026-02-13"]
    fridays = get_fridays_in_range(start, end, skip)

    assert len(fridays) == 3
    assert fridays[0] == date(2026, 2, 6)
    assert fridays[1] == date(2026, 2, 20)
    assert fridays[2] == date(2026, 2, 27)

def test_get_fridays_in_range_exact_friday():
    start = "2026-02-06"
    end = "2026-02-13"
    fridays = get_fridays_in_range(start, end)

    assert len(fridays) == 2
    assert fridays[0] == date(2026, 2, 6)
    assert fridays[1] == date(2026, 2, 13)

def test_format_date_for_display():
    d = date(2026, 2, 6)
    assert format_date_for_display(d) == "February 06, 2026"
