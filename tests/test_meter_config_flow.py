"""Tests for the Local vision meter config flow."""

from datetime import datetime, timedelta
from typing import Any
from unittest.mock import ANY, AsyncMock, Mock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from tarsius import BackendError, BlankFrameError, Roi, outline
from tarsius.meter import Calibration, Rejection
from tarsius.meter.finder import Suggestion

from custom_components.local_vision import preview
from custom_components.local_vision.const import (
    CONF_BOTTOM,
    CONF_CALIBRATION,
    CONF_CONFIRMED,
    CONF_COUNTERCLOCKWISE,
    CONF_DECIMAL_WHEELS,
    CONF_DIAL_COUNT,
    CONF_DIALS,
    CONF_DIGITS,
    CONF_LEFT,
    CONF_MAX_RATE,
    CONF_RIGHT,
    CONF_ROTATION,
    CONF_TOP,
    CONF_TYPE,
    CONF_UNIT,
    CONF_VALUE,
    CONF_WHEELS,
    DOMAIN,
    TYPE_METER,
)
from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_DEVICE_CLASS, CONF_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult, FlowResultType
from homeassistant.util import dt as dt_util

from . import CAMERA, DIAL, DIGITS, METER_INPUT, FakeCamera, meter_reading

DIGITS_INPUT = {
    **DIGITS,
    CONF_ROTATION: 0,
    CONF_WHEELS: 8,
    CONF_DECIMAL_WHEELS: 3,
    CONF_DIAL_COUNT: 0,
}
DIAL_INPUT = {**DIAL, CONF_COUNTERCLOCKWISE: False}


async def _start(hass: HomeAssistant) -> str:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["menu_options"] == ["state", "meter"]
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "meter"}
    )
    assert result["step_id"] == "meter"
    return result["flow_id"]


async def _to_regions(hass: HomeAssistant, unit: dict[str, Any] | None = None) -> str:
    flow_id = await _start(hass)
    result = await hass.config_entries.flow.async_configure(flow_id, METER_INPUT)
    assert result["step_id"] == "meter_unit"
    result = await hass.config_entries.flow.async_configure(
        flow_id, unit or {CONF_UNIT: "m³"}
    )
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "meter_regions"
    # Finding the counter automatically comes first.
    assert result["menu_options"] == ["suggest", "digits"]
    return flow_id


async def _choose(hass: HomeAssistant, flow_id: str, step: str) -> FlowResult:
    return await hass.config_entries.flow.async_configure(
        flow_id, {"next_step_id": step}
    )


async def _confirm(
    hass: HomeAssistant, flow_id: str, value: float = 123.4
) -> FlowResult:
    """Use the regions and confirm the value on the meter."""
    result = await _choose(hass, flow_id, "meter_value")
    assert result["step_id"] == "meter_value"
    return await hass.config_entries.flow.async_configure(flow_id, {CONF_VALUE: value})


def _suggested(result: FlowResult, key: str) -> Any:
    for field in result["data_schema"].schema:
        if field == key:
            return field.description["suggested_value"]
    raise KeyError(key)


def _times(factor: str) -> str:
    """Return a dial factor as the flow shows it, with the multiplication sign."""
    return f"\N{MULTIPLICATION SIGN}{factor}"


def _field(result: FlowResult, key: str) -> Any:
    return next(field for field in result["data_schema"].schema if field == key)


@pytest.mark.usefixtures("camera")
async def test_meter_flow(
    hass: HomeAssistant, mock_setup_entry: AsyncMock, mock_meter_reader: Mock
) -> None:
    """Pick a camera and unit, set the wheels, check the reading."""
    flow_id = await _to_regions(hass)
    result = await _choose(hass, flow_id, "digits")
    assert result["step_id"] == "digits"

    result = await hass.config_entries.flow.async_configure(flow_id, DIGITS_INPUT)
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "meter_confirm"
    assert result["menu_options"] == ["meter_value", "digits"]
    assert result["description_placeholders"]["reading"] == "123.456 (Accepted)"
    assert result["description_placeholders"]["preview"].startswith(
        "/api/local_vision/preview/"
    )

    result = await _choose(hass, flow_id, "meter_value")
    assert result["type"] is FlowResultType.FORM
    assert _suggested(result, CONF_VALUE) == 123.456
    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_VALUE: 123.4}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Water meter"
    assert result["description"] == "meter"
    assert result["options"] == {
        CONF_TYPE: TYPE_METER,
        CONF_ENTITY_ID: CAMERA,
        CONF_DEVICE_CLASS: "water",
        CONF_UNIT: "m³",
        CONF_MAX_RATE: 4,
        CONF_DIGITS: DIGITS,
        CONF_ROTATION: 0,
        CONF_WHEELS: 8,
        CONF_DECIMAL_WHEELS: 3,
        CONF_DIALS: [],
        CONF_CALIBRATION: Calibration().as_dict(),
        CONF_CONFIRMED: {CONF_VALUE: 123.4, "at": ANY},
    }
    confirmed_at = datetime.fromisoformat(result["options"][CONF_CONFIRMED]["at"])
    assert abs(confirmed_at - dt_util.utcnow()) < timedelta(minutes=1)
    assert len(mock_setup_entry.mock_calls) == 1


