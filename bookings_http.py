"""HTTP client for unrestricted Microsoft Bookings scheduling pages.

Microsoft does not document this customer-facing API as a stable public
contract. The client intentionally mirrors only the requests made by a
published, unrestricted booking page and never attempts to bypass an
authenticated page.
"""

from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import TYPE_CHECKING, Any, Callable, Iterable, Mapping
from urllib.parse import quote, unquote, urlparse

import httpx
from dateutil.parser import parse as parse_datetime

if TYPE_CHECKING:
    from booker import BookingConfig


API_PATH = "/BookingsService/api/V1/bookingBusinessesc2"
PUBLIC_BOOKINGS_HOST = "bookings.cloud.microsoft"
OWA_CANARY_SENTINEL = "X-OWA-CANARY_cookie_is_null_or_empty"
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
SERVICE_QUERY = {
    "queryOptions": {
        "filter": {
            "or": {
                "filters": [
                    {
                        "attributeFilter": {
                            "attributeName": "BookingServiceCategory",
                            "operator": "FILTER_OPERATOR_TYPE_EQUAL",
                            "stringValue": "BOOKING_SERVICE_CATEGORY_SCHEDULED",
                        }
                    },
                    {
                        "attributeFilter": {
                            "attributeName": "BookingServiceCategory",
                            "operator": "FILTER_OPERATOR_TYPE_EQUAL",
                            "stringValue": "BOOKING_SERVICE_CATEGORY_ON_DEMAND",
                        }
                    },
                ]
            }
        }
    }
}


class BookingsHttpError(RuntimeError):
    """Base error for the anonymous Bookings HTTP workflow."""


class UnsupportedBookingPageError(BookingsHttpError):
    """Raised when a URL is not an unrestricted Microsoft Bookings page."""


class BookingSelectionError(BookingsHttpError):
    """Raised when a configured service or staff member cannot be resolved."""


class BookingUnavailableError(BookingsHttpError):
    """Raised when a requested appointment interval is unavailable."""


class BookingRequestError(BookingsHttpError):
    """Raised when Microsoft rejects or cannot complete an HTTP request."""


@dataclass(frozen=True)
class BookingPageState:
    """Configuration loaded from a published Bookings page."""

    business_id: str
    page_url: str
    settings: dict[str, Any]
    business: dict[str, Any]


@dataclass(frozen=True)
class BookingSelection:
    """Live service and staff records selected for an appointment."""

    service: dict[str, Any]
    staff: dict[str, Any]


def _normalized_name(value: str) -> str:
    return " ".join(value.split()).casefold()


def _parse_duration(value: str) -> timedelta:
    """Parse the ISO-8601 duration subset returned by Bookings."""

    match = re.fullmatch(
        r"P(?:(?P<days>\d+(?:\.\d+)?)D)?"
        r"(?:T(?:(?P<hours>\d+(?:\.\d+)?)H)?"
        r"(?:(?P<minutes>\d+(?:\.\d+)?)M)?"
        r"(?:(?P<seconds>\d+(?:\.\d+)?)S)?)?",
        value or "",
    )
    if not match:
        raise BookingSelectionError(f"Unsupported service duration: {value!r}")

    parts = {name: float(raw or 0) for name, raw in match.groupdict().items()}
    duration = timedelta(
        days=parts["days"],
        hours=parts["hours"],
        minutes=parts["minutes"],
        seconds=parts["seconds"],
    )
    if duration <= timedelta(0):
        raise BookingSelectionError(f"Service duration must be positive: {value!r}")
    return duration


def _parse_api_datetime(value: str) -> datetime:
    normalized = value.removesuffix("Z")
    parsed = datetime.fromisoformat(normalized)
    return parsed.replace(tzinfo=None)


