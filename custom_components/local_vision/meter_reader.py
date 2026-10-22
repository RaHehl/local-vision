"""Meter readings of an entry; the only module that uses tarsius.meter."""

from collections.abc import Mapping
from datetime import datetime
from io import BytesIO
from typing import Any

from PIL import Image
from tarsius import Roi, load_image
from tarsius.meter import (
    METER_FRAME_SIDE,
    Calibration,
    Dial,
    MeterLayout,
    MeterReader,
    MeterReading,
    Plausibility,
    PlausibilityState,
    Rejection,
)

from .const import (
    CONF_CALIBRATION,
    CONF_COUNTERCLOCKWISE,
    CONF_DECIMAL_WHEELS,
    CONF_DIALS,
    CONF_DIGITS,
    CONF_MAX_RATE,
    CONF_ROTATION,
    CONF_WHEELS,
    LOGGER,
)
from .detection import region_to_roi

__all__ = ["MeterReading", "MeterSession"]

# Previews keep the detail of the frame the wheels are read from.
PREVIEW_SIDE = METER_FRAME_SIDE
# Status of the last reading: accepted or why it was rejected.
ACCEPTED = "accepted"
STATUSES = [ACCEPTED, *(rejection.value for rejection in Rejection)]


def layout_from_options(options: Mapping[str, Any]) -> MeterLayout:
    """Return the layout of stored options; raises RoiError or ValueError."""
    return MeterLayout(
        digits=region_to_roi(options[CONF_DIGITS]),
        wheels=int(options[CONF_WHEELS]),
        decimal_wheels=int(options[CONF_DECIMAL_WHEELS]),
        dials=tuple(
            Dial(
                region_to_roi(_region(dial)),
                counterclockwise=dial[CONF_COUNTERCLOCKWISE],
            )
            for dial in options[CONF_DIALS]
        ),
        rotation=float(options[CONF_ROTATION]),
    )


def layout_rois(options: Mapping[str, Any]) -> list[Roi]:
    """Return the regions that are set so far, for a preview."""
    rois = []
    if CONF_DIGITS in options:
        rois.append(region_to_roi(options[CONF_DIGITS]))
    rois.extend(region_to_roi(_region(dial)) for dial in options.get(CONF_DIALS, []))
    return rois


def _region(dial: Mapping[str, Any]) -> dict[str, float]:
    return {key: value for key, value in dial.items() if key != CONF_COUNTERCLOCKWISE}


def calibrate(frame: bytes, options: Mapping[str, Any]) -> dict[str, Any]:
    """Return the calibration of the wheels from a frame; blocking.

    Raises tarsius ImageError, BlankFrameError or BackendError.
    """
    return MeterReader().calibrate(frame, layout_from_options(options)).as_dict()


def read_once(frame: bytes, options: Mapping[str, Any]) -> MeterReading:
    """Read a frame without plausibility, e.g. to show it in a flow; blocking."""
    return MeterReader().read(
        frame, layout_from_options(options), _calibration(options)
    )


def suggest(frame: bytes) -> dict[str, Any] | None:
    """Return regions the alpha finder suggests, None without a match; blocking.

    The regions are in percent like the flow forms. Raises tarsius
    ImageError, BlankFrameError or BackendError.
    """
    # Alpha and heavy, only loaded when a user asks for a suggestion.
    from tarsius.meter import finder

    suggestion = finder.suggest(frame)
    if suggestion.digits is None:
        return None
    return {
        CONF_DIGITS: _percent(suggestion.digits),
        CONF_ROTATION: round(suggestion.rotation, 1),
        CONF_DIALS: [
            {**_percent(roi), CONF_COUNTERCLOCKWISE: False} for roi in suggestion.dials
        ],
    }


def rotate(frame: bytes, rotation: float) -> bytes:
    """Return the frame turned like the reader turns it, for a preview; blocking.

    Raises tarsius ImageError.
    """
    image = load_image(frame, max_side=METER_FRAME_SIDE)
    buffer = BytesIO()
    image.rotate(rotation, Image.Resampling.BILINEAR).save(buffer, "JPEG", quality=90)
    return buffer.getvalue()


def _percent(roi: Roi) -> dict[str, float]:
    return dict(
        zip(
            ("left", "top", "right", "bottom"),
            (round(value * 100, 1) for value in roi.as_tuple()),
            strict=True,
        )
    )


def _calibration(options: Mapping[str, Any]) -> Calibration | None:
    if (data := options.get(CONF_CALIBRATION)) is None:
        return None
    return Calibration.from_dict(data)


class MeterSession:
    """Reads one meter and remembers the last plausible value.

    Calls block and must not overlap; the coordinator serializes them.
    """

    def __init__(
        self, options: Mapping[str, Any], state: Mapping[str, Any] | None
    ) -> None:
        """Initialize from the entry options and the stored state."""
        self.layout = layout_from_options(options)
        self._calibration = _calibration(options)
        self._reader = MeterReader()
        restored = None
        if state is not None:
            try:
                restored = PlausibilityState.from_dict(state)
            except ValueError:
                LOGGER.warning("Ignoring invalid stored meter state %r", state)
        self._plausibility = Plausibility(
            max_rate=options[CONF_MAX_RATE],
            # A rolling last wheel or dial jitters by about one unit.
            tolerance=10.0**-self.layout.decimals,
            state=restored,
        )

    @property
    def state(self) -> dict[str, Any] | None:
        """Return the state to store, so checks continue after a restart."""
        if (state := self._plausibility.state) is None:
            return None
        return state.as_dict()

    def load(self) -> None:
        """Load the readers; blocking, raises tarsius BackendError."""
        self._reader.load()

    def read(self, frame: bytes, at: datetime) -> MeterReading:
        """Read a frame and check it against the last accepted value; blocking.

        Raises tarsius ImageError, BlankFrameError or BackendError.
        """
        reading = self._reader.read(frame, self.layout, self._calibration)
        return self._plausibility.review(reading, at)

    def correct(self, value: float, at: datetime) -> None:
        """Take a value the user entered as the last accepted one."""
        self._plausibility.accept(value, at)


def status(reading: MeterReading) -> str:
    """Return the status of a reading."""
    return ACCEPTED if reading.rejection is None else reading.rejection.value
