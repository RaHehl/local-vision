"""Meter readings of an entry; the only module that uses tarsius.meter."""

from collections.abc import Mapping
import dataclasses
from dataclasses import dataclass
from datetime import datetime
import hashlib
from io import BytesIO
import json
from typing import Any
import warnings

from PIL import Image
from tarsius import Roi, load_image
from tarsius.meter import (
    METER_FRAME_SIDE,
    Calibration,
    Dial,
    Dispute,
    Exposure,
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
    MAX_DIALS,
)
from .detection import region_to_roi

__all__ = ["MeterSession", "Readout", "parse_state"]

# Previews keep the detail of the frame the wheels are read from.
PREVIEW_SIDE = METER_FRAME_SIDE
# Status of the last reading: accepted or why it was rejected.
ACCEPTED = "accepted"
STATUSES = [ACCEPTED, *(rejection.value for rejection in Rejection)]


def parse_state(data: Any) -> PlausibilityState | None:
    """Return the meter state of a stored or confirmed dict, None if unusable.

    The state is tarsius' own, stored as its dict in a versioned store.
    """
    if data is None:
        return None
    try:
        return PlausibilityState.from_dict(data)
    except ValueError:
        pass
    # One damaged detail must not cost the value: keep value and time.
    try:
        state = PlausibilityState.from_dict(
            {"value": data.get("value"), "at": data.get("at")}
        )
    except AttributeError, ValueError:
        LOGGER.warning("Ignoring invalid stored meter state %r", data)
        return None
    LOGGER.warning("Keeping only the value of a damaged meter state %r", data)
    return state


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

    The regions are in percent like the flow forms. Only the first
    MAX_DIALS dials in the finder's order are kept. Raises tarsius
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
            {**_percent(roi), CONF_COUNTERCLOCKWISE: False}
            for roi in suggestion.dials[:MAX_DIALS]
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

    def __init__(
        self,
        options: Mapping[str, Any],
        state: PlausibilityState | None,
        exposure: Mapping[str, Any] | None = None,
    ) -> None:
        """Initialize from the entry options, the state and the learnt exposure.

        Raises ValueError for options tarsius refuses, e.g. a maximum rate
        of 0 or a calibration that is out of range. ``state`` is from
        :func:`parse_state`, ``exposure`` from :attr:`exposure`; it is used
        while the calibration it was learnt on is still the entry's.
        """
        self.layout = layout_from_options(options)
        self._source = _fingerprint(options.get(CONF_CALIBRATION))
        self._calibration = _calibration(options) or Calibration()
        self._calibration = _learnt(self._calibration, self._source, exposure)
        self._reader = MeterReader()
        self._plausibility = Plausibility(
            max_rate=float(options[CONF_MAX_RATE]),
            # A rolling last wheel or dial jitters by about one unit.
            tolerance=10.0**-self.layout.decimals,
            # The counter wraps to zero; the value counts on.
            period=self.layout.period,
            state=state,
        )

    @property
    def value(self) -> float | None:
        """Return the last accepted value, None before one was confirmed."""
        if (state := self._plausibility.state) is None:
            return None
        return state.value

    @property
    def state(self) -> dict[str, Any] | None:
        """Return the state to store, None before a value was confirmed."""
        if (state := self._plausibility.state) is None:
            return None
        return state.as_dict()

    @property
    def dispute(self) -> Dispute | None:
        """Return lasting readings that contradict the last value, or None."""
        return self._plausibility.dispute

    @property
    def exposure(self) -> dict[str, Any] | None:
        """Return the exposure learnt from accepted readings, to store."""
        if not self._calibration.exposure:
            return None
        return {
            "source": self._source,
            "light": self._calibration.light,
            "baseline": [item.as_list() for item in self._calibration.exposure],
        }

    def load(self) -> None:
        """Load the readers; blocking, raises tarsius BackendError."""
        self._reader.load()

    def read(self, frame: bytes, at: datetime) -> Readout:
        """Read a frame and check it against the last accepted value; blocking.

        Raises tarsius ImageError, BlankFrameError or BackendError.
        """
        reading = self._reader.read(frame, self.layout, self._calibration)
        reading = self._plausibility.review(reading, at)
        # The look of the wheels follows the scene slowly.
        self._calibration = self._reader.adapt(self._calibration, reading)
        return _readout(reading)

    def correct(self, value: float, at: datetime) -> None:
        """Take a value the user read on the meter as the last accepted one."""
        self._plausibility.accept(value, at)


def _fingerprint(calibration: Any) -> str:
    """Return a short id of the entry's calibration, to match learnt exposure."""
    text = json.dumps(calibration, sort_keys=True, default=str)
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _learnt(
    calibration: Calibration, source: str, exposure: Mapping[str, Any] | None
) -> Calibration:
    """Return the calibration with the stored learnt exposure, if it fits."""
    if not isinstance(exposure, Mapping) or exposure.get("source") != source:
        return calibration
    try:
        baseline = tuple(Exposure(*item) for item in exposure["baseline"])
        return dataclasses.replace(
            calibration, exposure=baseline, light=exposure["light"]
        )
    except KeyError, TypeError, ValueError:
        LOGGER.warning("Ignoring invalid stored exposure %r", exposure)
        return calibration
