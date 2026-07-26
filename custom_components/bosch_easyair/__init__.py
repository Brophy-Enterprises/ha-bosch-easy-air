"""Bosch EasyAir integration."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import EasyAirClient, EasyAirTokens
from .const import (
    CONF_ACCESS_TOKEN,
    CONF_REFRESH_TOKEN,
    PLATFORMS,
)
from .coordinator import BoschEasyAirDataUpdateCoordinator

type BoschEasyAirConfigEntry = ConfigEntry[BoschEasyAirDataUpdateCoordinator]


async def async_setup_entry(
    hass: HomeAssistant, entry: BoschEasyAirConfigEntry
) -> bool:
    """Set up Bosch EasyAir from a config entry."""

    async def async_update_tokens(tokens: EasyAirTokens) -> None:
        """Persist refreshed OAuth tokens."""
        data = dict(entry.data)
        data[CONF_ACCESS_TOKEN] = tokens.access_token
        if tokens.refresh_token:
            data[CONF_REFRESH_TOKEN] = tokens.refresh_token
        # Reachable during async_config_entry_first_refresh() below if the
        # token rotates at setup. Safe only because no update listener is
        # registered -- do not add entry.add_update_listener() here without
        # guarding this call, or setup will reload itself in a loop.
        hass.config_entries.async_update_entry(entry, data=data)

    client = EasyAirClient(
        session=async_get_clientsession(hass),
        access_token=entry.data[CONF_ACCESS_TOKEN],
        refresh_token=entry.data.get(CONF_REFRESH_TOKEN),
        token_updater=async_update_tokens,
    )
    coordinator = BoschEasyAirDataUpdateCoordinator(hass, client)
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: BoschEasyAirConfigEntry
) -> bool:
    """Unload a Bosch EasyAir config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
