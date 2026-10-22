"""Diagnostics for Local vision: settings and what the last reading saw, no images."""

from typing import Any

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant

from .const import CONF_CALIBRATION, CONF_TYPE, TYPE_METER
from .coordinator import LocalVisionConfigEntry, MeterCoordinator, StateCoordinator


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: LocalVisionConfigEntry
) -> dict[str, Any]:
    """Return the options and the last result of an entry."""
    options = dict(entry.options)
    if (calibration := options.pop(CONF_CALIBRATION, None)) is not None:
        # The per-wheel exposure is long and says little; keep the summary.
        options[CONF_CALIBRATION] = {
            key: value for key, value in calibration.items() if key != "exposure"
        } | {"exposure_elements": len(calibration.get("exposure", []))}
    data: dict[str, Any] = {"options": options, "loaded": False}
    if entry.state is not ConfigEntryState.LOADED:
        return data
    data["loaded"] = True
    coordinator = entry.runtime_data
    if entry.options[CONF_TYPE] == TYPE_METER:
        assert isinstance(coordinator, MeterCoordinator)
        session = coordinator.session
        reading = coordinator.data
        data["meter"] = {
            "state": session.state,
            "exposure_learnt": session.exposure is not None,
            "last_reading": None
            if reading is None
            else {
                "status": reading.status,
                "candidate": reading.candidate,
                "value": reading.value,
                "confidence": reading.confidence,
            },
        }
        return data
    assert isinstance(coordinator, StateCoordinator)
    result = coordinator.data
    taught = coordinator.taught_frames
    data["state"] = {
        "taught": taught.detector.counts(),
        "last_result": None
        if result is None
        else {
            "status": result.status.value,
            "state": result.state,
            "margin": result.margin,
            "probabilities": dict(result.probabilities),
        },
    }
    return data
