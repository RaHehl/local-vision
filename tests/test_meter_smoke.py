"""A meter read end to end with the real tarsius reader, no mocks."""

from datetime import timedelta
from typing import Any

from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.local_vision.const import (
    CONF_CALIBRATION,
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
from custom_components.local_vision.meter_store import SAVE_DELAY
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


def _entry(calibration: dict[str, object] | None = None) -> MockConfigEntry:
    options: dict[str, object] = {
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
    }
    if calibration is not None:
        options[CONF_CALIBRATION] = calibration
    return MockConfigEntry(
        domain=DOMAIN, title="Water meter", minor_version=3, options=options
    )


async def test_exposure_is_learnt_and_kept(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    freezer: FrozenDateTimeFactory,
    camera: FakeCamera,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An entry without an exposure record learns one from its readings."""
    camera.frame = meter_jpeg("00627", (8.84, 8.4), roll=0.884)
    entry = _entry()
    await setup_integration(hass, entry)
    freezer.tick(timedelta(seconds=SAVE_DELAY + 1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    exposure = hass_storage[f"{DOMAIN}.{entry.entry_id}"]["data"]["exposure"]
    assert len(exposure["baseline"]) == 7
    assert exposure["light"] > 0

    # Reloaded, the record is used again; a damaged one is ignored.
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.runtime_data.session.exposure == exposure
    await hass.config_entries.async_unload(entry.entry_id)
    hass_storage[f"{DOMAIN}.{entry.entry_id}"]["data"]["exposure"] = {
        **exposure,
        "baseline": [[1, 2]],
    }
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert "Ignoring invalid stored exposure" in caplog.text
    assert hass.states.get(STATUS_ENTITY_ID).state == "accepted"
