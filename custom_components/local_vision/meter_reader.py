"""Meter readings of an entry; the only module that uses tarsius.meter."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
import math
from typing import Any
import warnings

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

__all__ = ["MeterSession", "MeterState", "Readout"]

# Previews keep the detail of the frame the wheels are read from.
PREVIEW_SIDE = METER_FRAME_SIDE
# Status of the last reading: accepted or why it was rejected.
ACCEPTED = "accepted"
STATUSES = [ACCEPTED, *(rejection.value for rejection in Rejection)]


@dataclass(frozen=True, slots=True)
class MeterState:
    """The last accepted value of a meter, as the integration stores it."""

    value: float
    # Timezone-aware.
    at: datetime

    def as_dict(self) -> dict[str, Any]:
        """Return the stored form."""
        return {"value": self.value, "at": self.at.isoformat()}

    @classmethod
    def from_dict(cls, data: Any) -> MeterState | None:
        """Return the state of a stored form, None when it is not usable."""
        if not isinstance(data, Mapping):
            return None
        value, at = data.get("value"), data.get("at")
        if (
            isinstance(value, bool)
            or not isinstance(value, int | float)
            or not math.isfinite(value)
            or not isinstance(at, str)
        ):
            return None
        try:
            parsed = datetime.fromisoformat(at)
        except ValueError:
            return None
        if parsed.utcoffset() is None:
            return None
        return cls(float(value), parsed)


@dataclass(frozen=True, slots=True)
class Readout:
    """One reading of the meter, as the entities show it."""

    # The accepted value, None when the reading was rejected.
    value: float | None
    # What the wheels and dials show, also when rejected.
    candidate: float
    # ACCEPTED or the rejection, one of STATUSES.
    status: str
    # Of the least sure wheel or dial, 0 to 1.
    confidence: float


def layout_from_options(options: Mapping[str, Any]) -> MeterLayout:
    """Return the layout of stored options; raises ValueError if invalid."""
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


def value_scale(options: Mapping[str, Any]) -> tuple[int, int]:
    """Return the whole and fractional places the counter shows.

    Two layouts with the same scale read the same number from a meter.
    """
    layout = layout_from_options(options)
    return layout.wheels - layout.decimal_wheels, layout.decimals


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


def read_once(frame: bytes, options: Mapping[str, Any]) -> Readout:
    """Read a frame without plausibility, e.g. to show it in a flow; blocking."""
    reading = MeterReader().read(
        frame, layout_from_options(options), _calibration(options)
    )
    return _readout(reading)


_alpha_silenced = False


def suggest(frame: bytes) -> dict[str, Any] | None:
    """Return regions the alpha finder suggests, None without a match; blocking.

    The regions are in percent like the flow forms. Raises tarsius
    ImageError, BlankFrameError or BackendError.
    """
    global _alpha_silenced  # noqa: PLW0603
    # Alpha and heavy, only loaded when a user asks for a suggestion.
    from tarsius.meter import finder

    if not _alpha_silenced:
        # The flow says it is alpha; the warning would only fill the log.
        warnings.filterwarnings("ignore", category=finder.AlphaWarning)
        LOGGER.debug("Suggesting meter regions with the alpha finder")
        _alpha_silenced = True
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


def _readout(reading: MeterReading) -> Readout:
    return Readout(
        reading.value,
        reading.candidate,
        ACCEPTED if reading.rejection is None else reading.rejection.value,
        reading.confidence,
    )


class MeterSession:
    """Reads one meter and checks each reading against the last accepted one.

    Calls block and must not overlap; the coordinator serializes them.
    """

    def __init__(self, options: Mapping[str, Any], state: MeterState | None) -> None:
        """Initialize from the entry options and the last accepted value.

        Raises ValueError for options tarsius refuses, e.g. a maximum rate
        of 0 or a calibration that is out of range.
        """
        self.layout = layout_from_options(options)
        self._calibration = _calibration(options)
        self._reader = MeterReader()
        self._plausibility = Plausibility(
            max_rate=float(options[CONF_MAX_RATE]),
            # A rolling last wheel or dial jitters by about one unit.
            tolerance=10.0**-self.layout.decimals,
            # The counter wraps to zero; the value counts on.
            period=self.layout.period,
            state=None if state is None else PlausibilityState(state.value, state.at),
        )

    @property
    def state(self) -> MeterState | None:
        """Return the last accepted value, None before one was confirmed."""
        if (state := self._plausibility.state) is None:
            return None
        return MeterState(state.value, state.at)

    def load(self) -> None:
        """Load the readers; blocking, raises tarsius BackendError."""
        self._reader.load()

    def read(self, frame: bytes, at: datetime) -> Readout:
        """Read a frame and check it against the last accepted value; blocking.

        Raises tarsius ImageError, BlankFrameError or BackendError.
        """
        reading = self._reader.read(frame, self.layout, self._calibration)
        reading = self._plausibility.review(reading, at)
        return _readout(reading)

    def correct(self, value: float, at: datetime) -> None:
        """Take a value the user read on the meter as the last accepted one."""
        self._plausibility.accept(value, at)
