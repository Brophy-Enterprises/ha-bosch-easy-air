"""Data coordinator for Bosch EasyAir."""
from __future__ import annotations

from dataclasses import replace
import logging

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    HVAC_MODE_TO_BCC,
    EasyAirAuthError,
    EasyAirClient,
    EasyAirError,
    EasyAirThermostat,
)
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
        """Set a thermostat HVAC mode and publish the accepted change."""
        confirmed_mode = await self.client.async_set_hvac_mode(device_id, hvac_mode)
        if (current := self.data.get(device_id)) is None:
            # There is no entity state to update. The normal polling cycle can
            # rediscover the device without adding a redundant command poll.
            return

        updated = dict(self.data)
        updated[device_id] = _with_hvac_mode(current, confirmed_mode)
        # The command response includes the mode applied by the API. Publish it
        # directly; the regular poll remains responsible for later changes.
        self.async_set_updated_data(updated)


def _with_hvac_mode(
    thermostat: EasyAirThermostat, hvac_mode: str
) -> EasyAirThermostat:
    """Return thermostat state updated for an accepted HVAC mode command."""
    target_temperature = thermostat.target_temperature_high
    if hvac_mode == "heat":
        target_temperature = thermostat.target_temperature_low
    elif hvac_mode == "off":
        target_temperature = None

    return replace(
        thermostat,
        target_temperature=target_temperature,
        hvac_mode=hvac_mode,
        hvac_action="off" if hvac_mode == "off" else None,
        raw={**thermostat.raw, "mode": HVAC_MODE_TO_BCC[hvac_mode]},
    )
