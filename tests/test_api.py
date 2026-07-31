"""Tests for the Bosch EasyAir API client."""

from __future__ import annotations

from typing import Any
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from homeassistant.const import UnitOfTemperature

from custom_components.bosch_easyair.api import EasyAirClient, EasyAirThermostat


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
