"""Tests for the Local vision meter config flow."""

from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest
from tarsius import BackendError, BlankFrameError, Roi
from tarsius.meter import Calibration, Rejection
from tarsius.meter.finder import Suggestion

from custom_components.local_vision.const import (
    CONF_BOTTOM,
    CONF_CALIBRATION,
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
    CONF_WHEELS,
    DOMAIN,
    TYPE_METER,
)
from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_DEVICE_CLASS, CONF_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult, FlowResultType

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
    return flow_id


async def _choose(hass: HomeAssistant, flow_id: str, step: str) -> FlowResult:
    return await hass.config_entries.flow.async_configure(
        flow_id, {"next_step_id": step}
    )


def _suggested(result: FlowResult, key: str) -> Any:
    for field in result["data_schema"].schema:
        if field == key:
            return field.description["suggested_value"]
    raise KeyError(key)


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
    assert result["description_placeholders"]["reading"] == "123.456 (Accepted)"
    assert result["description_placeholders"]["preview"].startswith(
        "/api/local_vision/preview/"
    )

    result = await _choose(hass, flow_id, "meter_finish")
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Water meter"
    assert result["options"] == {
        CONF_TYPE: TYPE_METER,
        CONF_ENTITY_ID: CAMERA,
        CONF_DEVICE_CLASS: "water",
        CONF_UNIT: "m³",
        CONF_MAX_RATE: 10,
        CONF_DIGITS: DIGITS,
        CONF_ROTATION: 0,
        CONF_WHEELS: 8,
        CONF_DECIMAL_WHEELS: 3,
        CONF_DIALS: [],
        CONF_CALIBRATION: Calibration().as_dict(),
    }
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
    result = await _choose(hass, flow_id, "meter_finish")

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
    assert _suggested(result, CONF_LEFT) == 60
    assert _suggested(result, CONF_COUNTERCLOCKWISE) is False
    await hass.config_entries.flow.async_configure(flow_id, DIAL_INPUT)
    result = await _choose(hass, flow_id, "meter_finish")
    assert result["options"][CONF_ROTATION] == 4.0
    assert result["options"][CONF_DIALS] == [DIAL_INPUT]


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
    ("outcome", "reading", "calibrated"),
    [
        pytest.param(
            {"return_value": meter_reading(12.5, Rejection.LOW_CONFIDENCE)},
            "12.5 (Not sure)",
            True,
            id="unsure",
        ),
        pytest.param(
            {"side_effect": BlankFrameError("dark")},
            "The camera shows nothing",
            False,
            id="blank",
        ),
        pytest.param(
            {"side_effect": BackendError("broken")},
            "Unexpected error",
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
    calibrated: bool,
) -> None:
    """The regions can be kept even when the meter can't be read right now."""
    mock_meter_reader.configure_mock(**outcome)
    flow_id = await _to_regions(hass)
    await _choose(hass, flow_id, "digits")
    result = await hass.config_entries.flow.async_configure(flow_id, DIGITS_INPUT)
    assert result["description_placeholders"]["reading"] == reading

    result = await _choose(hass, flow_id, "meter_finish")
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

    result = await _choose(hass, flow_id, "meter_finish")
    assert result["options"][CONF_ROTATION] == -3.5
