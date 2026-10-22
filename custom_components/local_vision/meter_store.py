"""Stored state of a meter entry."""

from typing import Any, override

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN
from .meter_reader import parse_state

# 1 kept a separate highest value next to tarsius' own plausibility state.
# 2 keeps one state, {"meter": {"value": float, "at": iso time}}.
# 3 adds what the checks remember to "meter" and the exposure learnt from
#   readings as "exposure".
# 4 keeps "meter" as tarsius' PlausibilityState dict.
STORAGE_VERSION = 4
# Readings come every few minutes; a restart flushes pending saves.
SAVE_DELAY = 10


class MeterStore(Store[dict[str, Any]]):
    """The store of a meter entry, migrating older formats."""

    @override
    async def _async_migrate_func(
        self, old_major_version: int, old_minor_version: int, old_data: Any
    ) -> dict[str, Any]:
        if not isinstance(old_data, dict):
            return {}
        data = dict(old_data)
        meter = data.get("meter")
        if old_major_version < 2 and isinstance(meter, dict):
            # The plausibility state was the last accepted value; the separate
            # maximum could only drift above it by the jitter tolerance.
            rest = {
                key: value
                for key, value in meter.items()
                if key not in ("value", "plausibility")
            }
            state = parse_state(meter.get("plausibility"))
            data["meter"] = meter = rest | (state.as_dict() if state else {})
        if old_major_version < 4 and isinstance(meter, dict):
            # 3 kept a flag where tarsius keeps an offset, and bare misread
            # offsets where it follows them; both start afresh.
            data["meter"] = {
                key: value
                for key, value in meter.items()
                if key not in ("alternative", "misreads")
            }
        return data


def meter_store(hass: HomeAssistant, entry_id: str) -> MeterStore:
    """Return the store of a meter entry."""
    return MeterStore(hass, STORAGE_VERSION, f"{DOMAIN}.{entry_id}")
