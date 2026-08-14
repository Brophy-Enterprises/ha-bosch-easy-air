"""Tests for the Bosch EasyAir API client."""

from __future__ import annotations

from typing import Any
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from homeassistant.const import UnitOfTemperature

from custom_components.bosch_easyair.api import (
    EasyAirClient,
    EasyAirConnectionError,
    EasyAirThermostat,
    _token_endpoint_error,
)

SECRET = "secret-access-token-or-device-id"


class FakeResponse:
    """Provide the response attributes used by the API client."""

    def __init__(
        self,
        status: int,
        body: str,
        content_type: str = "application/json",
    ) -> None:
        """Initialize a fake response."""
        self.status = status
        self.body = body
        self.headers = {"Content-Type": content_type}

    async def text(self) -> str:
        """Return the configured response body."""
        return self.body


class RecordingEasyAirClient(EasyAirClient):
    """EasyAir client that records requests without sending them."""

    def __init__(self, response: Any = None) -> None:
        """Initialize the request recorder."""
        self.request: tuple[str, str, dict[str, Any]] | None = None
        self.response = response

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        """Record a request."""
        self.request = (method, path, kwargs)
        return self.response


class PollingEasyAirClient(EasyAirClient):
    """Return controlled device-list and status payloads across polls."""

    def __init__(self) -> None:
        """Initialize without a real HTTP session."""
        self._temperature_units = {}
        self.device: dict[str, Any] = {
            "gatewayId": "001122aabbcc",
            "tempUnit": "F",
            "mode": "1",
            "temp": "75.0-65.0",
            "room_temp": "70.0",
        }
        self.status: dict[str, Any] = {"tempUnit": None}

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        """Return the current device-list payload."""
        return {"data": [self.device]}

    async def async_get_status(self, device_id: str) -> Any:
        """Return the current status payload."""
        return self.status


def _thermostat() -> EasyAirThermostat:
    """Return a thermostat state suitable for a setpoint write."""
    return EasyAirThermostat(
        id="001122aabbcc",
        name="Bosch EasyAir",
        model="BCC110",
        serial_number="001122aabbcc",
        firmware_version=None,
        wifi_firmware_version=None,
        current_temperature=70.0,
        target_temperature=74.0,
        target_temperature_low=65.0,
        target_temperature_high=74.0,
        min_temperature=None,
        max_temperature=None,
        humidity=None,
        temperature_unit=UnitOfTemperature.FAHRENHEIT,
        hvac_mode="cool",
        hvac_action="idle",
        available_modes=["off", "cool", "heat", "auto"],
        raw={},
    )


class SetTemperatureTest(IsolatedAsyncioTestCase):
    """Verify the captured iOS set-temperature request contract."""

    async def test_request_matches_captured_payload(self) -> None:
        """Whole-degree setpoints and timestamps are JSON strings."""
        client = RecordingEasyAirClient()

        with patch(
            "custom_components.bosch_easyair.api._timestamp_ms",
            return_value="1783835632458",
        ):
            await client.async_set_temperature(
                "001122aabbcc",
                target_temperature=75.0,
                current=_thermostat(),
            )

        self.assertEqual(
            client.request,
            (
                "POST",
                "/control/temp",
                {
                    "json": {
                        "device_id": "001122aabbcc",
                        "temp": "75.0-65.0",
                        "hold": "1",
                        "timestamp": "1783835632458",
                    }
                },
            ),
        )


class TemperatureUnitTest(IsolatedAsyncioTestCase):
    """Verify incomplete polls cannot change a thermostat's native unit."""

    async def test_missing_status_unit_preserves_fahrenheit(self) -> None:
        """Retain the list or last-known unit when status omits its unit."""
        client = PollingEasyAirClient()

        first = (await client.async_get_thermostats())[0]
        self.assertEqual(first.temperature_unit, UnitOfTemperature.FAHRENHEIT)

        client.device = {
            key: value for key, value in client.device.items() if key != "tempUnit"
        }
        second = (await client.async_get_thermostats())[0]
        self.assertEqual(second.temperature_unit, UnitOfTemperature.FAHRENHEIT)

    async def test_initial_unit_is_inferred_from_fahrenheit_values(self) -> None:
        """Do not default an unmistakably Fahrenheit payload to Celsius."""
        client = PollingEasyAirClient()
        client.device.pop("tempUnit")

        thermostat = (await client.async_get_thermostats())[0]

        self.assertEqual(
            thermostat.temperature_unit, UnitOfTemperature.FAHRENHEIT
        )


