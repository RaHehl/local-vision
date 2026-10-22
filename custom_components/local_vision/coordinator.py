"""Coordinators classifying camera frames."""

from typing import override

from tarsius import BackendError, BlankFrameError, ImageError, Result, Status

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ENTITY_ID
from homeassistant.core import CoreState, HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN, LOGGER, UPDATE_INTERVAL
from .frame_source import FrameUnavailable, async_fetch_frame
from .taught_frames import TaughtFrames

type StateConfigEntry = ConfigEntry[StateCoordinator]
type LocalVisionConfigEntry = ConfigEntry[StateCoordinator]


class StateCoordinator(DataUpdateCoordinator[Result]):
    """Fetch a frame from the camera and classify the state of the region."""

    config_entry: StateConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: StateConfigEntry,
        taught_frames: TaughtFrames,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            logger=LOGGER,
            config_entry=config_entry,
            name=config_entry.title,
            update_interval=UPDATE_INTERVAL,
        )
        self.taught_frames = taught_frames

    @override
    async def _async_update_data(self) -> Result:
        if not self.taught_frames.detector.ready:
            # Nothing to compare with yet; spare the camera.
            return Result(Status.NOT_READY)
        if self.hass.state is not CoreState.running:
            # The camera may not be set up yet; refreshed once started.
            return Result(Status.NOT_READY)
        frame = await async_fetch_camera_frame(self.hass, self.config_entry)
        try:
            return await self.hass.async_add_executor_job(
                self.taught_frames.classify, frame
            )
        except ImageError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="invalid_frame",
                translation_placeholders={"error": str(err)},
            ) from err
        except BlankFrameError:
            # A dark night looks the same as a camera showing nothing.
            return Result(Status.UNSURE)
        except BackendError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="model_unavailable",
                translation_placeholders={"error": str(err)},
            ) from err


async def async_fetch_camera_frame(hass: HomeAssistant, entry: ConfigEntry) -> bytes:
    """Return the current frame of the entry's camera, UpdateFailed without one."""
    try:
        return await async_fetch_frame(hass, entry.options[CONF_ENTITY_ID])
    except FrameUnavailable as err:
        raise UpdateFailed(
            translation_domain=DOMAIN,
            translation_key="camera_unavailable",
            translation_placeholders=err.translation_placeholders,
        ) from err
