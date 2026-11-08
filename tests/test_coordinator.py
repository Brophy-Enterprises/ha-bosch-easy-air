"""Tests for the Bosch EasyAir data coordinator."""

from __future__ import annotations

from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, Mock

from homeassistant.const import UnitOfTemperature

from custom_components.bosch_easyair.api import EasyAirThermostat
from custom_components.bosch_easyair.coordinator import (
    BoschEasyAirDataUpdateCoordinator,
)

DEVICE_ID = "001122aabbcc"


def _thermostat() -> EasyAirThermostat:
    """Return a cooling thermostat state."""
    return EasyAirThermostat(
        id=DEVICE_ID,
        name="Bosch EasyAir",
        model="BCC110",
        serial_number=DEVICE_ID,
        firmware_version=None,
        wifi_firmware_version=None,
        current_temperature=70.0,
        target_temperature=75.0,
        target_temperature_low=65.0,
        target_temperature_high=75.0,
        min_temperature=None,
        max_temperature=None,
        humidity=None,
        temperature_unit=UnitOfTemperature.FAHRENHEIT,
        hvac_mode="cool",
        hvac_action="cooling",
        available_modes=["off", "cool", "heat", "auto"],
        raw={"mode": "1"},
    )


class SetHvacModeTest(IsolatedAsyncioTestCase):
    """Verify HVAC mode writes update Home Assistant immediately."""

    async def test_successful_command_publishes_requested_mode(self) -> None:
        """Do not overwrite an accepted mode with an immediate stale poll."""
        coordinator = object.__new__(BoschEasyAirDataUpdateCoordinator)
        coordinator.client = Mock()
        coordinator.client.async_set_hvac_mode = AsyncMock(return_value="off")
        coordinator.data = {DEVICE_ID: _thermostat()}
        coordinator.async_set_updated_data = Mock()
        coordinator.async_request_refresh = AsyncMock()

        await coordinator.async_set_hvac_mode(DEVICE_ID, "off")

        coordinator.client.async_set_hvac_mode.assert_awaited_once_with(
            DEVICE_ID, "off"
        )
        coordinator.async_request_refresh.assert_not_awaited()
        updated = coordinator.async_set_updated_data.call_args.args[0][DEVICE_ID]
        self.assertEqual(updated.hvac_mode, "off")
        self.assertEqual(updated.hvac_action, "off")
        self.assertIsNone(updated.target_temperature)
        self.assertEqual(updated.raw["mode"], "0")


class SetFanTest(IsolatedAsyncioTestCase):
    """Verify fan writes publish optimistically instead of re-polling."""

    async def test_successful_command_publishes_fan_state(self) -> None:
        """Do not let an immediate stale poll flip the switch back."""
        coordinator = object.__new__(BoschEasyAirDataUpdateCoordinator)
        coordinator.client = Mock()
        coordinator.client.async_set_fan = AsyncMock()
        coordinator.data = {DEVICE_ID: _thermostat()}
        coordinator.async_set_updated_data = Mock()
        coordinator.async_request_refresh = AsyncMock()

        await coordinator.async_set_fan(DEVICE_ID, True)

        coordinator.client.async_set_fan.assert_awaited_once_with(DEVICE_ID, True)
        coordinator.async_request_refresh.assert_not_awaited()
        updated = coordinator.async_set_updated_data.call_args.args[0][DEVICE_ID]
        self.assertTrue(updated.fan_on)
        self.assertEqual(updated.raw["fanstatus"], "1")
        # The rest of the thermostat state is carried over untouched.
        self.assertEqual(updated.hvac_mode, "cool")
        self.assertEqual(updated.raw["mode"], "1")

    async def test_unknown_device_only_sends_command(self) -> None:
        """A device missing from the last poll gets the write but no publish."""
        coordinator = object.__new__(BoschEasyAirDataUpdateCoordinator)
        coordinator.client = Mock()
        coordinator.client.async_set_fan = AsyncMock()
        coordinator.data = {}
        coordinator.async_set_updated_data = Mock()

        await coordinator.async_set_fan(DEVICE_ID, False)

        coordinator.client.async_set_fan.assert_awaited_once_with(DEVICE_ID, False)
        coordinator.async_set_updated_data.assert_not_called()
