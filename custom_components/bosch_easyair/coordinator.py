"""Data coordinator for Bosch EasyAir."""
from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import EasyAirAuthError, EasyAirClient, EasyAirError, EasyAirThermostat
from .const import DEFAULT_SCAN_INTERVAL, DOMAIN

_LOGGER = logging.getLogger(__name__)


class BoschEasyAirDataUpdateCoordinator(
    DataUpdateCoordinator[dict[str, EasyAirThermostat]]
):
    """Coordinate polling EasyAir thermostats."""

    def __init__(self, hass: HomeAssistant, client: EasyAirClient) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=DEFAULT_SCAN_INTERVAL,
        )
        self.client = client

    async def _async_update_data(self) -> dict[str, EasyAirThermostat]:
        """Fetch latest device data from EasyAir."""
        try:
            thermostats = await self.client.async_get_thermostats()
        except EasyAirAuthError as err:
            # A permanently-invalid/expired token cannot recover by retrying;
            # start a reauth flow so the user can re-authenticate.
            raise ConfigEntryAuthFailed(
                f"EasyAir authentication failed: {err}"
            ) from err
        except EasyAirError as err:
            raise UpdateFailed(f"EasyAir update failed: {err}") from err
        return {thermostat.id: thermostat for thermostat in thermostats}

    async def async_set_temperature(
        self,
        device_id: str,
        *,
        target_temperature: float | None = None,
        target_temperature_low: float | None = None,
        target_temperature_high: float | None = None,
    ) -> None:
        """Set a thermostat temperature and refresh state."""
        current = self.data.get(device_id)
        await self.client.async_set_temperature(
            device_id,
            target_temperature=target_temperature,
            target_temperature_low=target_temperature_low,
            target_temperature_high=target_temperature_high,
            current=current,
        )
        await self.async_request_refresh()

    async def async_set_hvac_mode(self, device_id: str, hvac_mode: str) -> None:
        """Set a thermostat HVAC mode and refresh state."""
        await self.client.async_set_hvac_mode(device_id, hvac_mode)
        await self.async_request_refresh()