@pytest.mark.usefixtures("camera", "mock_setup_entry")
async def test_meter_flow_dials(hass: HomeAssistant, mock_meter_reader: Mock) -> None:
    """Each dial gets a form; going back keeps them as suggestions."""
    flow_id = await _to_regions(hass, {CONF_UNIT: "L", CONF_MAX_RATE: 3000})
    await _choose(hass, flow_id, "digits")
    result = await hass.config_entries.flow.async_configure(
        flow_id, {**DIGITS_INPUT, CONF_DECIMAL_WHEELS: 0, CONF_DIAL_COUNT: 2}
    )
    assert result["step_id"] == "dial"
    assert result["description_placeholders"]["number"] == "1"
    assert result["description_placeholders"]["count"] == "2"
    first = {**DIAL_INPUT, CONF_COUNTERCLOCKWISE: True}
    result = await hass.config_entries.flow.async_configure(flow_id, first)
    assert result["description_placeholders"]["number"] == "2"
    second = {**DIAL_INPUT, CONF_LEFT: 40, CONF_RIGHT: 50}
    result = await hass.config_entries.flow.async_configure(flow_id, second)
    assert result["step_id"] == "meter_confirm"

    result = await _choose(hass, flow_id, "digits")
    assert _suggested(result, CONF_LEFT) == DIGITS[CONF_LEFT]
    assert _suggested(result, CONF_DIAL_COUNT) == 2
    result = await hass.config_entries.flow.async_configure(
        flow_id, {**DIGITS_INPUT, CONF_DIAL_COUNT: 1}
    )
    assert _suggested(result, CONF_COUNTERCLOCKWISE) is True
    await hass.config_entries.flow.async_configure(flow_id, first)
    result = await _confirm(hass, flow_id)

    assert result["options"][CONF_UNIT] == "L"
    assert result["options"][CONF_MAX_RATE] == 3000
    assert result["options"][CONF_DIALS] == [first]


@pytest.mark.usefixtures("camera", "mock_setup_entry", "mock_meter_reader")
@pytest.mark.parametrize(
    ("user_input", "error"),
    [
        pytest.param(
            {**DIGITS_INPUT, CONF_LEFT: 80}, "invalid_horizontal_range", id="left"
        ),
        pytest.param(
            {**DIGITS_INPUT, CONF_BOTTOM: 30}, "invalid_vertical_range", id="height"
        ),
        pytest.param(
            {**DIGITS_INPUT, CONF_DECIMAL_WHEELS: 9},
            "too_many_decimal_wheels",
            id="dec",
        ),
    ],
)
async def test_digits_refused(
    hass: HomeAssistant, user_input: dict[str, Any], error: str
) -> None:
    """Impossible wheel settings are refused, valid ones recover."""
    flow_id = await _to_regions(hass)
    await _choose(hass, flow_id, "digits")
    result = await hass.config_entries.flow.async_configure(flow_id, user_input)
    assert result["step_id"] == "digits"
    assert result["errors"] == {"base": error}

    result = await hass.config_entries.flow.async_configure(flow_id, DIGITS_INPUT)
    assert result["step_id"] == "meter_confirm"


@pytest.mark.usefixtures("camera", "mock_setup_entry", "mock_meter_reader")
async def test_dial_refused(hass: HomeAssistant) -> None:
    """An empty dial region is refused."""
    flow_id = await _to_regions(hass)
    await _choose(hass, flow_id, "digits")
    await hass.config_entries.flow.async_configure(
        flow_id, {**DIGITS_INPUT, CONF_DIAL_COUNT: 1}
    )
    result = await hass.config_entries.flow.async_configure(
        flow_id, {**DIAL_INPUT, CONF_TOP: 90}
    )
    assert result["errors"] == {"base": "invalid_vertical_range"}

    result = await hass.config_entries.flow.async_configure(flow_id, DIAL_INPUT)
    assert result["step_id"] == "meter_confirm"