class RequestErrorTest(IsolatedAsyncioTestCase):
    """Verify request failures are useful without exposing sensitive data."""

    async def test_http_error_includes_sanitized_response_body(self) -> None:
        """Include useful response details while redacting secret values."""
        client = object.__new__(EasyAirClient)
        client._refresh_token = None
        response = FakeResponse(
            500,
            '{"message":"Internal server error","code":5001,'
            f'"device_id":"{SECRET}","access_token":"{SECRET}"}}',
        )

        with self.assertRaises(EasyAirConnectionError) as raised:
            await client._handle_response(
                response,
                "GET",
                f"/device/list?device_id={SECRET}",
                auth=True,
                base_url=None,
                form=None,
                json={"access_token": SECRET},
                params={"device_id": SECRET},
                retry_auth=False,
                token_request=False,
                request_token=SECRET,
            )

        self.assertEqual(
            str(raised.exception),
            "EasyAir GET /device/list failed with HTTP 500: "
            '{"message":"Internal server error","code":5001,'
            '"device_id":"<redacted>","access_token":"<redacted>"}',
        )
        self.assertNotIn(SECRET, str(raised.exception))

    async def test_token_error_redacts_response_description(self) -> None:
        """Expose the OAuth error while redacting its response description."""
        response = FakeResponse(
            400,
            f'{{"error":"invalid_grant","error_description":"{SECRET}"}}',
        )

        error = await _token_endpoint_error(response)

        self.assertEqual(
            str(error),
            "EasyAir POST /oauth2/token failed with HTTP 400 (invalid_grant): "
            '{"error":"invalid_grant","error_description":"<redacted>"}',
        )
        self.assertNotIn(SECRET, str(error))

    async def test_non_json_error_body_is_retained_and_sanitized(self) -> None:
        """Keep free-form diagnostics while removing recognizable credentials."""
        client = object.__new__(EasyAirClient)
        client._refresh_token = None
        response = FakeResponse(
            502,
            f"device 001122aabbcc failed at 123456789012; access_token={SECRET}",
            content_type="text/plain",
        )

        with self.assertRaises(EasyAirConnectionError) as raised:
            await client._handle_response(
                response,
                "GET",
                "/device/list",
                auth=True,
                base_url=None,
                form=None,
                json=None,
                params=None,
                retry_auth=False,
                token_request=False,
                request_token=SECRET,
            )

        self.assertEqual(
            str(raised.exception),
            "EasyAir GET /device/list failed with HTTP 502: "
            "device <redacted> failed at 123456789012; access_token=<redacted>",
        )
        self.assertNotIn(SECRET, str(raised.exception))


class SetHvacModeTest(IsolatedAsyncioTestCase):
    """Verify the captured iOS change-mode contract."""

    async def test_returns_mode_confirmed_by_response(self) -> None:
        """Normalize and return the mode applied by the API."""
        client = RecordingEasyAirClient(
            {"message": "Operation succeed", "mode": "2", "distr": "0"}
        )

        with patch(
            "custom_components.bosch_easyair.api._timestamp_ms",
            return_value="1783835637946",
        ):
            mode = await client.async_set_hvac_mode("001122aabbcc", "heat")

        self.assertEqual(mode, "heat")
        self.assertEqual(
            client.request,
            (
                "POST",
                "/control/change_mode",
                {
                    "json": {
                        "device_id": "001122aabbcc",
                        "mode": "2",
                        "distr": "0",
                        "timestamp": "1783835637946",
                    }
                },
            ),
        )
