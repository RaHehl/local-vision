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

METER_DEVICE_CLASSES = [
    SensorDeviceClass.WATER,
    SensorDeviceClass.GAS,
    SensorDeviceClass.ENERGY,
]
# Units the Energy dashboard accepts for each kind, the first is the default.
METER_UNITS: dict[str, list[str]] = {
    SensorDeviceClass.WATER: [
        UnitOfVolume.CUBIC_METERS,
        UnitOfVolume.LITERS,
        UnitOfVolume.CUBIC_FEET,
        UnitOfVolume.GALLONS,
        UnitOfVolume.CENTUM_CUBIC_FEET,
    ],
    SensorDeviceClass.GAS: [
        UnitOfVolume.CUBIC_METERS,
        UnitOfVolume.CUBIC_FEET,
        UnitOfVolume.CENTUM_CUBIC_FEET,
        UnitOfEnergy.KILO_WATT_HOUR,
    ],
    SensorDeviceClass.ENERGY: [
        UnitOfEnergy.KILO_WATT_HOUR,
        UnitOfEnergy.WATT_HOUR,
        UnitOfEnergy.MEGA_WATT_HOUR,
    ],
}
# Suggested maximum increase per hour, well above a household's peak flow.
DEFAULT_MAX_RATES: dict[str, float] = {
    UnitOfVolume.CUBIC_METERS: 10,
    UnitOfVolume.LITERS: 10000,
    UnitOfVolume.CUBIC_FEET: 350,
    UnitOfVolume.GALLONS: 2600,
    UnitOfVolume.CENTUM_CUBIC_FEET: 3.5,
    UnitOfEnergy.KILO_WATT_HOUR: 100,
    UnitOfEnergy.WATT_HOUR: 100000,
    UnitOfEnergy.MEGA_WATT_HOUR: 0.1,
}
# Limits of tarsius.meter.MeterLayout.
MAX_WHEELS = 12
MAX_DIALS = 8