@pytest.mark.usefixtures("mock_setup_entry", "mock_meter_reader")
@pytest.mark.parametrize(
    ("frame", "error"),
    [
        pytest.param(None, "cannot_connect", id="no_frame"),
        pytest.param(b"<html>502</html>", "invalid_frame", id="undecodable"),
    ],
)
async def test_meter_camera_errors(
    hass: HomeAssistant, camera: FakeCamera, frame: bytes | None, error: str
) -> None:
    """A camera without a usable frame shows an error until it answers."""
    good, camera.frame = camera.frame, frame
    flow_id = await _start(hass)
    result = await hass.config_entries.flow.async_configure(flow_id, METER_INPUT)
    assert result["errors"] == {"base": error}

    camera.frame = good
    result = await hass.config_entries.flow.async_configure(flow_id, METER_INPUT)
    assert result["step_id"] == "meter_unit"


@pytest.mark.usefixtures("camera", "mock_setup_entry")
async def test_suggest_regions(
    hass: HomeAssistant, mock_meter_reader: Mock, mock_finder: Mock
) -> None:
    """The alpha finder fills in the regions and the rotation."""
    mock_finder.return_value = Suggestion(
        rotation=4.04,
        digits=Roi(0.3, 0.4, 0.7, 0.5),
        digits_score=0.8,
        dials=(Roi(0.6, 0.6, 0.7, 0.8),),
        dial_scores=(0.7,),
    )
    flow_id = await _to_regions(hass)
    result = await _choose(hass, flow_id, "suggest")

    assert result["step_id"] == "digits"
    assert result["errors"] == {}
    assert _suggested(result, CONF_LEFT) == 30
    assert _suggested(result, CONF_BOTTOM) == 50
    assert _suggested(result, CONF_ROTATION) == 4.0
    assert _suggested(result, CONF_DIAL_COUNT) == 1

    result = await hass.config_entries.flow.async_configure(
        flow_id, {**DIGITS_INPUT, CONF_ROTATION: 4.0, CONF_DIAL_COUNT: 1}
    )
    assert result["step_id"] == "dial_order"
    result = await hass.config_entries.flow.async_configure(flow_id, {})
    assert _suggested(result, CONF_LEFT) == 60
    assert _suggested(result, CONF_COUNTERCLOCKWISE) is False
    await hass.config_entries.flow.async_configure(flow_id, DIAL_INPUT)
    result = await _confirm(hass, flow_id)
    assert result["options"][CONF_ROTATION] == 4.0
    assert result["options"][CONF_DIALS] == [DIAL_INPUT]


@pytest.mark.usefixtures("camera", "mock_setup_entry", "mock_meter_reader")
async def test_previews_highlight_the_edited_region(
    hass: HomeAssistant, mock_finder: Mock
) -> None:
    """The preview marks the region in the form apart from the set ones."""
    counter = Roi(0.3, 0.4, 0.7, 0.5)
    first, second = Roi(0.6, 0.6, 0.7, 0.8), Roi(0.2, 0.6, 0.3, 0.8)
    mock_finder.return_value = Suggestion(
        digits=counter,
        digits_score=0.8,
        dials=(first, second),
        dial_scores=(0.7, 0.7),
    )
    flow_id = await _to_regions(hass)
    with patch("custom_components.local_vision.preview.outline", wraps=outline) as spy:
        await _choose(hass, flow_id, "suggest")
        assert spy.call_args.args[1] == []
        assert spy.call_args.kwargs["highlight"] == counter

        await hass.config_entries.flow.async_configure(
            flow_id, {**DIGITS_INPUT, CONF_DIAL_COUNT: 2}
        )
        # The order step outlines every found dial, none highlighted.
        assert spy.call_args.args[1] == [counter, first, second]
        assert spy.call_args.kwargs["highlight"] is None

        await hass.config_entries.flow.async_configure(flow_id, {})
        assert spy.call_args.args[1] == [counter]
        assert spy.call_args.kwargs["highlight"] == first

        await hass.config_entries.flow.async_configure(flow_id, DIAL_INPUT)
        assert spy.call_args.args[1] == [counter, first]
        assert spy.call_args.kwargs["highlight"] == second

        # A region that cannot be drawn is not highlighted.
        await hass.config_entries.flow.async_configure(
            flow_id, {**DIAL_INPUT, CONF_TOP: 90}
        )
        assert spy.call_args.kwargs["highlight"] is None


