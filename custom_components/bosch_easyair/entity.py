"""Shared entity base for Bosch EasyAir platforms."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from functools import wraps
from typing import Any

from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import EasyAirError, EasyAirInvalidInputError, EasyAirThermostat
from .const import DOMAIN, MANUFACTURER, MODEL_BCC110
from .coordinator import BoschEasyAirDataUpdateCoordinator


def translate_api_errors(
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


class BoschEasyAirEntity(CoordinatorEntity[BoschEasyAirDataUpdateCoordinator]):
    """Base entity bound to one EasyAir thermostat.

    Every platform entity for a thermostat shares the same device registry
    identifiers, so the climate, sensor and switch entities group under one
    device in the UI.
    """

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: BoschEasyAirDataUpdateCoordinator,
        device_id: str,
        unique_id_suffix: str | None = None,
    ) -> None:
        """Initialize the entity for ``device_id``.

        ``unique_id_suffix`` distinguishes the per-thermostat entities from the
        climate entity, whose unique id has no suffix for backwards
        compatibility with entries created before the other platforms existed.
        """
        super().__init__(coordinator)
        self._device_id = device_id
        self._attr_unique_id = f"{DOMAIN}_{device_id}"
        if unique_id_suffix:
            self._attr_unique_id += f"_{unique_id_suffix}"
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
