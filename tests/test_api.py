"""Tests for the Bosch EasyAir API client."""

from __future__ import annotations

from typing import Any
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from homeassistant.const import UnitOfTemperature

from custom_components.bosch_easyair.api import EasyAirClient, EasyAirThermostat


class RecordingEasyAirClient(EasyAirClient):
    """EasyAir client that records requests without sending them."""

    def __init__(self) -> None:
        """Initialize the request recorder."""
        self.request: tuple[str, str, dict[str, Any]] | None = None

    async def _request(self, method: str, path: str, **kwargs: Any) -> None:
        """Record a request."""
        self.request = (method, path, kwargs)


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