@pytest.mark.usefixtures("camera", "mock_setup_entry", "mock_meter_reader")
@pytest.mark.parametrize(
    "outcome",
    [
        pytest.param({"return_value": Suggestion()}, id="nothing_found"),
        pytest.param({"side_effect": BackendError("model missing")}, id="failure"),
    ],
)
async def test_suggest_nothing(
    hass: HomeAssistant, mock_finder: Mock, outcome: dict[str, Any]
) -> None:
    """Without a suggestion the regions are entered by hand."""
    mock_finder.configure_mock(**outcome)
    flow_id = await _to_regions(hass)
    result = await _choose(hass, flow_id, "suggest")

    assert result["step_id"] == "digits"
    assert result["errors"] == {"base": "no_suggestion"}
    result = await hass.config_entries.flow.async_configure(flow_id, DIGITS_INPUT)
    assert result["step_id"] == "meter_confirm"


@pytest.mark.usefixtures("camera", "mock_setup_entry")
@pytest.mark.parametrize(
    ("outcome", "reading", "suggested", "calibrated"),
    [
        pytest.param(
            {"return_value": meter_reading(12.5, Rejection.LOW_CONFIDENCE)},
            "12.5 (Not sure)",
            None,
            True,
            id="unsure",
        ),
        pytest.param(
            {"side_effect": BlankFrameError("dark")},
            "The camera shows nothing",
            None,
            False,
            id="blank",
        ),
        pytest.param(
            {"side_effect": BackendError("broken")},
            "Unexpected error",
            None,
            False,
            id="model",
        ),
    ],
)
async def test_confirm_without_reading(
    hass: HomeAssistant,
    mock_meter_reader: Mock,
    outcome: dict[str, Any],
    reading: str,
    suggested: float | None,
    calibrated: bool,
) -> None:
    """The regions can be kept even when the meter can't be read right now."""
    mock_meter_reader.configure_mock(**outcome)
    flow_id = await _to_regions(hass)
    await _choose(hass, flow_id, "digits")
    result = await hass.config_entries.flow.async_configure(flow_id, DIGITS_INPUT)
    assert result["description_placeholders"]["reading"] == reading

    result = await _choose(hass, flow_id, "meter_value")
    field = next(iter(result["data_schema"].schema))
    assert (field.description or {}).get("suggested_value") == suggested
    result = await hass.config_entries.flow.async_configure(flow_id, {CONF_VALUE: 12.0})
    assert result["options"][CONF_CONFIRMED][CONF_VALUE] == 12.0
    assert (CONF_CALIBRATION in result["options"]) is calibrated


@pytest.mark.usefixtures("camera", "mock_setup_entry", "mock_meter_reader")
async def test_rotated_meter(hass: HomeAssistant) -> None:
    """A turned meter shows a turned preview and keeps its rotation."""
    flow_id = await _to_regions(hass)
    await _choose(hass, flow_id, "digits")
    result = await hass.config_entries.flow.async_configure(
        flow_id, {**DIGITS_INPUT, CONF_ROTATION: -3.5}
    )
    assert result["description_placeholders"]["preview"].startswith(
        "/api/local_vision/preview/"
    )

    result = await _confirm(hass, flow_id)
    assert result["options"][CONF_ROTATION] == -3.5


@pytest.mark.usefixtures("camera", "mock_setup_entry", "mock_meter_reader")
async def test_max_rate_must_be_positive(hass: HomeAssistant) -> None:
    """A maximum increase of 0 would reject every reading."""
    flow_id = await _start(hass)
    await hass.config_entries.flow.async_configure(flow_id, METER_INPUT)
    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_UNIT: "m³", CONF_MAX_RATE: 0}
    )
    assert result["step_id"] == "meter_unit"
    assert result["errors"] == {CONF_MAX_RATE: "invalid_max_rate"}

    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_UNIT: "m³", CONF_MAX_RATE: 2.5}
    )
    assert result["step_id"] == "meter_regions"


@pytest.mark.usefixtures("camera", "mock_setup_entry", "mock_meter_reader")
@pytest.mark.parametrize(
    ("device_class", "units", "max_rate"),
    [
        ("water", ["m³", "L", "gal", "ft³", "CCF", "MCF"], 4),
        ("gas", ["m³", "ft³", "CCF", "MCF", "L"], 6),
        ("energy", ["kWh", "Wh", "MWh"], 50),
    ],
)
async def test_units_and_default_rate(
    hass: HomeAssistant, device_class: str, units: list[str], max_rate: float
) -> None:
    """Each kind offers the units its device class allows; gas has no kWh."""
    flow_id = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        flow_id, {**METER_INPUT, CONF_DEVICE_CLASS: device_class}
    )
    unit = next(iter(result["data_schema"].schema))
    selector = result["data_schema"].schema[unit]
    assert selector.config["options"] == units

    await hass.config_entries.flow.async_configure(flow_id, {CONF_UNIT: units[0]})
    await _choose(hass, flow_id, "digits")
    await hass.config_entries.flow.async_configure(flow_id, DIGITS_INPUT)
    result = await _confirm(hass, flow_id)
    assert result["options"][CONF_MAX_RATE] == max_rate


