import json
from datetime import date, datetime

import httpx

from booker import BookingConfig
from bookings_http import (
    OWA_CANARY_SENTINEL,
    BookingsHttpClient,
    HttpBookingAutomation,
)


BUSINESS_ID = "GTAOfficeHours@gmuedu.onmicrosoft.com"
PAGE_URL = f"https://outlook.office365.com/book/{BUSINESS_ID}/"
PUBLIC_PAGE_URL = f"https://bookings.cloud.microsoft/book/{BUSINESS_ID}/"
API_ROOT = (
    "https://bookings.cloud.microsoft/BookingsService/api/V1/"
    f"bookingBusinessesc2/{BUSINESS_ID}"
)
SERVICE_ID = "service-2-hours"
STAFF_ID = "staff-d7"


def booking_config(**overrides):
    values = {
        "url": PAGE_URL,
        "service": "Office Hours 2 Hours",
        "staff": "ENGR 4456 D7",
        "time_slot": "2:00 PM",
        "name": "Jane Doe",
        "email": "jane@example.edu",
        "address": "",
        "phone": "",
        "notes": "Weekly office hours",
        "backend": "http",
    }
    values.update(overrides)
    return BookingConfig(**values)


def business_payload():
    return {
        "businessInfo": {
            "displayName": "GTA Office Hours Desk Reservation",
            "businessTimeZone": "Eastern Standard Time",
            "schedulingPolicy": {"timeSlotInterval": "PT15M"},
        }
    }


def service_payload():
    return {
        "service": [
            {
                "title": "Office Hours 2 Hours",
                "serviceId": SERVICE_ID,
                "staffMemberIds": [STAFF_ID],
                "isHiddenFromCustomers": False,
                "restrictionType": "BOOKING_SERVICE_RESTRICTION_PUBLIC",
                "defaultDuration": "PT2H",
                "formattedDuration": "2 hours",
                "defaultPrice": 0,
                "defaultPriceType": "SERVICEDEFAULTPRICETYPES_FREE",
                "isLocationOnline": False,
            }
        ]
    }


def staff_payload():
    return {
        "staffMembers": [
            {
                "id": STAFF_ID,
                "displayName": "ENGR 4456 D7",
            }
        ]
    }


def available_payload(end="2026-08-21T19:00:00"):
    return {
        "staffAvailabilityResponse": [
            {
                "staffId": STAFF_ID,
                "availabilityItems": [
                    {
                        "status": "BOOKINGSAVAILABILITYSTATUS_AVAILABLE",
                        "startDateTime": {
                            "dateTime": "2026-08-21T09:00:00",
                            "timeZone": "Eastern Standard Time",
                        },
                        "endDateTime": {
                            "dateTime": end,
                            "timeZone": "Eastern Standard Time",
                        },
                    }
                ],
            }
        ]
    }


def api_response(request, *, auth_enabled=False, availability_end=None):
    path = request.url.path
    if request.url.host == "outlook.office365.com":
        return httpx.Response(302, headers={"Location": PUBLIC_PAGE_URL})
    if path == f"/book/{BUSINESS_ID}/":
        return httpx.Response(
            200,
            text="<html><title>Bookings</title></html>",
            headers={"Set-Cookie": "ClientId=test-client; Path=/; Secure"},
        )
    if path.endswith("/bookingsSettings"):
        return httpx.Response(
            200,
            json={
                "tenantSettings": {
                    "settings": {
                        "BookingsAuthEnabled": {"boolValue": auth_enabled}
                    }
                }
            },
        )
    if path == f"/BookingsService/api/V1/bookingBusinessesc2/{BUSINESS_ID}/":
        return httpx.Response(200, json=business_payload())
    if path.endswith("/services"):
        return httpx.Response(200, json=service_payload())
    if path.endswith("/staffmembers"):
        return httpx.Response(200, json=staff_payload())
    if path.endswith("/GetStaffAvailability"):
        return httpx.Response(
            200,
            json=available_payload(availability_end or "2026-08-21T19:00:00"),
        )
    raise AssertionError(f"Unexpected request: {request.method} {request.url}")


def make_automation(handler, *, max_retries=2, sleep=lambda _: None):
    raw_client = httpx.Client(
        transport=httpx.MockTransport(handler),
        follow_redirects=True,
    )

    def factory(url):
        return BookingsHttpClient(
            url,
            client=raw_client,
            max_retries=max_retries,
            sleep=sleep,
        )

    return HttpBookingAutomation(client_factory=factory), raw_client


