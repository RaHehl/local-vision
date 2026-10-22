"""Stored state of a meter entry."""

from typing import Any, override

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN
from .meter_reader import MeterState

# 1 kept a separate highest value next to tarsius' own plausibility state.
# 2 keeps one state, {"meter": {"value": float, "at": iso time}}.
# 3 adds what the checks remember to "meter" (see MeterState) and the
#   exposure learnt from readings as "exposure"; 2 reads as 3 unchanged.
STORAGE_VERSION = 3
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
            state = MeterState.from_dict(meter.get("plausibility"))
            data["meter"] = rest | (state.as_dict() if state else {})
        return data


def meter_store(hass: HomeAssistant, entry_id: str) -> MeterStore:
    """Return the store of a meter entry."""
    return MeterStore(hass, STORAGE_VERSION, f"{DOMAIN}.{entry_id}")
