"""A meter read end to end with the real tarsius reader, no mocks."""

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.local_vision.const import (
    CONF_CONFIRMED,
    CONF_COUNTERCLOCKWISE,
    CONF_DECIMAL_WHEELS,
    CONF_DIALS,
    CONF_DIGITS,
    CONF_MAX_RATE,
    CONF_ROTATION,
    CONF_TYPE,
    CONF_UNIT,
    CONF_WHEELS,
    DOMAIN,
    TYPE_METER,
)
from homeassistant.const import CONF_DEVICE_CLASS, CONF_ENTITY_ID
from homeassistant.core import HomeAssistant

from . import CAMERA, METER_ENTITY_ID, STATUS_ENTITY_ID, FakeCamera, setup_integration
from .meter_scene import DIAL_REGIONS, DIGITS_REGION, meter_jpeg


async def test_reads_a_rendered_meter(hass: HomeAssistant, camera: FakeCamera) -> None:
    """Five wheels at 00627, rolling towards 8, and dials at 8.84 and 8.4."""
    camera.frame = meter_jpeg("00627", (8.84, 8.4), roll=0.884)
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Water meter",
        minor_version=3,
        options={
            CONF_TYPE: TYPE_METER,
            CONF_ENTITY_ID: CAMERA,
            CONF_DEVICE_CLASS: "water",
            CONF_UNIT: "m³",
            CONF_MAX_RATE: 4.0,
            CONF_DIGITS: DIGITS_REGION,
            CONF_ROTATION: 0,
            CONF_WHEELS: 5,
            CONF_DECIMAL_WHEELS: 0,
            CONF_DIALS: [
                {**region, CONF_COUNTERCLOCKWISE: False} for region in DIAL_REGIONS
            ],
            CONF_CONFIRMED: {"value": 627.8, "at": "2026-10-01T00:00:00+00:00"},
        },
    )
    await setup_integration(hass, entry)

    assert hass.states.get(STATUS_ENTITY_ID).state == "accepted"
    assert abs(float(hass.states.get(METER_ENTITY_ID).state) - 627.884) < 0.01