async def _reconfigure(hass: HomeAssistant, entry: MockConfigEntry) -> FlowResult:
    entry.add_to_hass(hass)
    result = await entry.start_reconfigure_flow(hass)
    assert result["step_id"] == "meter_unit"
    assert _suggested(result, CONF_UNIT) == "m³"
    assert _suggested(result, CONF_MAX_RATE) == 4.0
    return result


@pytest.mark.usefixtures("camera", "mock_meter_reader")
async def test_reconfigure_keeps_the_value(
    hass: HomeAssistant, meter_entry: MockConfigEntry
) -> None:
    """New regions on the same counter keep the value and its checks."""
    result = await _reconfigure(hass, meter_entry)
    flow_id = result["flow_id"]
    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_UNIT: "m³", CONF_MAX_RATE: 3}
    )
    result = await _choose(hass, flow_id, "digits")
    assert _suggested(result, CONF_LEFT) == DIGITS[CONF_LEFT]
    assert _suggested(result, CONF_WHEELS) == 8
    result = await hass.config_entries.flow.async_configure(
        flow_id, {**DIGITS_INPUT, CONF_ROTATION: 2.5, CONF_LEFT: 31}
    )
    assert result["menu_options"] == ["meter_finish", "digits"]
    confirmed = meter_entry.options[CONF_CONFIRMED]

    result = await _choose(hass, flow_id, "meter_finish")
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert meter_entry.options[CONF_MAX_RATE] == 3
    assert meter_entry.options[CONF_ROTATION] == 2.5
    assert meter_entry.options[CONF_DIGITS][CONF_LEFT] == 31
    assert meter_entry.options[CONF_CONFIRMED] == confirmed
    assert meter_entry.options[CONF_CALIBRATION] == Calibration().as_dict()


@pytest.mark.usefixtures("camera", "mock_meter_reader")
@pytest.mark.parametrize(
    ("unit", "digits"),
    [
        pytest.param("m³", {**DIGITS_INPUT, CONF_DECIMAL_WHEELS: 2}, id="decimals"),
        pytest.param("m³", {**DIGITS_INPUT, CONF_WHEELS: 7}, id="wheels"),
        pytest.param("m³", {**DIGITS_INPUT, CONF_DIAL_COUNT: 1}, id="dial"),
        pytest.param("L", DIGITS_INPUT, id="unit"),
    ],
)
async def test_reconfigure_with_another_scale_asks_the_value(
    hass: HomeAssistant,
    meter_entry: MockConfigEntry,
    unit: str,
    digits: dict[str, Any],
) -> None:
    """When the counter reads another number, the value is confirmed again."""
    result = await _reconfigure(hass, meter_entry)
    flow_id = result["flow_id"]
    await hass.config_entries.flow.async_configure(flow_id, {CONF_UNIT: unit})
    await _choose(hass, flow_id, "digits")
    result = await hass.config_entries.flow.async_configure(flow_id, digits)
    if result["step_id"] == "dial":
        result = await hass.config_entries.flow.async_configure(flow_id, DIAL_INPUT)
    assert result["menu_options"] == ["meter_value", "digits"]

    result = await _confirm(hass, flow_id, 1234.5)
    assert result["reason"] == "reconfigure_successful"
    assert meter_entry.options[CONF_CONFIRMED][CONF_VALUE] == 1234.5
    assert meter_entry.options[CONF_UNIT] == unit


@pytest.mark.usefixtures("camera")
async def test_reconfigure_state_helper(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """The region of a state helper stays with its taught frames."""
    mock_config_entry.add_to_hass(hass)
    result = await mock_config_entry.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_state"


@pytest.mark.parametrize(
    ("frame", "reason"),
    [
        pytest.param(None, "cannot_connect", id="no_frame"),
        pytest.param(b"<html>502</html>", "invalid_frame", id="undecodable"),
    ],
)
async def test_reconfigure_without_frame(
    hass: HomeAssistant,
    camera: FakeCamera,
    meter_entry: MockConfigEntry,
    frame: bytes | None,
    reason: str,
) -> None:
    """Regions are set on a frame; without one there is nothing to show."""
    camera.frame = frame
    meter_entry.add_to_hass(hass)
    result = await meter_entry.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == reason


@pytest.mark.usefixtures("camera", "mock_meter_reader")
async def test_reconfigure_broken_settings(
    hass: HomeAssistant, meter_entry: MockConfigEntry
) -> None:
    """Settings that failed the setup are fixed and the value confirmed again."""
    meter_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        meter_entry, options={**meter_entry.options, CONF_WHEELS: 13}
    )
    result = await _reconfigure(hass, meter_entry)
    flow_id = result["flow_id"]
    await hass.config_entries.flow.async_configure(flow_id, {CONF_UNIT: "m³"})
    await _choose(hass, flow_id, "digits")
    result = await hass.config_entries.flow.async_configure(flow_id, DIGITS_INPUT)
    assert result["menu_options"] == ["meter_value", "digits"]

    result = await _confirm(hass, flow_id)
    assert meter_entry.options[CONF_WHEELS] == 8


