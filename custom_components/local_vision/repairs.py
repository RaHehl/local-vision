"""Repairs of Local vision meters: confirm the value on the meter."""

from typing import Any


from homeassistant.components.repairs import RepairsFlow
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult

from .config_flow import VALUE_SCHEMA
from .const import CONF_VALUE


async def async_create_fix_flow(
    hass: HomeAssistant, issue_id: str, data: dict[str, Any] | None
) -> RepairsFlow:
    """Create the flow that asks for the value on the meter."""
    return MeterValueRepairFlow()


class MeterValueRepairFlow(RepairsFlow):
    """Set the value shown on the meter, like the correct-value option."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Go to the only step."""
        return await self.async_step_value()

    async def async_step_value(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Ask for the value; the next readings are checked against it."""
        data = self.data or {}
        entry = self.hass.config_entries.async_get_entry(str(data.get("entry_id")))
        if entry is None or entry.state is not ConfigEntryState.LOADED:
            return self.async_abort(reason="not_loaded")
        coordinator = entry.runtime_data
        if user_input is not None:
            await coordinator.async_correct(user_input[CONF_VALUE])
            return self.async_create_entry(data={})
        suggested = data.get("value", coordinator.value)
        return self.async_show_form(
            step_id="value",
            data_schema=self.add_suggested_values_to_schema(
                VALUE_SCHEMA, {} if suggested is None else {CONF_VALUE: suggested}
            ),
            description_placeholders={"name": entry.title},
        )
