"""Coordinators reading camera frames."""

import asyncio
from typing import Any, override

from tarsius import BackendError, BlankFrameError, ImageError, Result, Status

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ENTITY_ID
from homeassistant.core import CoreState, HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import DOMAIN, LOGGER, UPDATE_INTERVAL
from .frame_source import FrameUnavailable, async_fetch_frame
from .meter_reader import MeterSession, Readout
from .meter_store import SAVE_DELAY, MeterStore
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


class MeterCoordinator(DataUpdateCoordinator[Readout | None]):
    """Read the meter and keep the last accepted value.

    The data is the last reading, None before the first one and when the
    frame shows nothing. The value is the session's last accepted one; it
    only drops when the user corrects it or several readings agree on a
    lower value.
    """

    config_entry: MeterConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: LocalVisionConfigEntry,
        session: MeterSession,
        store: MeterStore,
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

    @property
    def value(self) -> float | None:
        """Return the last accepted value, None before one was confirmed."""
        return self.session.value

    @override
    async def _async_update_data(self) -> Readout | None:
        if self.hass.state is not CoreState.running:
            # The camera may not be set up yet; refreshed once started.
            return self.data
        frame = await async_fetch_camera_frame(self.hass, self.config_entry)
        async with self._lock:
            return await self._async_read(frame)

    async def _async_read(self, frame: bytes) -> Readout | None:
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
        # Rejected readings change what the checks remember too.
        self._store.async_delay_save(self._data_to_save, SAVE_DELAY)
        self._update_dispute_issue()
        return reading

    def _update_dispute_issue(self) -> None:
        """Ask the user to settle lasting lower readings, or drop the request."""
        issue_id = f"disputed_{self.config_entry.entry_id}"
        if (dispute := self.session.dispute) is None:
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)
            return
        decimals = self.session.layout.decimals
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            issue_id,
            # The fix suggests the last value, not the disputed reading:
            # one click must not confirm what may be a misread.
            data={"entry_id": self.config_entry.entry_id},
            is_fixable=True,
            severity=ir.IssueSeverity.WARNING,
            translation_key="meter_disputed",
            translation_placeholders={
                "name": self.config_entry.title,
                "value": f"{dispute.value:.{decimals}f}",
                "last": f"{self.value:.{decimals}f}" if self.value is not None else "",
                "since": dt_util.as_local(dispute.since).strftime("%Y-%m-%d %H:%M"),
            },
        )

    async def async_correct(self, value: float) -> None:
        """Set the value the user read on the meter, also a lower one."""
        async with self._lock:
            self.session.correct(value, dt_util.utcnow())
            await self._store.async_save(self._data_to_save())
        async_delete_unconfirmed_issue(self.hass, self.config_entry)
        self._update_dispute_issue()
        self.async_update_listeners()

    def _data_to_save(self) -> dict[str, Any]:
        meter = self._stored.get("meter")
        meter = dict(meter) if isinstance(meter, dict) else {}
        if (state := self.session.state) is not None:
            meter |= state
        self._stored = {**self._stored, "meter": meter}
        if (exposure := self.session.exposure) is not None:
            self._stored["exposure"] = exposure
        return self._stored


def async_create_unconfirmed_issue(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Ask the user for the value on the meter; nothing is accepted without it."""
    ir.async_create_issue(
        hass,
        DOMAIN,
        f"unconfirmed_{entry.entry_id}",
        data={"entry_id": entry.entry_id},
        is_fixable=True,
        severity=ir.IssueSeverity.WARNING,
        translation_key="meter_unconfirmed",
        translation_placeholders={"name": entry.title},
    )


def async_delete_unconfirmed_issue(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove the requests for the meter value."""
    ir.async_delete_issue(hass, DOMAIN, f"unconfirmed_{entry.entry_id}")
    ir.async_delete_issue(hass, DOMAIN, f"disputed_{entry.entry_id}")
