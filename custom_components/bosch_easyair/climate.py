"""Climate platform for Bosch EasyAir."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from functools import wraps
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
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import EasyAirError, EasyAirInvalidInputError, EasyAirThermostat
from .const import DOMAIN, MANUFACTURER, MODEL_BCC110
from .coordinator import BoschEasyAirDataUpdateCoordinator

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


def _translate_api_errors(
    func: Callable[..., Awaitable[None]],
) -> Callable[..., Awaitable[None]]:
    """Re-raise API failures as the errors Home Assistant knows how to show.

    ``EasyAirError`` is not a ``HomeAssistantError``, so without this it
    escapes the service call unhandled: the user sees "Unknown error" and the
    log gets a traceback instead of the message the API layer wrote.
    """

    @wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> None:
        try:
            await func(*args, **kwargs)
        except EasyAirInvalidInputError as err:
            # The request itself was wrong, so retrying it verbatim cannot help.
            raise ServiceValidationError(str(err)) from err
        except EasyAirError as err:
            raise HomeAssistantError(str(err)) from err

    return wrapper


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


class BoschEasyAirClimate(
    CoordinatorEntity[BoschEasyAirDataUpdateCoordinator], ClimateEntity
):
    """Representation of a Bosch EasyAir thermostat."""

    _attr_has_entity_name = True
    _attr_name = None

    def __init__(
        self, coordinator: BoschEasyAirDataUpdateCoordinator, device_id: str
    ) -> None:
        """Initialize the climate entity."""
        super().__init__(coordinator)
        self._device_id = device_id
        self._attr_unique_id = f"{DOMAIN}_{device_id}"
        self._last_device = coordinator.data[device_id]

    @callback
    def _handle_coordinator_update(self) -> None:
        """Cache the newest thermostat state, then write the entity state."""
        if (device := self.coordinator.data.get(self._device_id)) is not None:
            self._last_device = device
        super()._handle_coordinator_update()

    @property
    def _device(self) -> EasyAirThermostat:
        """Return the newest thermostat state, falling back to the cache.

        The cache keeps state writes from crashing if the thermostat
        temporarily drops out of the cloud device list.
        """
        device = self.coordinator.data.get(self._device_id)
        return device if device is not None else self._last_device

    @property
    def device_info(self) -> DeviceInfo:
        """Return device registry information."""
        device = self._device
        return DeviceInfo(
            identifiers={(DOMAIN, device.serial_number or device.id)},
            manufacturer=MANUFACTURER,
            model=device.model or MODEL_BCC110,
            name=device.name,
            serial_number=device.serial_number,
            sw_version=device.firmware_version,
        )

    @property
    def available(self) -> bool:
        """Return whether the entity is available."""
        return super().available and self._device_id in self.coordinator.data

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

    @_translate_api_errors
    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set a new target temperature."""
        await self.coordinator.async_set_temperature(
            self._device_id,
            target_temperature=kwargs.get(ATTR_TEMPERATURE),
            target_temperature_low=kwargs.get(ATTR_TARGET_TEMP_LOW),
            target_temperature_high=kwargs.get(ATTR_TARGET_TEMP_HIGH),
        )

    @_translate_api_errors
    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set HVAC mode."""
        await self.coordinator.async_set_hvac_mode(
            self._device_id, HA_TO_MODE[hvac_mode]
        )
