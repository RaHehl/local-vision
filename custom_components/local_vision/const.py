"""Constants for the Local vision integration."""

from datetime import timedelta
import logging

from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import STATE_OFF, STATE_ON, UnitOfEnergy, UnitOfVolume

DOMAIN = "local_vision"
LOGGER = logging.getLogger(__package__)

CONF_TYPE = "type"
CONF_LEFT = "left"
CONF_TOP = "top"
CONF_RIGHT = "right"
CONF_BOTTOM = "bottom"
CONF_FRAMES = "frames"

# What an entry recognizes; one entry is one detector.
TYPE_STATE = "state"
TYPE_METER = "meter"

DEFAULT_REGION = {CONF_LEFT: 0, CONF_TOP: 0, CONF_RIGHT: 100, CONF_BOTTOM: 100}

# States a camera can show for an area; the rest need a measurement.
DEVICE_CLASSES = [
    BinarySensorDeviceClass.DOOR,
    BinarySensorDeviceClass.GARAGE_DOOR,
    BinarySensorDeviceClass.OCCUPANCY,
    BinarySensorDeviceClass.OPENING,
    BinarySensorDeviceClass.WINDOW,
]

DEFAULT_DEVICE_CLASS = BinarySensorDeviceClass.OCCUPANCY
STATES = (STATE_ON, STATE_OFF)

UPDATE_INTERVAL = timedelta(minutes=5)

CONF_UNIT = "unit"
CONF_DIGITS = "digits"
CONF_ROTATION = "rotation"
CONF_WHEELS = "wheels"
CONF_DECIMAL_WHEELS = "decimal_wheels"
CONF_DIALS = "dials"
CONF_DIAL_COUNT = "dial_count"
CONF_COUNTERCLOCKWISE = "counterclockwise"
CONF_CALIBRATION = "calibration"
CONF_MAX_RATE = "max_rate"
CONF_VALUE = "value"
# The value the user confirmed and when: {"value": float, "at": iso time}.
CONF_CONFIRMED = "confirmed"

METER_DEVICE_CLASSES = [
    SensorDeviceClass.WATER,
    SensorDeviceClass.GAS,
    SensorDeviceClass.ENERGY,
]
# Units a meter of each kind counts in, the first is the default. All are
# valid for the device class and accepted by the Energy dashboard; a gas
# meter that counts kWh is set up as energy.
METER_UNITS: dict[str, list[str]] = {
    SensorDeviceClass.WATER: [
        UnitOfVolume.CUBIC_METERS,
        UnitOfVolume.LITERS,
        UnitOfVolume.GALLONS,
        UnitOfVolume.CUBIC_FEET,
        UnitOfVolume.CENTUM_CUBIC_FEET,
        UnitOfVolume.MILLE_CUBIC_FEET,
    ],
    SensorDeviceClass.GAS: [
        UnitOfVolume.CUBIC_METERS,
        UnitOfVolume.CUBIC_FEET,
        UnitOfVolume.CENTUM_CUBIC_FEET,
        UnitOfVolume.MILLE_CUBIC_FEET,
        UnitOfVolume.LITERS,
    ],
    SensorDeviceClass.ENERGY: [
        UnitOfEnergy.KILO_WATT_HOUR,
        UnitOfEnergy.WATT_HOUR,
        UnitOfEnergy.MEGA_WATT_HOUR,
    ],
}
# Suggested maximum increase per hour, above a household's peak: 4 m³/h of
# water (a Q3 2.5 meter is built for 3.125), 6 m³/h of gas (a G4 meter's
# maximum) and 50 kWh/h of electricity (a 3 x 63 A supply).
DEFAULT_MAX_RATES: dict[str, dict[str, float]] = {
    SensorDeviceClass.WATER: {
        UnitOfVolume.CUBIC_METERS: 4,
        UnitOfVolume.LITERS: 4000,
        UnitOfVolume.GALLONS: 1050,
        UnitOfVolume.CUBIC_FEET: 140,
        UnitOfVolume.CENTUM_CUBIC_FEET: 1.4,
        UnitOfVolume.MILLE_CUBIC_FEET: 0.14,
    },
    SensorDeviceClass.GAS: {
        UnitOfVolume.CUBIC_METERS: 6,
        UnitOfVolume.CUBIC_FEET: 210,
        UnitOfVolume.CENTUM_CUBIC_FEET: 2.1,
        UnitOfVolume.MILLE_CUBIC_FEET: 0.21,
        UnitOfVolume.LITERS: 6000,
    },
    SensorDeviceClass.ENERGY: {
        UnitOfEnergy.KILO_WATT_HOUR: 50,
        UnitOfEnergy.WATT_HOUR: 50000,
        UnitOfEnergy.MEGA_WATT_HOUR: 0.05,
    },
}
# Limits of tarsius.meter.MeterLayout.
MAX_WHEELS = 12
MAX_DIALS = 8
