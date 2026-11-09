"""Climate platform for Bosch EasyAir."""
from __future__ import annotations

from typing import Any

from homeassistant.components.climate import ClimateEntity
from homeassistant.components.climate.const import (
    ATTR_TARGET_TEMP_HIGH,
    ATTR_TARGET_TEMP_LOW,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import BoschEasyAirDataUpdateCoordinator
from .entity import BoschEasyAirEntity, translate_api_errors

MODE_TO_HA = {
    "off": HVACMode.OFF,
    "heat": HVACMode.HEAT,
    "cool": HVACMode.COOL,
    "auto": HVACMode.HEAT_COOL,
}
HA_TO_MODE = {value: key for key, value in MODE_TO_HA.items()}

ACTION_TO_HA = {
    "heating": HVACAction.HEATING,
    "cooling": HVACAction.COOLING,
    "fan": HVACAction.FAN,
    "idle": HVACAction.IDLE,
    "off": HVACAction.OFF,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Bosch EasyAir climate entities."""
    coordinator: BoschEasyAirDataUpdateCoordinator = entry.runtime_data
    async_add_entities(
        BoschEasyAirClimate(coordinator, device_id)
        for device_id in coordinator.data
    )


class BoschEasyAirClimate(BoschEasyAirEntity, ClimateEntity):
    """Representation of a Bosch EasyAir thermostat."""

    _attr_name = None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional EasyAir metadata."""
        device = self._device
        return {
            "easyair_device_id": device.id,
            "model": device.model,
            "serial_number": device.serial_number,
            "wifi_firmware_version": device.wifi_firmware_version,
        }

    @property
    def current_humidity(self) -> int | None:
        """Return the current humidity."""
        return self._device.humidity

    @property
    def supported_features(self) -> ClimateEntityFeature:
        """Return supported climate features.

        A single target temperature is only writable when the mode says which
        half of the BCC setpoint pair it belongs to, so ``heat``/``cool`` get
        TARGET_TEMPERATURE and everything else -- ``auto``, and any mode that
        did not parse -- gets TARGET_TEMPERATURE_RANGE. Keying the fallback on
        the mode rather than on whether the setpoints parsed matters: a ``temp``
        payload that failed to split would otherwise render a single-target
        slider in auto mode whose every write is rejected by
        ``async_set_temperature``, leaving a control that can never succeed.
        """
        features = ClimateEntityFeature.TURN_ON | ClimateEntityFeature.TURN_OFF
        if self._device.hvac_mode in ("heat", "cool"):
            return features | ClimateEntityFeature.TARGET_TEMPERATURE
        return features | ClimateEntityFeature.TARGET_TEMPERATURE_RANGE

    @property
    def temperature_unit(self):
        """Return the unit of measurement."""
        return self._device.temperature_unit

    @property
    def current_temperature(self) -> float | None:
        """Return the current temperature."""
        return self._device.current_temperature

    @property
    def target_temperature(self) -> float | None:
        """Return the target temperature."""
        return self._device.target_temperature

    @property
    def target_temperature_low(self) -> float | None:
        """Return the lower target temperature."""
        return self._device.target_temperature_low

    @property
    def target_temperature_high(self) -> float | None:
        """Return the upper target temperature."""
        return self._device.target_temperature_high

    @property
    def min_temp(self) -> float:
        """Return the minimum supported temperature."""
        if (min_temp := self._device.min_temperature) is not None:
            return min_temp
        return super().min_temp

    @property
    def max_temp(self) -> float:
        """Return the maximum supported temperature."""
        if (max_temp := self._device.max_temperature) is not None:
            return max_temp
        return super().max_temp

    @property
    def hvac_mode(self) -> HVACMode | None:
        """Return the current HVAC mode."""
        return MODE_TO_HA.get(self._device.hvac_mode or "")

    @property
    def hvac_modes(self) -> list[HVACMode]:
        """Return supported HVAC modes."""
        modes = [
            MODE_TO_HA[mode]
            for mode in self._device.available_modes
            if mode in MODE_TO_HA
        ]
        return modes or list(MODE_TO_HA.values())

    @property
    def hvac_action(self) -> HVACAction | None:
        """Return the current HVAC action."""
        return ACTION_TO_HA.get(self._device.hvac_action or "")

    @translate_api_errors
    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set a new target temperature."""
        await self.coordinator.async_set_temperature(
            self._device_id,
            target_temperature=kwargs.get(ATTR_TEMPERATURE),
            target_temperature_low=kwargs.get(ATTR_TARGET_TEMP_LOW),
            target_temperature_high=kwargs.get(ATTR_TARGET_TEMP_HIGH),
        )

    @translate_api_errors
    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set HVAC mode."""
        await self.coordinator.async_set_hvac_mode(
            self._device_id, HA_TO_MODE[hvac_mode]
        )
