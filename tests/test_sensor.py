"""Tests for the Local vision meter sensors."""

from datetime import timedelta
from typing import Any
from unittest.mock import Mock, patch

from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    snapshot_platform,
)
from syrupy.assertion import SnapshotAssertion
from tarsius import BackendError, BlankFrameError, ImageError
from tarsius.meter import MeterReader, Rejection

from custom_components.local_vision.const import (
    CONF_CALIBRATION,
    DOMAIN,
    UPDATE_INTERVAL,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import CoreState, HomeAssistant
from homeassistant.helpers import entity_registry as er

from . import (
    CAMERA,
    CONFIDENCE_ENTITY_ID,
    METER_ENTITY_ID,
    STATUS_ENTITY_ID,
    FakeCamera,
    meter_reading,
    render_frame,
    setup_integration,
)


async def _poll(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    # The coordinator spreads its refreshes over the second.
    freezer.tick(UPDATE_INTERVAL + timedelta(seconds=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)


def _state(hass: HomeAssistant, entity_id: str = METER_ENTITY_ID) -> str:
    return hass.states.get(entity_id).state


@pytest.mark.usefixtures(
    "camera", "mock_meter_reader", "entity_registry_enabled_by_default"
)
async def test_entities(
    hass: HomeAssistant,
    meter_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    snapshot: SnapshotAssertion,
) -> None:
    """The meter, its status and its confidence join the camera device."""
    await setup_integration(hass, meter_entry)
    await snapshot_platform(hass, entity_registry, snapshot, meter_entry.entry_id)

    sensor = entity_registry.async_get(METER_ENTITY_ID)
    assert sensor.device_id == entity_registry.async_get(CAMERA).device_id


@pytest.mark.parametrize(
    ("rejection", "status"),
    [
        pytest.param(Rejection.FAULT, "fault", id="fault"),
        pytest.param(Rejection.LOW_CONFIDENCE, "low_confidence", id="unsure"),
        pytest.param(Rejection.INCONSISTENT, "inconsistent", id="inconsistent"),
    ],
)
@pytest.mark.usefixtures("camera")
async def test_rejected_reading_keeps_value(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    meter_entry: MockConfigEntry,
    mock_meter_reader: Mock,
    rejection: Rejection,
    status: str,
) -> None:
    """Only accepted readings change the value; the status says why not."""
    await setup_integration(hass, meter_entry)
    assert _state(hass) == "123.456"

    mock_meter_reader.return_value = meter_reading(124.5, rejection)
    await _poll(hass, freezer)

    assert _state(hass) == "123.456"
    assert _state(hass, STATUS_ENTITY_ID) == status
    assert hass.states.get(STATUS_ENTITY_ID).attributes["candidate"] == 124.5


@pytest.mark.usefixtures("camera")
async def test_value_never_decreases(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    meter_entry: MockConfigEntry,
    mock_meter_reader: Mock,
) -> None:
    """A lower reading is rejected, a jitter within one unit keeps the value."""
    await setup_integration(hass, meter_entry)

    mock_meter_reader.return_value = meter_reading(123.4)
    await _poll(hass, freezer)
    assert _state(hass) == "123.456"
    assert _state(hass, STATUS_ENTITY_ID) == "decreased"

    mock_meter_reader.return_value = meter_reading(123.4555)
    await _poll(hass, freezer)
    assert _state(hass) == "123.456"
    assert _state(hass, STATUS_ENTITY_ID) == "accepted"

    mock_meter_reader.return_value = meter_reading(123.47)
    await _poll(hass, freezer)
    assert _state(hass) == "123.47"


@pytest.mark.usefixtures("camera")
async def test_too_fast_is_rejected(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    meter_entry: MockConfigEntry,
    mock_meter_reader: Mock,
) -> None:
    """A rise faster than the maximum rate is a misread."""
    await setup_integration(hass, meter_entry)

    mock_meter_reader.return_value = meter_reading(133.456)
    await _poll(hass, freezer)

    assert _state(hass) == "123.456"
    assert _state(hass, STATUS_ENTITY_ID) == "too_fast"


@pytest.mark.usefixtures("camera")
async def test_restart_keeps_value_and_checks(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    meter_entry: MockConfigEntry,
    mock_meter_reader: Mock,
) -> None:
    """After a restart the value is there at once and lower readings stay out."""
    await setup_integration(hass, meter_entry)
    stored = hass_storage[f"{DOMAIN}.{meter_entry.entry_id}"]["data"]["meter"]
    assert stored["value"] == 123.456
    assert stored["plausibility"]["value"] == 123.456
    await hass.config_entries.async_unload(meter_entry.entry_id)

    # During startup the camera is not asked, the stored value shows.
    hass.set_state(CoreState.starting)
    await hass.config_entries.async_setup(meter_entry.entry_id)
    await hass.async_block_till_done()
    assert _state(hass) == "123.456"
    assert _state(hass, STATUS_ENTITY_ID) == STATE_UNKNOWN

    mock_meter_reader.return_value = meter_reading(23.456)
    hass.set_state(CoreState.running)
    await meter_entry.runtime_data.async_refresh()
    assert _state(hass) == "123.456"
    assert _state(hass, STATUS_ENTITY_ID) == "decreased"


@pytest.mark.parametrize(
    "stored",
    [
        pytest.param({"meter": {"value": "high", "plausibility": {"x": 1}}}, id="bad"),
        pytest.param({"meter": ["not", "a", "dict"]}, id="not_dict"),
        pytest.param(["not", "a", "dict"], id="no_data"),
    ],
)
@pytest.mark.usefixtures("camera", "mock_meter_reader")
async def test_invalid_stored_state(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    meter_entry: MockConfigEntry,
    stored: Any,
) -> None:
    """Unusable stored data starts the meter afresh."""
    hass_storage[f"{DOMAIN}.{meter_entry.entry_id}"] = {
        "version": 1,
        "minor_version": 1,
        "key": f"{DOMAIN}.{meter_entry.entry_id}",
        "data": stored,
    }
    await setup_integration(hass, meter_entry)

    assert meter_entry.state is ConfigEntryState.LOADED
    assert _state(hass) == "123.456"


@pytest.mark.usefixtures("camera", "mock_meter_reader")
async def test_unknown_fields_survive(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    meter_entry: MockConfigEntry,
) -> None:
    """Fields written by a newer version are kept on the next save."""
    key = f"{DOMAIN}.{meter_entry.entry_id}"
    hass_storage[key] = {
        "version": 1,
        "minor_version": 1,
        "key": key,
        "data": {"history": [1], "meter": {"value": 100.0, "trend": 2}},
    }
    await setup_integration(hass, meter_entry)

    data = hass_storage[key]["data"]
    assert data["history"] == [1]
    assert data["meter"]["trend"] == 2
    assert data["meter"]["value"] == 123.456


@pytest.mark.usefixtures("camera", "entity_registry_enabled_by_default")
async def test_blank_frame_keeps_value(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    meter_entry: MockConfigEntry,
    mock_meter_reader: Mock,
) -> None:
    """A dark frame leaves the value, the status and confidence are unknown."""
    await setup_integration(hass, meter_entry)

    mock_meter_reader.side_effect = BlankFrameError("dark")
    await _poll(hass, freezer)

    assert _state(hass) == "123.456"
    assert _state(hass, STATUS_ENTITY_ID) == STATE_UNKNOWN
    assert _state(hass, CONFIDENCE_ENTITY_ID) == STATE_UNKNOWN


@pytest.mark.usefixtures("camera")
async def test_without_calibration(
    hass: HomeAssistant, meter_entry: MockConfigEntry, mock_meter_reader: Mock
) -> None:
    """A meter set up while it could not be read is read uncalibrated."""
    meter_entry.add_to_hass(hass)
    options = dict(meter_entry.options)
    del options[CONF_CALIBRATION]
    hass.config_entries.async_update_entry(meter_entry, options=options)
    await setup_integration(hass, meter_entry)

    assert _state(hass) == "123.456"
    assert mock_meter_reader.call_args.args[2] is None


@pytest.mark.parametrize(
    ("frame", "error", "message"),
    [
        pytest.param(
            None,
            None,
            f"Could not get a frame from {CAMERA}: Unable to get image",
            id="no_frame",
        ),
        pytest.param(
            b"not an image",
            ImageError("cannot decode image"),
            "The camera returned an image that could not be decoded: "
            "cannot decode image",
            id="undecodable",
        ),
        pytest.param(
            render_frame(car=True),
            BackendError("broken"),
            "The recognition model failed: broken",
            id="model",
        ),
    ],
)
async def test_unavailable_without_reading(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    camera: FakeCamera,
    meter_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
    frame: bytes | None,
    error: Exception | None,
    message: str,
) -> None:
    """Without a frame or a working reader the sensors are unavailable."""
    with patch.object(MeterReader, "load"):
        await setup_integration(hass, meter_entry)
        camera.frame = frame
        with patch.object(MeterReader, "read", side_effect=error):
            await _poll(hass, freezer)

    assert _state(hass) == STATE_UNAVAILABLE
    assert f"Error fetching Water meter data: {message}" in caplog.text


@pytest.mark.usefixtures("camera")
async def test_model_failure_fails_setup(
    hass: HomeAssistant, meter_entry: MockConfigEntry
) -> None:
    """Without the bundled readers the entry fails instead of retrying."""
    with patch.object(MeterReader, "load", side_effect=BackendError("missing")):
        await setup_integration(hass, meter_entry)

    assert meter_entry.state is ConfigEntryState.SETUP_ERROR
    assert meter_entry.reason == "The recognition model failed: missing"


@pytest.mark.usefixtures("camera")
async def test_correct_value(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    freezer: FrozenDateTimeFactory,
    meter_entry: MockConfigEntry,
    mock_meter_reader: Mock,
) -> None:
    """A corrected value may be lower; the next readings follow from it."""
    await setup_integration(hass, meter_entry)

    result = await hass.config_entries.options.async_init(meter_entry.entry_id)
    assert result["step_id"] == "correct"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"value": 100.0}
    )
    assert result["reason"] == "correct_successful"
    assert _state(hass) == "100.0"
    stored = hass_storage[f"{DOMAIN}.{meter_entry.entry_id}"]["data"]["meter"]
    assert stored["plausibility"]["value"] == 100.0

    mock_meter_reader.return_value = meter_reading(100.2)
    await _poll(hass, freezer)
    assert _state(hass) == "100.2"


@pytest.mark.usefixtures("camera", "mock_meter_reader")
async def test_correct_not_loaded(
    hass: HomeAssistant, meter_entry: MockConfigEntry
) -> None:
    """The value can't be corrected while the entry is not loaded."""
    await setup_integration(hass, meter_entry)
    await hass.config_entries.async_unload(meter_entry.entry_id)

    result = await hass.config_entries.options.async_init(meter_entry.entry_id)
    assert result["reason"] == "not_loaded"
