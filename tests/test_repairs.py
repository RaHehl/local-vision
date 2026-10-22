"""Tests for the Local vision repairs: lasting disputes and missing values."""

from datetime import timedelta
from typing import Any
from unittest.mock import Mock

from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.local_vision.const import CONF_CONFIRMED, DOMAIN, UPDATE_INTERVAL
from custom_components.local_vision.meter_store import STORAGE_VERSION
from homeassistant.components.repairs import repairs_flow_manager
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util

from . import METER_ENTITY_ID, STATUS_ENTITY_ID, meter_reading, setup_integration


async def _poll(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    freezer.tick(UPDATE_INTERVAL + timedelta(seconds=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)


def _seed(hass_storage: dict[str, Any], entry: MockConfigEntry, value: float) -> None:
    key = f"{DOMAIN}.{entry.entry_id}"
    hass_storage[key] = {
        "version": STORAGE_VERSION,
        "minor_version": 1,
        "key": key,
        "data": {"meter": {"value": value, "at": dt_util.utcnow().isoformat()}},
    }


async def _fix(hass: HomeAssistant, issue_id: str, value: float) -> dict[str, Any]:
    assert await async_setup_component(hass, "repairs", {})
    manager = repairs_flow_manager(hass)
    assert manager is not None
    result = await manager.async_init(DOMAIN, context={"issue_id": issue_id})
    if result["type"] is not FlowResultType.FORM:
        return dict(result)
    assert result["step_id"] == "value"
    return dict(await manager.async_configure(result["flow_id"], {"value": value}))


async def _dispute(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    freezer: FrozenDateTimeFactory,
    entry: MockConfigEntry,
    reader: Mock,
) -> str:
    """Read 10 below a typo'd 133.456 for seven hours."""
    _seed(hass_storage, entry, 133.456)
    reader.return_value = meter_reading(123.456)
    await setup_integration(hass, entry)
    for _ in range(84):
        await _poll(hass, freezer)
    return f"disputed_{entry.entry_id}"


@pytest.mark.usefixtures("camera")
async def test_lasting_misread_drops_raise_a_repair(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    freezer: FrozenDateTimeFactory,
    issue_registry: ir.IssueRegistry,
    meter_entry: MockConfigEntry,
    mock_meter_reader: Mock,
) -> None:
    """Ten below for hours: the meter or the last value is wrong, ask."""
    issue_id = await _dispute(
        hass, hass_storage, freezer, meter_entry, mock_meter_reader
    )
    issue = issue_registry.async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.is_fixable
    assert issue.translation_placeholders is not None
    assert issue.translation_placeholders["value"] == "123.456"
    assert issue.translation_placeholders["last"] == "133.456"
    assert hass.states.get(METER_ENTITY_ID).state == "133.456"

    result = await _fix(hass, issue_id, 123.456)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert issue_registry.async_get_issue(DOMAIN, issue_id) is None
    assert hass.states.get(METER_ENTITY_ID).state == "123.456"

    await _poll(hass, freezer)
    assert hass.states.get(STATUS_ENTITY_ID).state == "accepted"


@pytest.mark.usefixtures("camera")
async def test_readings_that_agree_again_drop_the_repair(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    freezer: FrozenDateTimeFactory,
    issue_registry: ir.IssueRegistry,
    meter_entry: MockConfigEntry,
    mock_meter_reader: Mock,
) -> None:
    """A misread that goes away settles the dispute by itself."""
    issue_id = await _dispute(
        hass, hass_storage, freezer, meter_entry, mock_meter_reader
    )
    assert issue_registry.async_get_issue(DOMAIN, issue_id) is not None

    mock_meter_reader.return_value = meter_reading(133.5)
    await _poll(hass, freezer)
    assert issue_registry.async_get_issue(DOMAIN, issue_id) is None


@pytest.mark.usefixtures("camera", "mock_meter_reader")
async def test_unconfirmed_meter_repair_sets_the_value(
    hass: HomeAssistant,
    issue_registry: ir.IssueRegistry,
    meter_entry: MockConfigEntry,
) -> None:
    """The request for a first value is fixed from the repairs dashboard."""
    meter_entry.add_to_hass(hass)
    options = {k: v for k, v in meter_entry.options.items() if k != CONF_CONFIRMED}
    hass.config_entries.async_update_entry(meter_entry, options=options)
    await setup_integration(hass, meter_entry)
    issue_id = f"unconfirmed_{meter_entry.entry_id}"

    result = await _fix(hass, issue_id, 123.4)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert hass.states.get(METER_ENTITY_ID).state == "123.4"
    assert issue_registry.async_get_issue(DOMAIN, issue_id) is None


@pytest.mark.usefixtures("camera", "mock_meter_reader")
async def test_repair_of_an_unloaded_meter(
    hass: HomeAssistant, meter_entry: MockConfigEntry
) -> None:
    """While the helper is not loaded the value can't be set."""
    meter_entry.add_to_hass(hass)
    options = {k: v for k, v in meter_entry.options.items() if k != CONF_CONFIRMED}
    hass.config_entries.async_update_entry(meter_entry, options=options)
    await setup_integration(hass, meter_entry)
    await hass.config_entries.async_unload(meter_entry.entry_id)
    ir.async_create_issue(
        hass,
        DOMAIN,
        "unconfirmed_x",
        data={"entry_id": meter_entry.entry_id},
        is_fixable=True,
        severity=ir.IssueSeverity.WARNING,
        translation_key="meter_unconfirmed",
        translation_placeholders={"name": "Water meter"},
    )

    result = await _fix(hass, "unconfirmed_x", 1.0)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "not_loaded"