@pytest.mark.usefixtures("camera", "mock_meter_reader")
@pytest.mark.parametrize("loaded", [False, True])
async def test_reconfigure_without_a_value_asks_for_it(
    hass: HomeAssistant, meter_entry: MockConfigEntry, loaded: bool
) -> None:
    """A meter that never had a confirmed value gets one, scale or not."""
    meter_entry.add_to_hass(hass)
    options = {k: v for k, v in meter_entry.options.items() if k != CONF_CONFIRMED}
    hass.config_entries.async_update_entry(meter_entry, options=options)
    if loaded:
        await hass.config_entries.async_setup(meter_entry.entry_id)
        await hass.async_block_till_done()
        assert meter_entry.runtime_data.value is None
    result = await _reconfigure(hass, meter_entry)
    flow_id = result["flow_id"]
    await hass.config_entries.flow.async_configure(flow_id, {CONF_UNIT: "m³"})
    await _choose(hass, flow_id, "digits")
    result = await hass.config_entries.flow.async_configure(flow_id, DIGITS_INPUT)
    assert result["menu_options"] == ["meter_value", "digits"]

    result = await _confirm(hass, flow_id, 123.4)
    assert meter_entry.options[CONF_CONFIRMED][CONF_VALUE] == 123.4


@pytest.mark.usefixtures("camera", "mock_meter_reader")
async def test_reconfigure_loaded_meter_keeps_its_value(
    hass: HomeAssistant, meter_entry: MockConfigEntry
) -> None:
    """A loaded meter with a value keeps it on the same scale."""
    meter_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(meter_entry.entry_id)
    await hass.async_block_till_done()
    result = await _reconfigure(hass, meter_entry)
    flow_id = result["flow_id"]
    await hass.config_entries.flow.async_configure(flow_id, {CONF_UNIT: "m³"})
    await _choose(hass, flow_id, "digits")
    result = await hass.config_entries.flow.async_configure(flow_id, DIGITS_INPUT)
    assert result["menu_options"] == ["meter_finish", "digits"]


FIRST_BOX, SECOND_BOX = Roi(0.6, 0.6, 0.7, 0.8), Roi(0.2, 0.6, 0.3, 0.8)
FIRST_DIAL = {
    CONF_LEFT: 60,
    CONF_TOP: 60,
    CONF_RIGHT: 70,
    CONF_BOTTOM: 80,
    CONF_COUNTERCLOCKWISE: False,
}
SECOND_DIAL = {**FIRST_DIAL, CONF_LEFT: 20, CONF_RIGHT: 30}


async def _to_dial_order(
    hass: HomeAssistant,
    mock_finder: Mock,
    boxes: tuple[Roi, ...],
    dial_count: int,
    decimal_wheels: int = 0,
) -> FlowResult:
    """Suggest the boxes and confirm the counter with the dial count."""
    mock_finder.return_value = Suggestion(
        digits=Roi(0.3, 0.4, 0.7, 0.5),
        digits_score=0.8,
        dials=boxes,
        dial_scores=(0.7,) * len(boxes),
    )
    flow_id = await _to_regions(hass)
    await _choose(hass, flow_id, "suggest")
    return await hass.config_entries.flow.async_configure(
        flow_id,
        {
            **DIGITS_INPUT,
            CONF_DECIMAL_WHEELS: decimal_wheels,
            CONF_DIAL_COUNT: dial_count,
        },
    )


