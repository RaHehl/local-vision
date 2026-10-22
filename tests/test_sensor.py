"""Tests for the Local vision meter sensors."""

from datetime import timedelta
import threading
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
from tarsius.meter import Calibration, MeterReader, Rejection

from custom_components.local_vision.const import (
    CONF_CALIBRATION,
    CONF_CONFIRMED,
    CONF_MAX_RATE,
    DOMAIN,
    UPDATE_INTERVAL,
)
from custom_components.local_vision.meter_store import SAVE_DELAY, STORAGE_VERSION
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import CoreState, HomeAssistant
from homeassistant.helpers import entity_registry as er, issue_registry as ir
from homeassistant.util import dt as dt_util

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


async def _poll(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    delta: timedelta = UPDATE_INTERVAL,
) -> None:
    # The coordinator spreads its refreshes over the second.
    freezer.tick(delta + timedelta(seconds=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)


async def _flush(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    """Let delayed saves reach the store."""
    freezer.tick(timedelta(seconds=SAVE_DELAY + 1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


def _state(hass: HomeAssistant, entity_id: str = METER_ENTITY_ID) -> str:
    return hass.states.get(entity_id).state


def _key(entry: MockConfigEntry) -> str:
    return f"{DOMAIN}.{entry.entry_id}"


def _store(hass_storage: dict[str, Any], entry: MockConfigEntry, data: Any) -> None:
    hass_storage[_key(entry)] = {
        "version": STORAGE_VERSION,
        "minor_version": 1,
        "key": _key(entry),
        "data": data,
    }


def _with_options(hass: HomeAssistant, entry: MockConfigEntry, **changes: Any) -> None:
    entry.add_to_hass(hass)
    options = {**entry.options, **changes}
    hass.config_entries.async_update_entry(
        entry, options={k: v for k, v in options.items() if v is not None}
    )


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


@pytest.mark.usefixtures("camera", "mock_meter_reader")
async def test_confidence_disabled_by_default(
    hass: HomeAssistant,
    meter_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """The confidence sensor is there but disabled until the user wants it."""
    await setup_integration(hass, meter_entry)

    entry = entity_registry.async_get(CONFIDENCE_ENTITY_ID)
    assert entry.disabled_by is er.RegistryEntryDisabler.INTEGRATION
    assert hass.states.get(CONFIDENCE_ENTITY_ID) is None


@pytest.mark.parametrize(
    "rejection",
    [
        Rejection.FAULT,
        Rejection.LOW_CONFIDENCE,
        Rejection.INCONSISTENT,
    ],
)
@pytest.mark.usefixtures("camera")
async def test_rejected_reading_keeps_value(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    meter_entry: MockConfigEntry,
    mock_meter_reader: Mock,
    rejection: Rejection,
) -> None:
    """Only accepted readings change the value; the status says why not."""
    await setup_integration(hass, meter_entry)
    assert _state(hass) == "123.456"

    mock_meter_reader.return_value = meter_reading(124.5, rejection)
    await _poll(hass, freezer)

    assert _state(hass) == "123.456"
    assert _state(hass, STATUS_ENTITY_ID) == rejection.value
    assert hass.states.get(STATUS_ENTITY_ID).attributes["candidate"] == 124.5


@pytest.mark.parametrize(
    ("reading", "state", "status"),
    [
        pytest.param(
            meter_reading(133.5, Rejection.AMBIGUOUS, alternatives=(123.5,)),
            "123.5",
            "accepted",
            id="one_fits",
        ),
        pytest.param(
            meter_reading(113.5, alternatives=(123.5,)),
            "123.5",
            "accepted",
            id="biased_wheel",
        ),
        pytest.param(
            meter_reading(133.5, Rejection.AMBIGUOUS, alternatives=(113.5,)),
            "123.456",
            "ambiguous",
            id="none_fits",
        ),
    ],
)
@pytest.mark.usefixtures("camera")
async def test_last_value_picks_among_readings(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    meter_entry: MockConfigEntry,
    mock_meter_reader: Mock,
    reading: Any,
    state: str,
    status: str,
) -> None:
    """Near a carry the wheels allow two values; the one that fits is taken."""
    await setup_integration(hass, meter_entry)

    mock_meter_reader.return_value = reading
    await _poll(hass, freezer)

    assert _state(hass) == state
    assert _state(hass, STATUS_ENTITY_ID) == status


@pytest.mark.usefixtures("camera")
async def test_jitter_holds_the_value(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    meter_entry: MockConfigEntry,
    mock_meter_reader: Mock,
) -> None:
    """A reading one unit lower is accepted but does not lower the total."""
    await setup_integration(hass, meter_entry)

    mock_meter_reader.return_value = meter_reading(123.4555)
    await _poll(hass, freezer)
    assert _state(hass) == "123.456"
    assert _state(hass, STATUS_ENTITY_ID) == "accepted"

    mock_meter_reader.return_value = meter_reading(123.47)
    await _poll(hass, freezer)
    assert _state(hass) == "123.47"


@pytest.mark.usefixtures("camera")
async def test_lower_value_needs_agreement(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    meter_entry: MockConfigEntry,
    mock_meter_reader: Mock,
) -> None:
    """A lower value is taken only once three readings in a row agree."""
    await setup_integration(hass, meter_entry)

    mock_meter_reader.return_value = meter_reading(120.0)
    for _ in range(2):
        await _poll(hass, freezer)
        assert _state(hass) == "123.456"
        assert _state(hass, STATUS_ENTITY_ID) == "decreased"
    await _poll(hass, freezer)
    assert _state(hass) == "120.0"
    assert _state(hass, STATUS_ENTITY_ID) == "accepted"


@pytest.mark.usefixtures("camera")
async def test_too_fast_is_rejected(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    meter_entry: MockConfigEntry,
    mock_meter_reader: Mock,
) -> None:
    """A rise faster than the maximum rate is a misread."""
    await setup_integration(hass, meter_entry)

    mock_meter_reader.return_value = meter_reading(124.456)
    await _poll(hass, freezer)

    assert _state(hass) == "123.456"
    assert _state(hass, STATUS_ENTITY_ID) == "too_fast"


@pytest.mark.usefixtures("camera")
async def test_long_gap_is_still_checked(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    meter_entry: MockConfigEntry,
    mock_meter_reader: Mock,
) -> None:
    """After a day without readings the allowed rise grows, but no further."""
    await setup_integration(hass, meter_entry)

    mock_meter_reader.return_value = meter_reading(5123.456)
    await _poll(hass, freezer, timedelta(hours=25))
    assert _state(hass) == "123.456"
    assert _state(hass, STATUS_ENTITY_ID) == "too_fast"

    mock_meter_reader.return_value = meter_reading(160.0)
    await _poll(hass, freezer)
    assert _state(hass) == "160.0"


@pytest.mark.usefixtures("camera")
async def test_first_misread_is_rejected(
    hass: HomeAssistant, meter_entry: MockConfigEntry, mock_meter_reader: Mock
) -> None:
    """The confirmed value checks the very first reading."""
    _with_options(
        hass,
        meter_entry,
        **{CONF_CONFIRMED: {"value": 123.0, "at": dt_util.utcnow().isoformat()}},
    )
    mock_meter_reader.return_value = meter_reading(923.456)
    await setup_integration(hass, meter_entry)

    assert _state(hass) == "123.0"
    assert _state(hass, STATUS_ENTITY_ID) == "too_fast"


@pytest.mark.usefixtures("camera")
async def test_restart_keeps_value_and_checks(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    freezer: FrozenDateTimeFactory,
    meter_entry: MockConfigEntry,
    mock_meter_reader: Mock,
) -> None:
    """After a restart the value is there at once and lower readings stay out."""
    await setup_integration(hass, meter_entry)
    await _flush(hass, freezer)
    stored = hass_storage[_key(meter_entry)]
    assert stored["version"] == STORAGE_VERSION
    assert stored["data"]["meter"]["value"] == 123.456
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


@pytest.mark.usefixtures("camera")
async def test_saves_are_delayed(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    freezer: FrozenDateTimeFactory,
    meter_entry: MockConfigEntry,
    mock_meter_reader: Mock,
) -> None:
    """Readings are saved together a few seconds later, not each at once."""
    await setup_integration(hass, meter_entry)
    await _flush(hass, freezer)
    mock_meter_reader.return_value = meter_reading(123.5)
    await _poll(hass, freezer)
    assert hass_storage[_key(meter_entry)]["data"]["meter"]["value"] == 123.456

    await _flush(hass, freezer)
    assert hass_storage[_key(meter_entry)]["data"]["meter"]["value"] == 123.5


@pytest.mark.usefixtures("camera", "mock_meter_reader")
async def test_store_from_version_1_is_migrated(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    meter_entry: MockConfigEntry,
) -> None:
    """The former highest value gives way to the plausibility state."""
    at = "2026-10-20T08:00:00+00:00"
    hass_storage[_key(meter_entry)] = {
        "version": 1,
        "minor_version": 1,
        "key": _key(meter_entry),
        "data": {
            "history": [1],
            "meter": {
                "value": 130.0,
                "trend": 2,
                "plausibility": {"value": 129.9995, "at": at},
            },
        },
    }
    await setup_integration(hass, meter_entry)

    stored = hass_storage[_key(meter_entry)]
    assert stored["version"] == STORAGE_VERSION
    assert stored["data"]["history"] == [1]
    meter = stored["data"]["meter"]
    assert (meter["trend"], meter["value"], meter["at"]) == (2, 129.9995, at)
    assert "plausibility" not in meter
    # The reader sees 123.456, lower than the migrated value.
    assert _state(hass) == "129.9995"
    assert _state(hass, STATUS_ENTITY_ID) == "decreased"


@pytest.mark.parametrize(
    "data",
    [
        pytest.param({"meter": {"value": 1.0, "plausibility": {"x": 1}}}, id="bad"),
        pytest.param({"meter": ["not", "a", "dict"]}, id="not_dict"),
        pytest.param(["not", "a", "dict"], id="no_data"),
    ],
)
@pytest.mark.usefixtures("camera", "mock_meter_reader")
async def test_invalid_version_1_store(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    meter_entry: MockConfigEntry,
    data: Any,
) -> None:
    """Unusable old data falls back to the value confirmed at setup."""
    hass_storage[_key(meter_entry)] = {
        "version": 1,
        "minor_version": 1,
        "key": _key(meter_entry),
        "data": data,
    }
    await setup_integration(hass, meter_entry)

    assert meter_entry.state is ConfigEntryState.LOADED
    assert _state(hass) == "123.456"


@pytest.mark.parametrize(
    "meter",
    [
        {"value": "high", "at": "2026-10-20T08:00:00+00:00"},
        {"value": True, "at": "2026-10-20T08:00:00+00:00"},
        {"value": 1.0, "at": "yesterday"},
        {"value": 1.0, "at": "2026-10-20T08:00:00"},
        {"value": 1.0, "at": 5},
    ],
)
@pytest.mark.usefixtures("camera", "mock_meter_reader")
async def test_invalid_stored_state(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    meter_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
    meter: dict[str, Any],
) -> None:
    """An unusable stored state falls back to the value confirmed at setup."""
    _store(hass_storage, meter_entry, {"meter": meter})
    await setup_integration(hass, meter_entry)

    assert _state(hass) == "123.456"
    assert "Ignoring invalid stored meter state" in caplog.text


@pytest.mark.usefixtures("camera", "mock_meter_reader")
async def test_newer_confirmed_value_wins(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    meter_entry: MockConfigEntry,
) -> None:
    """A value confirmed in a reconfigure flow replaces an older stored one."""
    _store(
        hass_storage,
        meter_entry,
        {"meter": {"value": 130.0, "at": "2026-09-01T00:00:00+00:00"}},
    )
    await setup_integration(hass, meter_entry)
    # From the stored 130.0 the reading would be lower and refused.
    assert _state(hass) == "123.456"


@pytest.mark.usefixtures("camera")
async def test_state_from_a_clock_ahead_is_repaired(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    freezer: FrozenDateTimeFactory,
    meter_entry: MockConfigEntry,
    mock_meter_reader: Mock,
) -> None:
    """A state saved two hours ahead does not hold back every reading."""
    ahead = (dt_util.utcnow() + timedelta(hours=2)).isoformat()
    _store(hass_storage, meter_entry, {"meter": {"value": 123.456, "at": ahead}})
    await setup_integration(hass, meter_entry)

    mock_meter_reader.return_value = meter_reading(123.5)
    await _poll(hass, freezer)
    assert _state(hass) == "123.5"


@pytest.mark.usefixtures("camera", "mock_meter_reader")
async def test_unknown_fields_survive(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    freezer: FrozenDateTimeFactory,
    meter_entry: MockConfigEntry,
) -> None:
    """Fields written by a newer version are kept on the next save."""
    _store(
        hass_storage,
        meter_entry,
        {
            "history": [1],
            "meter": {"value": 100.0, "at": "2026-10-20T00:00:00+00:00", "trend": 2},
        },
    )
    await setup_integration(hass, meter_entry)
    await _flush(hass, freezer)

    data = hass_storage[_key(meter_entry)]["data"]
    assert data["history"] == [1]
    assert data["meter"]["trend"] == 2
    assert data["meter"]["value"] == 123.456


@pytest.mark.usefixtures("camera")
async def test_unconfirmed_meter_asks_for_the_value(
    hass: HomeAssistant,
    issue_registry: ir.IssueRegistry,
    freezer: FrozenDateTimeFactory,
    meter_entry: MockConfigEntry,
    mock_meter_reader: Mock,
) -> None:
    """Without any confirmed value nothing is taken and a repair asks for it."""
    _with_options(hass, meter_entry, **{CONF_CONFIRMED: None})
    await setup_integration(hass, meter_entry)

    assert _state(hass) == STATE_UNKNOWN
    assert _state(hass, STATUS_ENTITY_ID) == "unconfirmed"
    issue_id = f"unconfirmed_{meter_entry.entry_id}"
    issue = issue_registry.async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.translation_placeholders == {"name": "Water meter"}

    result = await hass.config_entries.options.async_init(meter_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"value": 123.4}
    )
    assert _state(hass) == "123.4"
    assert issue_registry.async_get_issue(DOMAIN, issue_id) is None

    await _poll(hass, freezer)
    assert _state(hass) == "123.456"


@pytest.mark.usefixtures("camera", "mock_meter_reader")
async def test_removing_the_entry_removes_the_issue(
    hass: HomeAssistant, issue_registry: ir.IssueRegistry, meter_entry: MockConfigEntry
) -> None:
    """The repair goes with the helper."""
    _with_options(hass, meter_entry, **{CONF_CONFIRMED: None})
    await setup_integration(hass, meter_entry)
    await hass.config_entries.async_remove(meter_entry.entry_id)

    issue_id = f"unconfirmed_{meter_entry.entry_id}"
    assert issue_registry.async_get_issue(DOMAIN, issue_id) is None


@pytest.mark.usefixtures("camera")
async def test_counter_wrap_counts_on(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    meter_entry: MockConfigEntry,
    mock_meter_reader: Mock,
) -> None:
    """From 99999.999 to 00000.050 the total rises by 0.051."""
    at = (dt_util.utcnow() - timedelta(hours=1)).isoformat()
    _store(hass_storage, meter_entry, {"meter": {"value": 99999.999, "at": at}})
    mock_meter_reader.return_value = meter_reading(0.05)
    await setup_integration(hass, meter_entry)

    assert float(_state(hass)) == pytest.approx(100000.05, abs=1e-6)
    assert hass.states.get(STATUS_ENTITY_ID).attributes["candidate"] == 0.05


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
    _with_options(hass, meter_entry, **{CONF_CALIBRATION: None})
    await setup_integration(hass, meter_entry)

    assert _state(hass) == "123.456"
    assert mock_meter_reader.call_args.args[2] == Calibration()


@pytest.mark.usefixtures("camera")
async def test_reader_runs_in_the_executor(
    hass: HomeAssistant, meter_entry: MockConfigEntry
) -> None:
    """Loading and reading block, so they stay off the event loop."""
    loop_thread = threading.get_ident()
    threads: dict[str, int] = {}

    def load(_reader: MeterReader) -> None:
        threads["load"] = threading.get_ident()

    def read(_reader: MeterReader, *args: Any) -> Any:
        threads["read"] = threading.get_ident()
        return meter_reading(123.456)

    with (
        patch.object(MeterReader, "load", load),
        patch.object(MeterReader, "read", read),
    ):
        await setup_integration(hass, meter_entry)

    assert _state(hass) == "123.456"
    assert set(threads) == {"load", "read"}
    assert loop_thread not in threads.values()


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


@pytest.mark.parametrize(
    ("changes", "detail"),
    [
        pytest.param({CONF_MAX_RATE: 0}, "max_rate must be positive", id="rate"),
        pytest.param(
            {CONF_CALIBRATION: {"offset": 0, "width": 5, "height": 1}},
            "invalid calibration",
            id="calibration",
        ),
    ],
)
@pytest.mark.usefixtures("camera", "mock_meter_reader")
async def test_invalid_settings_fail_setup(
    hass: HomeAssistant,
    meter_entry: MockConfigEntry,
    changes: dict[str, Any],
    detail: str,
) -> None:
    """Settings tarsius refuses fail the setup with a clear reason."""
    _with_options(hass, meter_entry, **changes)
    await setup_integration(hass, meter_entry)

    assert meter_entry.state is ConfigEntryState.SETUP_ERROR
    assert meter_entry.reason.startswith("The meter settings are invalid: ")
    assert detail in meter_entry.reason


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
    stored = hass_storage[_key(meter_entry)]["data"]["meter"]
    assert stored["value"] == 100.0

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


@pytest.mark.usefixtures("camera", "mock_meter_reader")
async def test_store_from_version_3_is_migrated(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    meter_entry: MockConfigEntry,
) -> None:
    """A flag and bare misread offsets give way, the rest is tarsius' state."""
    at = "2026-10-20T08:00:00+00:00"
    hass_storage[_key(meter_entry)] = {
        "version": 3,
        "minor_version": 1,
        "key": _key(meter_entry),
        "data": {
            "meter": {
                "value": 123.0,
                "at": at,
                "alternative": True,
                "pending": [[120.0, at]],
                "misreads": [10.0],
                "dispute": None,
            }
        },
    }
    await setup_integration(hass, meter_entry)

    meter = hass_storage[_key(meter_entry)]["data"]["meter"]
    assert "alternative" not in meter
    assert "misreads" not in meter
    assert meter["pending"] == [[120.0, at]]
    assert _state(hass) == "123.456"


@pytest.mark.usefixtures("camera", "mock_meter_reader")
async def test_exposure_of_another_calibration_is_not_used(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    meter_entry: MockConfigEntry,
) -> None:
    """Exposure learnt on an older calibration starts afresh."""
    _store(
        hass_storage,
        meter_entry,
        {
            "exposure": {
                "source": "elsewhere",
                "light": 100.0,
                "baseline": [[1, 0, 0, 0, 1]],
            }
        },
    )
    await setup_integration(hass, meter_entry)
    assert meter_entry.runtime_data.session.exposure is None


@pytest.mark.parametrize(
    "detail",
    [
        {"pending": [[1.0]]},
        {"dispute": {"since": "x"}},
        {"offset": "far"},
        {"misreads": [10.0]},
    ],
)
@pytest.mark.usefixtures("camera", "mock_meter_reader")
async def test_damaged_detail_keeps_the_stored_value(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    meter_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
    detail: dict[str, Any],
) -> None:
    """One damaged detail drops the details, not the newer stored value."""
    at = (dt_util.utcnow() - timedelta(hours=1)).isoformat()
    _store(hass_storage, meter_entry, {"meter": {"value": 130.0, "at": at, **detail}})
    await setup_integration(hass, meter_entry)

    # The stored 130.0 wins over the older confirmed 123.0: 123.456 is lower.
    assert _state(hass) == "130.0"
    assert "Keeping only the value of a damaged meter state" in caplog.text


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_cached_snapshot_cannot_confirm_a_rise(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    camera: FakeCamera,
    meter_entry: MockConfigEntry,
    mock_meter_reader: Mock,
) -> None:
    """An unsure rise of a step waits; the same bytes again are not read."""
    await setup_integration(hass, meter_entry)
    camera.live = False
    mock_meter_reader.return_value = meter_reading(123.6, confidence=0.6)
    await _poll(hass, freezer)
    assert _state(hass) == "123.456"
    assert _state(hass, STATUS_ENTITY_ID) == "rise_unconfirmed"
    reads = mock_meter_reader.call_count

    await _poll(hass, freezer)
    assert mock_meter_reader.call_count == reads
    assert _state(hass) == "123.456"
    assert _state(hass, STATUS_ENTITY_ID) == "rise_unconfirmed"

    camera.live = True
    mock_meter_reader.return_value = meter_reading(123.61, confidence=0.6)
    await _poll(hass, freezer)
    assert _state(hass) == "123.61"