def _extract_error_message(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        text = response.text.strip()
        return text[:500] if text else response.reason_phrase

    def find_message(value: Any) -> str | None:
        if isinstance(value, Mapping):
            for key in ("message", "errorMessage", "responseMessage", "Message"):
                message = value.get(key)
                if isinstance(message, str) and message.strip():
                    return message.strip()
            for nested in value.values():
                message = find_message(nested)
                if message:
                    return message
        elif isinstance(value, list):
            for nested in value:
                message = find_message(nested)
                if message:
                    return message
        return None

    return find_message(payload) or response.reason_phrase


def _appointment_id(payload: Any) -> str | None:
    if not isinstance(payload, Mapping):
        return None

    for key in ("appointment", "bookingAppointment"):
        nested = payload.get(key)
        if isinstance(nested, Mapping):
            value = nested.get("id") or nested.get("appointmentId")
            if value:
                return str(value)

    value = payload.get("id") or payload.get("appointmentId")
    return str(value) if value else None


def _self_service_appointment_id(payload: Any) -> str | None:
    """Extract the customer capability used by the public management page."""
    if not isinstance(payload, Mapping):
        return None

    for key in ("appointment", "bookingAppointment"):
        nested = payload.get(key)
        if isinstance(nested, Mapping):
            value = nested.get("selfServiceAppointmentId")
            if value:
                return str(value)

    value = payload.get("selfServiceAppointmentId")
    return str(value) if value else None


class BookingsHttpClient:
    """Session-oriented client for an unrestricted Microsoft Bookings page."""

    def __init__(
        self,
        page_url: str,
        *,
        timeout: float = 20.0,
        max_retries: int = 2,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be greater than zero")
        if max_retries < 0:
            raise ValueError("max_retries cannot be negative")

        self.page_url = page_url
        self.max_retries = max_retries
        self._sleep = sleep
        self._owns_client = client is None
        self._client = client or httpx.Client(
            follow_redirects=True,
            timeout=timeout,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/128.0 Safari/537.36"
                )
            },
        )
        self.state: BookingPageState | None = None
        self._api_base: str | None = None
        self._api_headers: dict[str, str] = {}

    def __enter__(self) -> "BookingsHttpClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def _retry_delay(self, response: httpx.Response, attempt: int) -> float:
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                return max(0.0, float(retry_after))
            except ValueError:
                try:
                    retry_at = parsedate_to_datetime(retry_after)
                    now = datetime.now(retry_at.tzinfo)
                    return max(0.0, (retry_at - now).total_seconds())
                except (TypeError, ValueError, OverflowError):
                    pass
        return 0.5 * (2**attempt)

    def _request(
        self,
        method: str,
        url: str,
        *,
        retry_safe: bool,
        headers: Mapping[str, str] | None = None,
        json: Any = None,
    ) -> httpx.Response:
        attempts = self.max_retries + 1 if retry_safe else 1

        for attempt in range(attempts):
            try:
                response = self._client.request(
                    method,
                    url,
                    headers=headers,
                    json=json,
                )
            except httpx.TransportError as exc:
                if attempt + 1 < attempts:
                    self._sleep(0.5 * (2**attempt))
                    continue
                raise BookingRequestError(
                    f"Microsoft Bookings request failed: {exc}"
                ) from exc

            if (
                retry_safe
                and response.status_code in RETRYABLE_STATUS_CODES
                and attempt + 1 < attempts
            ):
                self._sleep(self._retry_delay(response, attempt))
                continue

            if response.is_error:
                detail = _extract_error_message(response)
                raise BookingRequestError(
                    f"Microsoft Bookings returned HTTP {response.status_code}: {detail}"
                )
            return response

        raise AssertionError("request retry loop completed without a response")

    @staticmethod
    def _extract_business_id(urls: Iterable[str]) -> str:
        for url in urls:
            path_parts = [unquote(part) for part in urlparse(url).path.split("/") if part]
            lowered = [part.casefold() for part in path_parts]

            if "book" in lowered:
                index = lowered.index("book")
                if index + 1 < len(path_parts):
                    return path_parts[index + 1]

            if "calendar" in lowered and "bookings" in lowered:
                index = lowered.index("calendar")
                if index + 1 < len(path_parts):
                    return path_parts[index + 1]

        raise UnsupportedBookingPageError(
            "Could not determine the booking business from BOOKING_URL"
        )

    def bootstrap(self) -> BookingPageState:
        """Open the public page and load its anonymous business settings."""

        page_response = self._request(
            "GET",
            self.page_url,
            retry_safe=True,
            headers={"Accept": "text/html,application/xhtml+xml"},
        )
        final_url = str(page_response.url)
        final_host = (page_response.url.host or "").casefold()

        if final_host == "login.microsoftonline.com" or final_host.endswith(
            ".login.microsoftonline.com"
        ):
            raise UnsupportedBookingPageError(
                "The booking page requires Microsoft 365 authentication"
            )
        if final_host != PUBLIC_BOOKINGS_HOST:
            raise UnsupportedBookingPageError(
                "The HTTP backend only supports pages hosted by "
                f"{PUBLIC_BOOKINGS_HOST}; the page resolved to {final_host or 'an unknown host'}"
            )

        business_id = self._extract_business_id((final_url, self.page_url))
        encoded_business_id = quote(business_id, safe="@._-")
        origin = f"{page_response.url.scheme}://{page_response.url.host}"
        self._api_base = f"{origin}{API_PATH}/{encoded_business_id}"
        self._api_headers = {
            "Accept": "application/json",
            "Content-Type": "application/json; charset=utf-8",
            "Origin": origin,
            "Prefer": 'exchange.behavior="IncludeThirdPartyOnlineMeetingProviders"',
            "Referer": final_url,
            "X-AnchorMailbox": business_id,
            "X-Req-Source": "BookingsC2",
            "X-OWA-Hosted-UX": "false",
            "X-OWA-CANARY": OWA_CANARY_SENTINEL,
            "X-OWA-SessionId": str(uuid.uuid4()),
        }

        if not any(cookie.name == "ClientId" for cookie in self._client.cookies.jar):
            self._client.cookies.set(
                "ClientId",
                uuid.uuid4().hex,
                domain=PUBLIC_BOOKINGS_HOST,
                path="/",
            )
        if not any(cookie.name == "OIDC" for cookie in self._client.cookies.jar):
            self._client.cookies.set(
                "OIDC",
                "1",
                domain=PUBLIC_BOOKINGS_HOST,
                path="/",
            )

        settings = self.get_booking_settings()
        auth_setting = (
            settings.get("settings", {})
            .get("BookingsAuthEnabled", {})
            .get("boolValue")
        )
        if auth_setting is not False:
            if auth_setting is None:
                raise UnsupportedBookingPageError(
                    "The page did not expose its booking access-control setting"
                )
            raise UnsupportedBookingPageError(
                "The booking page requires Microsoft 365 authentication"
            )

        business = self.get_business()
        self.state = BookingPageState(
            business_id=business_id,
            page_url=final_url,
            settings=settings,
            business=business,
        )
        return self.state

    def _api_request(
        self,
        method: str,
        path: str = "",
        *,
        retry_safe: bool,
        json: Any = None,
    ) -> httpx.Response:
        if not self._api_base:
            raise BookingsHttpError("Call bootstrap() before using the Bookings API")

        headers = {
            **self._api_headers,
            "X-OWA-CorrelationId": str(uuid.uuid4()),
        }
        suffix = f"/{path.lstrip('/')}" if path else "/"
        return self._request(
            method,
            f"{self._api_base}{suffix}",
            retry_safe=retry_safe,
            headers=headers,
            json=json,
        )

    @staticmethod
    def _json_object(response: httpx.Response, context: str) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as exc:
            raise BookingRequestError(
                f"Microsoft Bookings returned invalid JSON for {context}"
            ) from exc
        if not isinstance(payload, dict):
            raise BookingRequestError(
                f"Microsoft Bookings returned an unexpected response for {context}"
            )
        return payload

    def get_booking_settings(self) -> dict[str, Any]:
        payload = self._json_object(
            self._api_request("GET", "bookingsSettings", retry_safe=True),
            "booking settings",
        )
        settings = payload.get("tenantSettings", {})
        return settings if isinstance(settings, dict) else {}

    def get_business(self) -> dict[str, Any]:
        payload = self._json_object(
            self._api_request("GET", retry_safe=True),
            "business information",
        )
        business = payload.get("businessInfo", {})
        if not isinstance(business, dict):
            raise BookingRequestError(
                "Microsoft Bookings returned invalid business information"
            )
        return business

    def list_services(self) -> list[dict[str, Any]]:
        payload = self._json_object(
            self._api_request(
                "POST",
                "services",
                retry_safe=True,
                json=SERVICE_QUERY,
            ),
            "services",
        )
        services = payload.get("service", [])
        return [item for item in services if isinstance(item, dict)]

    def list_staff(self) -> list[dict[str, Any]]:
        payload = self._json_object(
            self._api_request("GET", "staffmembers", retry_safe=True),
            "staff members",
        )
        staff = payload.get("staffMembers", [])
        return [item for item in staff if isinstance(item, dict)]

    @staticmethod
    def _resolve_named_item(
        items: Iterable[dict[str, Any]],
        configured_name: str,
        *,
        field: str,
        item_type: str,
    ) -> dict[str, Any]:
        candidates = list(items)
        desired = _normalized_name(configured_name)
        exact = [
            item
            for item in candidates
            if _normalized_name(str(item.get(field, ""))) == desired
        ]
        if len(exact) == 1:
            return exact[0]

        partial = [
            item
            for item in candidates
            if desired and desired in _normalized_name(str(item.get(field, "")))
        ]
        if len(partial) == 1:
            return partial[0]
        if len(partial) > 1:
            names = ", ".join(str(item.get(field, "")) for item in partial)
            raise BookingSelectionError(
                f"Configured {item_type} {configured_name!r} is ambiguous: {names}"
            )

        available = ", ".join(
            str(item.get(field, "")) for item in candidates if item.get(field)
        )
        suffix = f" Available {item_type}s: {available}" if available else ""
        raise BookingSelectionError(
            f"Configured {item_type} {configured_name!r} was not found.{suffix}"
        )

    def resolve_selection(self, service_name: str, staff_name: str) -> BookingSelection:
        service = self._resolve_named_item(
            self.list_services(),
            service_name,
            field="title",
            item_type="service",
        )
        if service.get("isHiddenFromCustomers"):
            raise BookingSelectionError(
                f"Configured service {service_name!r} is hidden from customers"
            )
        if service.get("restrictionType") not in (
            None,
            "",
            "BOOKING_SERVICE_RESTRICTION_PUBLIC",
        ):
            raise BookingSelectionError(
                f"Configured service {service_name!r} is not publicly bookable"
            )

        staff = self._resolve_named_item(
            self.list_staff(),
            staff_name,
            field="displayName",
            item_type="staff member",
        )
        staff_id = str(staff.get("id", ""))
        allowed_staff = {str(value) for value in service.get("staffMemberIds", [])}
        if allowed_staff and staff_id not in allowed_staff:
            raise BookingSelectionError(
                f"Staff member {staff_name!r} is not assigned to {service_name!r}"
            )
        return BookingSelection(service=service, staff=staff)

    def get_staff_availability(
        self,
        *,
        service_id: str,
        staff_ids: list[str],
        start: datetime,
        end: datetime,
        time_zone: str,
    ) -> list[dict[str, Any]]:
        payload = {
            "serviceId": service_id,
            "staffIds": staff_ids,
            "startDateTime": {
                "dateTime": start.isoformat(timespec="seconds"),
                "timeZone": time_zone,
            },
            "endDateTime": {
                "dateTime": end.isoformat(timespec="seconds"),
                "timeZone": time_zone,
            },
        }
        response_payload = self._json_object(
            self._api_request(
                "POST",
                "GetStaffAvailability",
                retry_safe=True,
                json=payload,
            ),
            "staff availability",
        )
        availability = response_payload.get("staffAvailabilityResponse", [])
        return [item for item in availability if isinstance(item, dict)]

    @staticmethod
    def interval_is_available(
        availability: Iterable[dict[str, Any]],
        *,
        staff_id: str,
        start: datetime,
        end: datetime,
    ) -> bool:
        intervals: list[tuple[datetime, datetime]] = []
        for staff_entry in availability:
            if str(staff_entry.get("staffId", "")) != staff_id:
                continue
            for item in staff_entry.get("availabilityItems", []):
                if not isinstance(item, Mapping):
                    continue
                status = str(item.get("status", "")).upper()
                if status not in {
                    "AVAILABLE",
                    "BOOKINGSAVAILABILITYSTATUS_AVAILABLE",
                }:
                    continue
                try:
                    item_start = _parse_api_datetime(
                        str(item["startDateTime"]["dateTime"])
                    )
                    item_end = _parse_api_datetime(str(item["endDateTime"]["dateTime"]))
                except (KeyError, TypeError, ValueError):
                    continue
                intervals.append((item_start, item_end))

        cursor = start
        for item_start, item_end in sorted(intervals):
            if item_end <= cursor:
                continue
            if item_start > cursor:
                return False
            cursor = max(cursor, item_end)
            if cursor >= end:
                return True
        return False

    @staticmethod
    def build_appointment_payload(
        config: "BookingConfig",
        *,
        service: Mapping[str, Any],
        staff: Mapping[str, Any],
        start: datetime,
        end: datetime,
        time_zone: str,
    ) -> dict[str, Any]:
        price = service.get("defaultPrice", 0) or 0
        price_type = (
            service.get("defaultPriceType") or "SERVICEDEFAULTPRICETYPES_FREE"
        )
        address = config.address or ""

        return {
            "appointment": {
                "startTime": {
                    "dateTime": start.isoformat(timespec="seconds"),
                    "timeZone": time_zone,
                },
                "endTime": {
                    "dateTime": end.isoformat(timespec="seconds"),
                    "timeZone": time_zone,
                },
                "serviceId": str(service["serviceId"]),
                "staffMemberIds": [str(staff["id"])],
                "customers": [
                    {
                        "name": config.name,
                        "emailAddress": config.email,
                        "phone": config.phone or "",
                        "notes": config.notes or "",
                        "timeZone": "",
                        "answeredCustomQuestions": [],
                        "location": {
                            "displayName": address,
                            "address": {
                                "street": address,
                                "type": "Other",
                            },
                        },
                        "smsNotificationsEnabled": False,
                        "instanceId": "",
                        "price": price,
                        "priceType": price_type,
                    }
                ],
                "isLocationOnline": bool(service.get("isLocationOnline", False)),
                "smsNotificationsEnabled": False,
                "verificationCode": "",
                "customerTimeZone": time_zone,
                "trackingDataId": "",
                "bookingFormInfoList": [],
                "price": price,
                "priceType": price_type,
                "isAllDay": False,
                "additionalRecipients": [],
            }
        }

    def create_appointment(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Create one appointment without retrying the mutating request."""

        response = self._api_request(
            "POST",
            "appointments",
            retry_safe=False,
            json=payload,
        )
        if not response.content:
            return {}
        return self._json_object(response, "appointment creation")

    def cancel_appointment(self, self_service_appointment_id: str) -> None:
        """Cancel one customer-managed appointment without retrying the write."""
        appointment_id = str(self_service_appointment_id or "").strip()
        if not appointment_id:
            raise BookingSelectionError(
                "A self-service appointment ID is required for cancellation"
            )
        encoded_id = quote(appointment_id, safe="")
        self._api_request(
            "DELETE",
            f"appointments/{encoded_id}",
            retry_safe=False,
        )


class HttpBookingAutomation:
    """Book configured appointments through the anonymous HTTP workflow."""

    def __init__(
        self,
        *,
        timeout: float = 20.0,
        max_retries: int = 2,
        client_factory: Callable[[str], BookingsHttpClient] | None = None,
    ) -> None:
        self.timeout = timeout
        self.max_retries = max_retries
        self._client_factory = client_factory

    def _new_client(self, page_url: str) -> BookingsHttpClient:
        if self._client_factory:
            return self._client_factory(page_url)
        return BookingsHttpClient(
            page_url,
            timeout=self.timeout,
            max_retries=self.max_retries,
        )

    def book_date(self, config: "BookingConfig", target_date: date) -> dict[str, Any]:
        try:
            with self._new_client(config.url) as client:
                state = client.bootstrap()
                selection = client.resolve_selection(config.service, config.staff)

                parsed_time = parse_datetime(config.time_slot).time().replace(
                    second=0,
                    microsecond=0,
                    tzinfo=None,
                )
                start = datetime.combine(target_date, parsed_time)
                duration = _parse_duration(str(selection.service.get("defaultDuration", "")))
                end = start + duration
                time_zone = str(state.business.get("businessTimeZone") or "UTC")
                staff_id = str(selection.staff.get("id", ""))
                service_id = str(selection.service.get("serviceId", ""))
                if not staff_id or not service_id:
                    raise BookingSelectionError(
                        "Microsoft Bookings returned a service or staff member without an ID"
                    )

                day_start = datetime.combine(target_date, datetime.min.time())
                query_end = max(day_start + timedelta(days=1), end)
                availability = client.get_staff_availability(
                    service_id=service_id,
                    staff_ids=[staff_id],
                    start=day_start,
                    end=query_end,
                    time_zone=time_zone,
                )
                if not client.interval_is_available(
                    availability,
                    staff_id=staff_id,
                    start=start,
                    end=end,
                ):
                    raise BookingUnavailableError(
                        f"{config.staff} is not available on {target_date.isoformat()} "
                        f"at {config.time_slot} for {selection.service.get('formattedDuration', 'the service duration')}"
                    )

                payload = client.build_appointment_payload(
                    config,
                    service=selection.service,
                    staff=selection.staff,
                    start=start,
                    end=end,
                    time_zone=time_zone,
                )
                result = client.create_appointment(payload)
                appointment_id = _appointment_id(result)
                self_service_appointment_id = _self_service_appointment_id(result)
                id_suffix = f" (appointment {appointment_id})" if appointment_id else ""
                return {
                    "success": True,
                    "message": (
                        f"Successfully booked {target_date.isoformat()} at "
                        f"{config.time_slot} via HTTP{id_suffix}"
                    ),
                    "date": target_date.isoformat(),
                    "backend": "http",
                    "appointment_id": appointment_id,
                    "self_service_appointment_id": self_service_appointment_id,
                }
        except (BookingsHttpError, ValueError, OverflowError) as exc:
            return {
                "success": False,
                "message": f"HTTP booking failed: {exc}",
                "date": target_date.isoformat(),
                "backend": "http",
            }

    def cancel_appointment(
        self,
        page_url: str,
        self_service_appointment_id: str,
    ) -> dict[str, Any]:
        """Cancel one appointment through its customer self-service capability."""
        try:
            with self._new_client(page_url) as client:
                client.bootstrap()
                client.cancel_appointment(self_service_appointment_id)
            return {
                "success": True,
                "message": "Successfully cancelled appointment via HTTP",
                "backend": "http",
            }
        except BookingsHttpError as exc:
            return {
                "success": False,
                "message": f"HTTP cancellation failed: {exc}",
                "backend": "http",
            }
