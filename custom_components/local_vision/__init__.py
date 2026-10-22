"""The Local vision integration."""

from tarsius import BackendError

from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_DEVICE_CLASS, CONF_ENTITY_ID, CONF_REGION, Platform
from homeassistant.core import CoreState, HomeAssistant
from homeassistant.exceptions import ConfigEntryError
from homeassistant.helpers.device import async_entity_id_to_device_id
from homeassistant.helpers.helper_integration import async_handle_source_entity_changes
from homeassistant.helpers.start import async_at_started

from .const import (
    CONF_CONFIRMED,
    CONF_TYPE,
    CONF_UNIT,
    DOMAIN,
    LOGGER,
    METER_UNITS,
    TYPE_METER,
    TYPE_STATE,
)
from .coordinator import (
    LocalVisionConfigEntry,
    MeterCoordinator,
    StateCoordinator,
    async_create_unconfirmed_issue,
    async_delete_unconfirmed_issue,
)
from .detection import async_create_detector
from .meter_reader import MeterSession, MeterState
from .meter_store import meter_store
from .taught_frames import TaughtFrames, async_remove_entry_data

PLATFORMS = {
    TYPE_STATE: [Platform.BINARY_SENSOR],
    TYPE_METER: [Platform.SENSOR],
}


async def async_setup_entry(hass: HomeAssistant, entry: LocalVisionConfigEntry) -> bool:
    """Set up Local vision from a config entry."""

    def set_source_entity_id_or_uuid(source_entity_id: str) -> None:
        hass.config_entries.async_update_entry(
            entry, options={**entry.options, CONF_ENTITY_ID: source_entity_id}
        )
        hass.config_entries.async_schedule_reload(entry.entry_id)

    entry.async_on_unload(
        async_handle_source_entity_changes(
            hass,
            helper_config_entry_id=entry.entry_id,
            set_source_entity_id_or_uuid=set_source_entity_id_or_uuid,
            source_device_id=async_entity_id_to_device_id(
                hass, entry.options[CONF_ENTITY_ID]
            ),
            source_entity_id_or_uuid=entry.options[CONF_ENTITY_ID],
        )
    )

    try:
        coordinator = await _async_create_coordinator(hass, entry)
    except BackendError as err:
        # The model ships with tarsius, retrying won't bring it back.
        raise ConfigEntryError(
            translation_domain=DOMAIN,
            translation_key="model_unavailable",
            translation_placeholders={"error": str(err)},
        ) from err
    # Cameras go offline at times; the sensor is unavailable meanwhile and
    # the options stay usable, like other helpers with a missing source.
    await coordinator.async_refresh()
    if hass.state is not CoreState.running:

        async def refresh_when_started(hass: HomeAssistant) -> None:
            await coordinator.async_refresh()

        entry.async_on_unload(async_at_started(hass, refresh_when_started))
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(
        entry, PLATFORMS[entry.options[CONF_TYPE]]
    )
    return True


async def _async_create_coordinator(
    hass: HomeAssistant, entry: LocalVisionConfigEntry
) -> StateCoordinator | MeterCoordinator:
    """Create the coordinator for the type of the entry."""
    if entry.options[CONF_TYPE] == TYPE_METER:
        store = meter_store(hass, entry.entry_id)
        if not isinstance(stored := await store.async_load(), dict):
            stored = {}
        meter = stored.get("meter")
        if meter and MeterState.from_dict(meter) is None:
            LOGGER.warning("Ignoring invalid stored meter state %r", meter)
        state = _newest(
            MeterState.from_dict(meter),
            MeterState.from_dict(entry.options.get(CONF_CONFIRMED)),
        )
        try:
            session = MeterSession(entry.options, state)
        except ValueError as err:
            raise ConfigEntryError(
                translation_domain=DOMAIN,
                translation_key="invalid_settings",
                translation_placeholders={"error": str(err)},
            ) from err
        await hass.async_add_executor_job(session.load)
        if session.state is None:
            async_create_unconfirmed_issue(hass, entry)
        else:
            async_delete_unconfirmed_issue(hass, entry)
        return MeterCoordinator(hass, entry, session, store, stored)
    detector = await async_create_detector(hass, entry.options[CONF_REGION])
    taught_frames = TaughtFrames(hass, entry.entry_id, detector)
    entry.async_on_unload(taught_frames.close)
    await taught_frames.async_load()
    return StateCoordinator(hass, entry, taught_frames)


def _newest(*states: MeterState | None) -> MeterState | None:
    """Return the latest state; a value confirmed in a flow can be newer."""
    known = [state for state in states if state is not None]
    return max(known, key=lambda state: state.at, default=None)


async def async_unload_entry(
    hass: HomeAssistant, entry: LocalVisionConfigEntry
) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(
        entry, PLATFORMS[entry.options[CONF_TYPE]]
    )


async def async_remove_entry(
    hass: HomeAssistant, entry: LocalVisionConfigEntry
) -> None:
    """Delete the stored data of a removed entry."""
    async_delete_unconfirmed_issue(hass, entry)
    await async_remove_entry_data(hass, entry.entry_id)


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate an entry to the current version."""
    if entry.minor_version < 2:
        # Entries from before meters all watch the state of a region.
        hass.config_entries.async_update_entry(
            entry,
            options={CONF_TYPE: TYPE_STATE, **entry.options},
            minor_version=2,
        )
    if entry.minor_version < 3:
        options = dict(entry.options)
        if (
            options[CONF_TYPE] == TYPE_METER
            and options[CONF_DEVICE_CLASS] == SensorDeviceClass.GAS
            and options[CONF_UNIT] not in METER_UNITS[SensorDeviceClass.GAS]
        ):
            # A gas sensor can't count kWh; as energy it still fits the
            # gas part of the Energy dashboard.
            options[CONF_DEVICE_CLASS] = SensorDeviceClass.ENERGY
        hass.config_entries.async_update_entry(entry, options=options, minor_version=3)
    return True