def test_http_automation_creates_public_appointment():
    captured = {}

    def handler(request):
        if request.url.path.endswith("/appointments"):
            captured["request"] = request
            captured["payload"] = json.loads(request.content)
            return httpx.Response(
                201,
                json={"appointment": {"id": "appointment-123"}},
            )
        return api_response(request)

    automation, raw_client = make_automation(handler)
    try:
        result = automation.book_date(booking_config(), date(2026, 8, 21))
    finally:
        raw_client.close()

    assert result["success"] is True
    assert result["backend"] == "http"
    assert result["appointment_id"] == "appointment-123"

    request = captured["request"]
    assert request.headers["x-owa-canary"] == OWA_CANARY_SENTINEL
    assert request.headers["x-anchormailbox"] == BUSINESS_ID
    assert "authorization" not in request.headers
    assert "ClientId=" in request.headers["cookie"]
    assert "OIDC=1" in request.headers["cookie"]

    appointment = captured["payload"]["appointment"]
    assert appointment["serviceId"] == SERVICE_ID
    assert appointment["staffMemberIds"] == [STAFF_ID]
    assert appointment["startTime"] == {
        "dateTime": "2026-08-21T14:00:00",
        "timeZone": "Eastern Standard Time",
    }
    assert appointment["endTime"]["dateTime"] == "2026-08-21T16:00:00"
    assert appointment["customers"][0]["name"] == "Jane Doe"
    assert appointment["customers"][0]["emailAddress"] == "jane@example.edu"


def test_http_automation_does_not_submit_unavailable_slot():
    appointment_requests = 0

    def handler(request):
        nonlocal appointment_requests
        if request.url.path.endswith("/appointments"):
            appointment_requests += 1
            return httpx.Response(201, json={"appointment": {"id": "unexpected"}})
        return api_response(request, availability_end="2026-08-21T15:00:00")

    automation, raw_client = make_automation(handler)
    try:
        result = automation.book_date(booking_config(), date(2026, 8, 21))
    finally:
        raw_client.close()

    assert result["success"] is False
    assert "not available" in result["message"]
    assert appointment_requests == 0


def test_http_automation_rejects_authenticated_page():
    def handler(request):
        return api_response(request, auth_enabled=True)

    automation, raw_client = make_automation(handler)
    try:
        result = automation.book_date(booking_config(), date(2026, 8, 21))
    finally:
        raw_client.close()

    assert result["success"] is False
    assert "requires Microsoft 365 authentication" in result["message"]


def test_safe_requests_retry_after_throttling():
    settings_calls = 0
    delays = []

    def handler(request):
        nonlocal settings_calls
        if request.url.path.endswith("/bookingsSettings"):
            settings_calls += 1
            if settings_calls == 1:
                return httpx.Response(429, headers={"Retry-After": "0"})
        return api_response(request)

    automation, raw_client = make_automation(handler, sleep=delays.append)
    try:
        client = automation._new_client(PAGE_URL)
        state = client.bootstrap()
    finally:
        raw_client.close()

    assert state.business_id == BUSINESS_ID
    assert settings_calls == 2
    assert delays == [0.0]


def test_appointment_write_is_never_retried():
    appointment_requests = 0

    def handler(request):
        nonlocal appointment_requests
        if request.url.path.endswith("/appointments"):
            appointment_requests += 1
            return httpx.Response(503, json={"message": "Service unavailable"})
        return api_response(request)

    automation, raw_client = make_automation(handler, max_retries=5)
    try:
        result = automation.book_date(booking_config(), date(2026, 8, 21))
    finally:
        raw_client.close()

    assert result["success"] is False
    assert "HTTP 503" in result["message"]
    assert appointment_requests == 1


def test_adjacent_available_intervals_cover_appointment():
    availability = [
        {
            "staffId": STAFF_ID,
            "availabilityItems": [
                {
                    "status": "BOOKINGSAVAILABILITYSTATUS_AVAILABLE",
                    "startDateTime": {"dateTime": "2026-08-21T14:00:00"},
                    "endDateTime": {"dateTime": "2026-08-21T15:00:00"},
                },
                {
                    "status": "BOOKINGSAVAILABILITYSTATUS_AVAILABLE",
                    "startDateTime": {"dateTime": "2026-08-21T15:00:00"},
                    "endDateTime": {"dateTime": "2026-08-21T16:00:00"},
                },
            ],
        }
    ]

    assert BookingsHttpClient.interval_is_available(
        availability,
        staff_id=STAFF_ID,
        start=datetime(2026, 8, 21, 14),
        end=datetime(2026, 8, 21, 16),
    )
