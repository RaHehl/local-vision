"""The Local vision integration."""

from tarsius import BackendError

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ENTITY_ID, CONF_REGION, Platform
from homeassistant.core import CoreState, HomeAssistant
from homeassistant.exceptions import ConfigEntryError
from homeassistant.helpers.device import async_entity_id_to_device_id
from homeassistant.helpers.helper_integration import async_handle_source_entity_changes
from homeassistant.helpers.start import async_at_started
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import CONF_TYPE, DOMAIN, TYPE_STATE
from .coordinator import LocalVisionConfigEntry, StateCoordinator
from .detection import async_create_detector
from .taught_frames import TaughtFrames, async_remove_entry_data

PLATFORMS = {TYPE_STATE: [Platform.BINARY_SENSOR]}


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
) -> DataUpdateCoordinator:
    """Create the coordinator for the type of the entry."""
    detector = await async_create_detector(hass, entry.options[CONF_REGION])
    taught_frames = TaughtFrames(hass, entry.entry_id, detector)
    entry.async_on_unload(taught_frames.close)
    await taught_frames.async_load()
    return StateCoordinator(hass, entry, taught_frames)


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
    return True
