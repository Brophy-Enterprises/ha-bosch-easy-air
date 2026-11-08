"""Switch platform for Bosch EasyAir."""
from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import BoschEasyAirDataUpdateCoordinator
from .entity import BoschEasyAirEntity, translate_api_errors


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Bosch EasyAir switch entities."""
    coordinator: BoschEasyAirDataUpdateCoordinator = entry.runtime_data
    async_add_entities(
        BoschEasyAirFanSwitch(coordinator, device_id)
        for device_id in coordinator.data
    )


class BoschEasyAirFanSwitch(BoschEasyAirEntity, SwitchEntity):
    """Fan circulation on/off for an EasyAir thermostat.

    On maps to the BCC "fan on" setting (continuous circulation); off maps to
    "auto" (the fan runs only with a heat or cool call). It drives the same
    ``/control/fan`` endpoint the EasyAir app uses and reads its state from the
    ``fanstatus`` status field; see ``api._fan_on`` for that mapping.
    """

    _attr_translation_key = "fan_circulation"
    _attr_device_class = SwitchDeviceClass.SWITCH

    def __init__(
        self, coordinator: BoschEasyAirDataUpdateCoordinator, device_id: str
    ) -> None:
        """Initialize the fan switch."""
        super().__init__(coordinator, device_id, unique_id_suffix="fan")

    @property
    def is_on(self) -> bool | None:
        """Return whether fan circulation is on.

        ``None`` (shown as unknown) when the status payload did not carry a
        ``fanstatus`` value; the switch is still usable, it just cannot claim a
        state it has not seen.
        """
        return self._device.fan_on

    @translate_api_errors
    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn fan circulation on."""
        await self.coordinator.async_set_fan(self._device_id, True)

    @translate_api_errors
    async def async_turn_off(self, **kwargs: Any) -> None:
        """Set the fan back to auto."""
        await self.coordinator.async_set_fan(self._device_id, False)
