"""Tests for the Bosch EasyAir sensor and switch entities."""

from __future__ import annotations

from dataclasses import replace
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock, Mock

from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import PERCENTAGE, UnitOfTemperature

from custom_components.bosch_easyair.api import EasyAirThermostat
from custom_components.bosch_easyair.climate import BoschEasyAirClimate
from custom_components.bosch_easyair.const import DOMAIN
from custom_components.bosch_easyair.sensor import SENSORS, BoschEasyAirSensor
from custom_components.bosch_easyair.switch import BoschEasyAirFanSwitch

DEVICE_ID = "001122aabbcc"


def _thermostat(**overrides) -> EasyAirThermostat:
    """Return a thermostat state with the sensor-facing fields populated."""
    thermostat = EasyAirThermostat(
        id=DEVICE_ID,
        name="Hallway",
        model="BCC110",
        serial_number=DEVICE_ID,
        firmware_version="1.2.3",
        wifi_firmware_version=None,
        current_temperature=70.5,
        target_temperature=75.0,
        target_temperature_low=65.0,
        target_temperature_high=75.0,
        min_temperature=None,
        max_temperature=None,
        humidity=41,
        temperature_unit=UnitOfTemperature.FAHRENHEIT,
        hvac_mode="cool",
        hvac_action="cooling",
        available_modes=["off", "cool", "heat", "auto"],
        raw={"mode": "1", "fanstatus": "1", "stage": "2", "power": "1"},
        fan_on=True,
        stage="2",
        power="1",
    )
    return replace(thermostat, **overrides) if overrides else thermostat


def _coordinator(thermostat: EasyAirThermostat) -> Mock:
    """Return a coordinator stand-in exposing one thermostat."""
    coordinator = Mock()
    coordinator.data = {DEVICE_ID: thermostat}
    coordinator.last_update_success = True
    coordinator.async_set_fan = AsyncMock()
    return coordinator


def _sensor(key: str, thermostat: EasyAirThermostat) -> BoschEasyAirSensor:
    """Return the sensor entity for ``key``."""
    description = next(d for d in SENSORS if d.key == key)
    return BoschEasyAirSensor(_coordinator(thermostat), DEVICE_ID, description)


class SensorTest(TestCase):
    """Verify the sensors expose what Thermal Comfort and friends need."""

    def test_temperature_sensor_has_device_class_and_device_unit(self) -> None:
        """Temperature is a real temperature sensor in the thermostat's unit."""
        sensor = _sensor("temperature", _thermostat())

        self.assertEqual(sensor.device_class, SensorDeviceClass.TEMPERATURE)
        self.assertEqual(sensor.state_class, SensorStateClass.MEASUREMENT)
        self.assertEqual(
            sensor.native_unit_of_measurement, UnitOfTemperature.FAHRENHEIT
        )
        self.assertEqual(sensor.native_value, 70.5)
        self.assertEqual(sensor.unique_id, f"{DOMAIN}_{DEVICE_ID}_temperature")

    def test_temperature_unit_follows_thermostat(self) -> None:
        """A Celsius thermostat yields a Celsius sensor."""
        sensor = _sensor(
            "temperature",
            _thermostat(temperature_unit=UnitOfTemperature.CELSIUS),
        )

        self.assertEqual(sensor.native_unit_of_measurement, UnitOfTemperature.CELSIUS)

    def test_humidity_sensor(self) -> None:
        """Humidity is a percentage humidity sensor."""
        sensor = _sensor("humidity", _thermostat())

        self.assertEqual(sensor.device_class, SensorDeviceClass.HUMIDITY)
        self.assertEqual(sensor.native_unit_of_measurement, PERCENTAGE)
        self.assertEqual(sensor.native_value, 41)

    def test_stage_sensor_is_raw_with_power_attribute(self) -> None:
        """Stage is surfaced as reported, with power alongside it."""
        sensor = _sensor("stage", _thermostat())

        self.assertIsNone(sensor.device_class)
        self.assertIsNone(sensor.state_class)
        self.assertEqual(sensor.native_value, "2")
        self.assertEqual(sensor.extra_state_attributes, {"power": "1"})

    def test_missing_values_are_none(self) -> None:
        """Fields absent from the payload read as unknown."""
        thermostat = _thermostat(humidity=None, stage=None, power=None)

        self.assertIsNone(_sensor("humidity", thermostat).native_value)
        self.assertIsNone(_sensor("stage", thermostat).native_value)

    def test_entities_share_the_climate_device(self) -> None:
        """All platforms group under one device registry entry."""
        thermostat = _thermostat()
        climate = BoschEasyAirClimate(_coordinator(thermostat), DEVICE_ID)
        sensor = _sensor("humidity", thermostat)
        switch = BoschEasyAirFanSwitch(_coordinator(thermostat), DEVICE_ID)

        self.assertEqual(climate.device_info, sensor.device_info)
        self.assertEqual(climate.device_info, switch.device_info)
        self.assertEqual(
            climate.device_info["identifiers"], {(DOMAIN, DEVICE_ID)}
        )

    def test_climate_unique_id_is_unchanged(self) -> None:
        """Moving to the shared base must not re-id existing climate entities."""
        climate = BoschEasyAirClimate(_coordinator(_thermostat()), DEVICE_ID)

        self.assertEqual(climate.unique_id, f"{DOMAIN}_{DEVICE_ID}")


class FanSwitchTest(IsolatedAsyncioTestCase):
    """Verify the fan switch reads fanstatus and writes through the coordinator."""

    def test_state_follows_fan_on(self) -> None:
        """is_on mirrors the parsed fanstatus, including unknown."""
        self.assertTrue(
            BoschEasyAirFanSwitch(_coordinator(_thermostat()), DEVICE_ID).is_on
        )
        self.assertFalse(
            BoschEasyAirFanSwitch(
                _coordinator(_thermostat(fan_on=False)), DEVICE_ID
            ).is_on
        )
        self.assertIsNone(
            BoschEasyAirFanSwitch(
                _coordinator(_thermostat(fan_on=None)), DEVICE_ID
            ).is_on
        )

    async def test_turn_on_and_off_call_coordinator(self) -> None:
        """Both directions go through the coordinator's fan method."""
        coordinator = _coordinator(_thermostat())
        switch = BoschEasyAirFanSwitch(coordinator, DEVICE_ID)

        await switch.async_turn_on()
        await switch.async_turn_off()

        self.assertEqual(
            [call.args for call in coordinator.async_set_fan.await_args_list],
            [(DEVICE_ID, True), (DEVICE_ID, False)],
        )

    def test_unique_id_is_suffixed(self) -> None:
        """The switch must not collide with the climate entity's unique id."""
        switch = BoschEasyAirFanSwitch(_coordinator(_thermostat()), DEVICE_ID)

        self.assertEqual(switch.unique_id, f"{DOMAIN}_{DEVICE_ID}_fan")
