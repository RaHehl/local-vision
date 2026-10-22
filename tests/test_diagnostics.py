"""Tests for the Local vision diagnostics."""

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from custom_components.local_vision.const import CONF_CALIBRATION
from homeassistant.core import HomeAssistant

from . import FakeCamera, setup_integration, teach_both


@pytest.mark.usefixtures("camera", "mock_meter_reader")
async def test_meter_diagnostics(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    meter_entry: MockConfigEntry,
) -> None:
    """Settings, the check state and the last reading, no images."""
    await setup_integration(hass, meter_entry)

    data = await get_diagnostics_for_config_entry(hass, hass_client, meter_entry)
    assert data["loaded"] is True
    assert data["options"]["wheels"] == 8
    assert "exposure" not in data["options"][CONF_CALIBRATION]
    assert data["options"][CONF_CALIBRATION]["exposure_elements"] == 0
    assert data["meter"]["state"]["value"] == 123.456
    assert data["meter"]["last_reading"] == {
        "status": "accepted",
        "candidate": 123.456,
        "value": 123.456,
        "confidence": 0.9,
    }


@pytest.mark.usefixtures("camera", "mock_meter_reader")
async def test_meter_diagnostics_not_loaded(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    meter_entry: MockConfigEntry,
) -> None:
    """An unloaded helper shows its options only."""
    await setup_integration(hass, meter_entry)
    await hass.config_entries.async_unload(meter_entry.entry_id)

    data = await get_diagnostics_for_config_entry(hass, hass_client, meter_entry)
    assert data["loaded"] is False
    assert "meter" not in data


async def test_state_diagnostics(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    camera: FakeCamera,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Taught frames per state and the last result."""
    await setup_integration(hass, mock_config_entry)
    # Teaching registers the preview view; the test client freezes the routes.
    await teach_both(hass, mock_config_entry, camera)
    await mock_config_entry.runtime_data.async_refresh()

    data = await get_diagnostics_for_config_entry(hass, hass_client, mock_config_entry)
    assert data["state"]["taught"] == {"on": 1, "off": 1}
    assert data["state"]["last_result"]["status"] == "confident"
    # The last frame taught shows the empty spot.
    assert data["state"]["last_result"]["state"] == "off"