@pytest.mark.usefixtures("camera", "mock_setup_entry", "mock_meter_reader")
async def test_dial_order_defaults_to_the_suggestion(
    hass: HomeAssistant, mock_finder: Mock
) -> None:
    """The found boxes are lettered and assigned in the finder's order."""
    result = await _to_dial_order(hass, mock_finder, (FIRST_BOX, SECOND_BOX), 2)
    flow_id = result["flow_id"]
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "dial_order"
    assert result["errors"] == {}
    placeholders = result["description_placeholders"]
    assert placeholders["factor_1"] == _times("0.1")
    assert placeholders["factor_2"] == _times("0.01")
    assert placeholders["preview"].startswith("/api/local_vision/preview/")
    for key, default in (("dial_1", "a"), ("dial_2", "b")):
        field = _field(result, key)
        assert field.default() == default
        selector = result["data_schema"].schema[field]
        assert selector.config["options"] == ["a", "b", "manual"]
        assert selector.config["translation_key"] == "dial_box"

    result = await hass.config_entries.flow.async_configure(flow_id, {})
    assert result["step_id"] == "dial"
    assert result["description_placeholders"]["factor"] == _times("0.1")
    assert _suggested(result, CONF_LEFT) == 60
    result = await hass.config_entries.flow.async_configure(flow_id, FIRST_DIAL)
    assert result["description_placeholders"]["factor"] == _times("0.01")
    assert _suggested(result, CONF_LEFT) == 20
    await hass.config_entries.flow.async_configure(flow_id, SECOND_DIAL)
    result = await _confirm(hass, flow_id)
    assert result["options"][CONF_DIALS] == [FIRST_DIAL, SECOND_DIAL]


@pytest.mark.usefixtures("camera", "mock_setup_entry", "mock_meter_reader")
async def test_dial_order_reorders_the_dials(
    hass: HomeAssistant, mock_finder: Mock
) -> None:
    """A box chosen for another dial is prefilled there and stays adjustable."""
    result = await _to_dial_order(hass, mock_finder, (FIRST_BOX, SECOND_BOX), 2)
    flow_id = result["flow_id"]
    result = await hass.config_entries.flow.async_configure(
        flow_id, {"dial_1": "b", "dial_2": "a"}
    )
    assert _suggested(result, CONF_LEFT) == 20
    turned = {**SECOND_DIAL, CONF_COUNTERCLOCKWISE: True}
    result = await hass.config_entries.flow.async_configure(flow_id, turned)
    assert _suggested(result, CONF_LEFT) == 60
    moved = {**FIRST_DIAL, CONF_RIGHT: 71}
    await hass.config_entries.flow.async_configure(flow_id, moved)
    result = await _confirm(hass, flow_id)
    assert result["options"][CONF_DIALS] == [turned, moved]


@pytest.mark.usefixtures("camera", "mock_setup_entry", "mock_meter_reader")
async def test_dial_order_refuses_a_box_twice(
    hass: HomeAssistant, mock_finder: Mock
) -> None:
    """One box can't be two dials; the choice is kept to correct it."""
    result = await _to_dial_order(hass, mock_finder, (FIRST_BOX, SECOND_BOX), 2)
    flow_id = result["flow_id"]
    twice = {"dial_1": "a", "dial_2": "a"}
    result = await hass.config_entries.flow.async_configure(flow_id, twice)
    assert result["step_id"] == "dial_order"
    assert result["errors"] == {"base": "dial_box_twice"}
    assert _suggested(result, "dial_2") == "a"

    result = await hass.config_entries.flow.async_configure(
        flow_id, {"dial_1": "manual", "dial_2": "manual"}
    )
    assert result["step_id"] == "dial"


@pytest.mark.usefixtures("camera", "mock_setup_entry", "mock_meter_reader")
async def test_dial_order_mark_it_myself(
    hass: HomeAssistant, mock_finder: Mock
) -> None:
    """A dial marked by hand starts empty, an unused box is left out."""
    result = await _to_dial_order(hass, mock_finder, (FIRST_BOX, SECOND_BOX), 2)
    flow_id = result["flow_id"]
    result = await hass.config_entries.flow.async_configure(
        flow_id, {"dial_1": "manual", "dial_2": "b"}
    )
    assert result["step_id"] == "dial"
    assert all(
        "suggested_value" not in (field.description or {})
        for field in result["data_schema"].schema
    )
    result = await hass.config_entries.flow.async_configure(flow_id, FIRST_DIAL)
    assert _suggested(result, CONF_LEFT) == 20


@pytest.mark.usefixtures("camera", "mock_setup_entry", "mock_meter_reader")
async def test_dial_order_fewer_boxes_than_dials(
    hass: HomeAssistant, mock_finder: Mock
) -> None:
    """Dials without a found box default to marking them by hand."""
    result = await _to_dial_order(hass, mock_finder, (FIRST_BOX,), 3, decimal_wheels=3)
    assert result["step_id"] == "dial_order"
    assert [result["description_placeholders"][f"factor_{n}"] for n in (1, 2, 3)] == [
        _times("0.0001"),
        _times("0.00001"),
        _times("0.000001"),
    ]
    assert [_field(result, f"dial_{n}").default() for n in (1, 2, 3)] == [
        "a",
        "manual",
        "manual",
    ]
    selector = result["data_schema"].schema[_field(result, "dial_3")]
    assert selector.config["options"] == ["a", "manual"]

    flow_id = result["flow_id"]
    result = await hass.config_entries.flow.async_configure(flow_id, {})
    assert result["description_placeholders"]["count"] == "3"
    assert _suggested(result, CONF_LEFT) == 60


