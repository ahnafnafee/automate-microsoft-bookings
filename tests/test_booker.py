from datetime import date
from booker import BookingAutomation, BookingConfig

def test_perform_booking_confidence(mocker):
    # This is a confidence test to ensure that the sequential calls required to
    # schedule a booking are fired appropriately against the Playwright Page API.

    config = BookingConfig(
        url="http://fake.url",
        service="Test Service",
        staff="Test Staff",
        time_slot="1:00 PM",
        name="Jane Doe",
        email="jane@doe.com"
    )
    target_date = date(2026, 8, 24)

    automation = BookingAutomation(headless=True)

    mock_page = mocker.Mock()
    mock_page.content.return_value = "August 2026"

    # Mock some basic locator methods
    mock_locator = mocker.Mock()
    mock_page.get_by_text.return_value = mock_locator
    mock_page.get_by_role.return_value = mock_locator
    mock_locator.first = mock_locator

    # We will just verify that it doesn't crash and returns success
    result = automation._perform_booking(mock_page, config, target_date)

    assert result["success"] is True
    assert result["date"] == "2026-08-24"
    assert "Successfully booked" in result["message"]

    # Verify page navigation
    mock_page.goto.assert_called_with("http://fake.url")

    # Verify that it attempts to find and click the service
    mock_page.get_by_text.assert_any_call("Test Service")

    # Verify that it attempts to fill user details
    mock_page.get_by_role.assert_any_call("textbox", name="First and last name")
    mock_page.get_by_role.assert_any_call("textbox", name="Email")
