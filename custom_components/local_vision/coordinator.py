"""Coordinators reading camera frames."""

import asyncio
from typing import Any, override

from tarsius import BackendError, BlankFrameError, ImageError, Result, Status
from tarsius.meter import MeterReading

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ENTITY_ID
from homeassistant.core import CoreState, HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import DOMAIN, LOGGER, UPDATE_INTERVAL
from .frame_source import FrameUnavailable, async_fetch_frame
from .meter_reader import MeterSession
from .taught_frames import TaughtFrames

type StateConfigEntry = ConfigEntry[StateCoordinator]
type MeterConfigEntry = ConfigEntry[MeterCoordinator]
type LocalVisionConfigEntry = ConfigEntry[StateCoordinator | MeterCoordinator]


class StateCoordinator(DataUpdateCoordinator[Result]):
    """Fetch a frame from the camera and classify the state of the region."""

    config_entry: StateConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: LocalVisionConfigEntry,
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


class MeterCoordinator(DataUpdateCoordinator[MeterReading | None]):
    """Read the meter and keep the last accepted value.

    The data is the last reading, None before the first one and when the
    frame shows nothing. The value only rises, except when the user
    corrects it.
    """

    config_entry: MeterConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: LocalVisionConfigEntry,
        session: MeterSession,
        store: Store[dict[str, Any]],
        stored: dict[str, Any],
    ) -> None:
        """Initialize the coordinator with the stored data of the entry."""
        super().__init__(
            hass,
            logger=LOGGER,
            config_entry=config_entry,
            name=config_entry.title,
            update_interval=UPDATE_INTERVAL,
        )
        self.session = session
        # The session is not thread safe; reads and corrections take turns.
        self._lock = asyncio.Lock()
        self._store = store
        # Raw stored data, so fields written by newer versions survive a save.
        self._stored = stored
        value = stored["meter"].get("value")
        self.value: float | None = (
            float(value) if isinstance(value, int | float) else None
        )

    @override
    async def _async_update_data(self) -> MeterReading | None:
        if self.hass.state is not CoreState.running:
            # The camera may not be set up yet; refreshed once started.
            return self.data
        frame = await async_fetch_camera_frame(self.hass, self.config_entry)
        async with self._lock:
            return await self._async_read(frame)

    async def _async_read(self, frame: bytes) -> MeterReading | None:
        try:
            reading = await self.hass.async_add_executor_job(
                self.session.read, frame, dt_util.utcnow()
            )
        except ImageError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="invalid_frame",
                translation_placeholders={"error": str(err)},
            ) from err
        except BlankFrameError:
            # A dark night looks the same as a camera showing nothing.
            return None
        except BackendError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="model_unavailable",
                translation_placeholders={"error": str(err)},
            ) from err
        if reading.value is not None:
            # A slightly lower reading within the tolerance keeps the value.
            if self.value is None or reading.value > self.value:
                self.value = reading.value
            await self._async_save()
        return reading

    async def async_correct(self, value: float) -> None:
        """Set the value the user read on the meter, also a lower one."""
        async with self._lock:
            self.session.correct(value, dt_util.utcnow())
            self.value = value
            await self._async_save()
        self.async_update_listeners()

    async def _async_save(self) -> None:
        self._stored = {
            **self._stored,
            "meter": {
                **self._stored["meter"],
                "value": self.value,
                "plausibility": self.session.state,
            },
        }
        await self._store.async_save(self._stored)