@pytest.mark.usefixtures("camera", "mock_setup_entry", "mock_meter_reader")
async def test_dial_order_only_after_a_suggestion_with_dials(
    hass: HomeAssistant, mock_finder: Mock
) -> None:
    """Without found dials, or once assigned, the dial forms come directly."""
    result = await _to_dial_order(hass, mock_finder, (), 1)
    assert result["step_id"] == "dial"

    result = await _to_dial_order(hass, mock_finder, (FIRST_BOX,), 1)
    flow_id = result["flow_id"]
    await hass.config_entries.flow.async_configure(flow_id, {})
    await hass.config_entries.flow.async_configure(flow_id, FIRST_DIAL)
    await _choose(hass, flow_id, "digits")
    result = await hass.config_entries.flow.async_configure(
        flow_id, {**DIGITS_INPUT, CONF_DIAL_COUNT: 1}
    )
    assert result["step_id"] == "dial"
    assert _suggested(result, CONF_LEFT) == 60


@pytest.mark.usefixtures("camera", "mock_setup_entry", "mock_meter_reader")
async def test_dial_order_letters_the_boxes(
    hass: HomeAssistant, mock_finder: Mock
) -> None:
    """The preview writes each box's letter next to its outline."""
    with patch(
        "custom_components.local_vision.preview._label", wraps=preview._label
    ) as spy:
        await _to_dial_order(hass, mock_finder, (FIRST_BOX, SECOND_BOX), 2)
    assert spy.call_count == 1
    assert spy.call_args.args[1] == [("A", FIRST_BOX), ("B", SECOND_BOX)]


@pytest.mark.usefixtures("camera", "mock_setup_entry", "mock_meter_reader")
@pytest.mark.parametrize(
    ("decimal_wheels", "factors"),
    [
        (0, ["0.1", "0.01", "0.001", "0.0001"]),
        (1, ["0.01", "0.001", "0.0001", "0.00001"]),
        (3, ["0.0001", "0.00001", "0.000001", "0.0000001"]),
    ],
)
async def test_dial_factor_in_the_dial_forms(
    hass: HomeAssistant, decimal_wheels: int, factors: list[str]
) -> None:
    """Each dial form names what the dial counts, after the decimal wheels."""
    flow_id = await _to_regions(hass)
    await _choose(hass, flow_id, "digits")
    result = await hass.config_entries.flow.async_configure(
        flow_id,
        {**DIGITS_INPUT, CONF_DECIMAL_WHEELS: decimal_wheels, CONF_DIAL_COUNT: 4},
    )
    shown = []
    while result["step_id"] == "dial":
        shown.append(result["description_placeholders"]["factor"])
        result = await hass.config_entries.flow.async_configure(flow_id, DIAL_INPUT)
    assert shown == [_times(factor) for factor in factors]


@pytest.mark.usefixtures("camera", "mock_setup_entry", "mock_meter_reader")
async def test_suggestion_keeps_at_most_eight_dials(
    hass: HomeAssistant, mock_finder: Mock
) -> None:
    """Dials beyond the eight a meter can have are left out, in the finder's order."""
    boxes = tuple(Roi(0.1 * n, 0.6, 0.1 * n + 0.05, 0.7) for n in range(10))
    mock_finder.return_value = Suggestion(
        digits=Roi(0.3, 0.4, 0.7, 0.5),
        digits_score=0.8,
        dials=boxes,
        dial_scores=(0.7,) * len(boxes),
    )
    flow_id = await _to_regions(hass)
    result = await _choose(hass, flow_id, "suggest")
    assert _suggested(result, CONF_DIAL_COUNT) == 8

    with patch(
        "custom_components.local_vision.preview._label", wraps=preview._label
    ) as spy:
        result = await hass.config_entries.flow.async_configure(
            flow_id, {**DIGITS_INPUT, CONF_DIAL_COUNT: 8}
        )
    assert result["step_id"] == "dial_order"
    assert [text for text, _ in spy.call_args.args[1]] == list("ABCDEFGH")
    selector = result["data_schema"].schema[_field(result, "dial_8")]
    assert selector.config["options"] == [*"abcdefgh", "manual"]
    assert _field(result, "dial_8").default() == "h"

    await hass.config_entries.flow.async_configure(flow_id, {})
    for _ in range(7):
        result = await hass.config_entries.flow.async_configure(flow_id, DIAL_INPUT)
    assert result["description_placeholders"]["number"] == "8"
    assert _suggested(result, CONF_LEFT) == 70
