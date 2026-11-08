"""Sensor platform for Bosch EasyAir."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import EasyAirThermostat
from .coordinator import BoschEasyAirDataUpdateCoordinator
from .entity import BoschEasyAirEntity


@dataclass(frozen=True, kw_only=True)
class BoschEasyAirSensorDescription(SensorEntityDescription):
    """Describe a Bosch EasyAir sensor and how to read it from the state."""

    value_fn: Callable[[EasyAirThermostat], Any]
    attributes_fn: Callable[[EasyAirThermostat], dict[str, Any]] | None = None


SENSORS: tuple[BoschEasyAirSensorDescription, ...] = (
    # Temperature and humidity are exposed as real sensors (not only as
    # climate attributes) so downstream integrations that pick inputs by
    # sensor domain and device class -- Thermal Comfort, statistics, the
    # history graph -- can use them. The unit comes from the thermostat
    # itself, so it is set per entity rather than on the description.
    BoschEasyAirSensorDescription(
        key="temperature",
        translation_key="temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda device: device.current_temperature,
    ),
    BoschEasyAirSensorDescription(
        key="humidity",
        translation_key="humidity",
        device_class=SensorDeviceClass.HUMIDITY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        value_fn=lambda device: device.humidity,
    ),
    # ``stage`` is the equipment stage field the cloud reports and that
    # ``_hvac_action`` already treats as "something is running" when non-zero.
    # Its exact vocabulary (compressor stage, aux/emergency heat) has not been
    # captured, so the raw value is surfaced as-is with no state class: a
    # numeric-looking stage still gets a history timeline, and an unexpected
    # non-numeric value cannot break the entity.
    BoschEasyAirSensorDescription(
        key="stage",
        translation_key="stage",
        value_fn=lambda device: device.stage,
        attributes_fn=lambda device: {"power": device.power},
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Bosch EasyAir sensor entities."""
    coordinator: BoschEasyAirDataUpdateCoordinator = entry.runtime_data
    async_add_entities(
        BoschEasyAirSensor(coordinator, device_id, description)
        for device_id in coordinator.data
        for description in SENSORS
    )


class BoschEasyAirSensor(BoschEasyAirEntity, SensorEntity):
    """A read-only value reported by an EasyAir thermostat."""

    entity_description: BoschEasyAirSensorDescription

    def __init__(
        self,
        coordinator: BoschEasyAirDataUpdateCoordinator,
        device_id: str,
        description: BoschEasyAirSensorDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, device_id, unique_id_suffix=description.key)
        self.entity_description = description

    @property
    def native_unit_of_measurement(self) -> str | None:
        """Return the unit, taking temperature from the thermostat itself."""
        if self.entity_description.device_class is SensorDeviceClass.TEMPERATURE:
            return self._device.temperature_unit
        return self.entity_description.native_unit_of_measurement

    @property
    def native_value(self) -> Any:
        """Return the sensor value."""
        return self.entity_description.value_fn(self._device)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return any supporting raw fields for this sensor."""
        if (attributes_fn := self.entity_description.attributes_fn) is None:
            return None
        return attributes_fn(self._device)
