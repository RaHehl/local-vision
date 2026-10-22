"""Config flow for Local vision."""

from collections.abc import Mapping
from typing import Any, override

import probatio
from tarsius import (
    BackendError,
    BlankFrameError,
    BlankRegionError,
    ImageError,
    RoiError,
    load_image,
)

from homeassistant.components.camera import DOMAIN as CAMERA_DOMAIN
from homeassistant.config_entries import (
    SOURCE_RECONFIGURE,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import (
    CONF_DEVICE_CLASS,
    CONF_ENTITY_ID,
    CONF_NAME,
    CONF_REGION,
    CONF_STATE,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import selector, translation
from homeassistant.util import dt as dt_util

from .const import (
    CONF_BOTTOM,
    CONF_CALIBRATION,
    CONF_CONFIRMED,
    CONF_COUNTERCLOCKWISE,
    CONF_DECIMAL_WHEELS,
    CONF_DIAL_COUNT,
    CONF_DIALS,
    CONF_DIGITS,
    CONF_FRAMES,
    CONF_LEFT,
    CONF_MAX_RATE,
    CONF_RIGHT,
    CONF_ROTATION,
    CONF_TOP,
    CONF_TYPE,
    CONF_UNIT,
    CONF_VALUE,
    CONF_WHEELS,
    DEFAULT_DEVICE_CLASS,
    DEFAULT_MAX_RATES,
    DEFAULT_REGION,
    DEVICE_CLASSES,
    DOMAIN,
    LOGGER,
    MAX_DIALS,
    MAX_WHEELS,
    METER_DEVICE_CLASSES,
    METER_UNITS,
    STATES,
    TYPE_METER,
    TYPE_STATE,
)
from .coordinator import (
    LocalVisionConfigEntry,
    MeterConfigEntry,
    StateConfigEntry,
    StateCoordinator,
)
from .detection import ROI_ERRORS, async_check_frame, region_to_roi
from .frame_source import FrameUnavailable, async_fetch_frame
from .meter_reader import (
    PREVIEW_SIDE,
    calibrate,
    layout_rois,
    read_once,
    rotate,
    suggest,
    value_scale,
)
from .preview import async_preview_url
from .taught_frames import EntryClosed

PERCENT = selector.NumberSelector(
    selector.NumberSelectorConfig(
        min=0,
        max=100,
        step=0.1,
        mode=selector.NumberSelectorMode.BOX,
        unit_of_measurement="%",
    )
)

CAMERA_SELECTOR = selector.EntitySelector(
    selector.EntitySelectorConfig(domain=CAMERA_DOMAIN)
)

STATE_SCHEMA = probatio.Schema(
    {
        probatio.Required(CONF_ENTITY_ID): CAMERA_SELECTOR,
        probatio.Required(
            CONF_DEVICE_CLASS, default=DEFAULT_DEVICE_CLASS
        ): selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=list(DEVICE_CLASSES),
                mode=selector.SelectSelectorMode.DROPDOWN,
                translation_key="device_class",
            )
        ),
        probatio.Required(CONF_NAME): selector.TextSelector(),
    }
)

REGION_SCHEMA = probatio.Schema(
    {
        probatio.Required(CONF_LEFT): PERCENT,
        probatio.Required(CONF_TOP): PERCENT,
        probatio.Required(CONF_RIGHT): PERCENT,
        probatio.Required(CONF_BOTTOM): PERCENT,
    }
)


METER_SCHEMA = probatio.Schema(
    {
        probatio.Required(CONF_ENTITY_ID): CAMERA_SELECTOR,
        probatio.Required(
            CONF_DEVICE_CLASS, default=METER_DEVICE_CLASSES[0]
        ): selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=list(METER_DEVICE_CLASSES),
                mode=selector.SelectSelectorMode.LIST,
                translation_key="meter_device_class",
            )
        ),
        probatio.Required(CONF_NAME): selector.TextSelector(),
    }
)


def _count(minimum: int, maximum: int) -> selector.NumberSelector:
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=minimum, max=maximum, step=1, mode=selector.NumberSelectorMode.BOX
        )
    )


DIGITS_SCHEMA = REGION_SCHEMA.extend(
    {
        probatio.Required(CONF_WHEELS): _count(1, MAX_WHEELS),
        probatio.Required(CONF_DECIMAL_WHEELS, default=0): _count(0, MAX_WHEELS),
        probatio.Required(CONF_DIAL_COUNT, default=0): _count(0, MAX_DIALS),
        probatio.Required(CONF_ROTATION, default=0): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=-180,
                max=180,
                step=0.1,
                mode=selector.NumberSelectorMode.BOX,
                unit_of_measurement="°",
            )
        ),
    }
)

DIAL_SCHEMA = REGION_SCHEMA.extend(
    {
        probatio.Required(
            CONF_COUNTERCLOCKWISE, default=False
        ): selector.BooleanSelector(),
    }
)


def _unit_schema(device_class: str) -> probatio.Schema:
    units = METER_UNITS[device_class]
    return probatio.Schema(
        {
            probatio.Required(CONF_UNIT, default=units[0]): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=units, mode=selector.SelectSelectorMode.DROPDOWN
                )
            ),
            # Above 0, checked in the step: the selector's minimum is inclusive.
            probatio.Optional(CONF_MAX_RATE): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0, step="any", mode=selector.NumberSelectorMode.BOX
                )
            ),
        }
    )


VALUE_SCHEMA = probatio.Schema(
    {
        probatio.Required(CONF_VALUE): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0, step="any", mode=selector.NumberSelectorMode.BOX
            )
        )
    }
)


def _same_scale(old: Mapping[str, Any], new: Mapping[str, Any]) -> bool:
    """Return True when both options read the same number in the same unit."""
    if old[CONF_UNIT] != new[CONF_UNIT]:
        return False
    try:
        return value_scale(old) == value_scale(new)
    except ValueError:
        return False


def _confirmed(value: float) -> dict[str, Any]:
    return {CONF_VALUE: value, "at": dt_util.utcnow().isoformat()}


def _region(user_input: Mapping[str, Any]) -> dict[str, float]:
    return {
        side: user_input[side]
        for side in (CONF_LEFT, CONF_TOP, CONF_RIGHT, CONF_BOTTOM)
    }


def _region_error(region: Mapping[str, float]) -> str | None:
    try:
        region_to_roi(region)
    except RoiError as err:
        return ROI_ERRORS[err.axis]
    return None


def _teach_schema(device_class: str) -> probatio.Schema:
    return probatio.Schema(
        {
            probatio.Required(CONF_STATE): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=list(STATES),
                    mode=selector.SelectSelectorMode.LIST,
                    # Labels follow the kind, e.g. open / closed for a door.
                    translation_key=f"state_{device_class}",
                )
            )
        }
    )


async def _async_frame_error(
    hass: HomeAssistant, frame: bytes, region: Mapping[str, float]
) -> str | None:
    """Return the error key when the frame can't be taught with the region."""
    try:
        await async_check_frame(hass, frame, region)
    except ImageError:
        return "invalid_frame"
    except RoiError as err:
        return ROI_ERRORS[err.axis]
    except BlankFrameError:
        return "blank_frame"
    except BlankRegionError:
        return "blank_region"
    except BackendError:
        LOGGER.exception("The recognition model failed")
        return "unknown"
    return None


class LocalVisionConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up a camera region as a sensor."""

    VERSION = 1
    MINOR_VERSION = 3

    def __init__(self) -> None:
        """Initialize the flow."""
        self._options: dict[str, Any] = {}
        self._frame = b""
        # Regions to suggest in the forms, from the finder or a former pass.
        self._suggested: dict[str, Any] = {}
        self._dial = 0
        # What the meter read in the confirm step, suggested as its value
        # when the reading was accepted.
        self._candidate: float | None = None

    @staticmethod
    @callback
    @override
    def async_get_options_flow(
        config_entry: LocalVisionConfigEntry,
    ) -> OptionsFlow:
        """Get the options flow."""
        if config_entry.options[CONF_TYPE] == TYPE_METER:
            return MeterOptionsFlow()
        return StateOptionsFlow()

    @override
    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose what to recognize."""
        return self.async_show_menu(
            step_id="user", menu_options=[TYPE_STATE, TYPE_METER]
        )

    async def async_step_state(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick the camera."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                frame = await async_fetch_frame(self.hass, user_input[CONF_ENTITY_ID])
            except FrameUnavailable:
                errors["base"] = "cannot_connect"
            else:
                if error := await _async_frame_error(self.hass, frame, DEFAULT_REGION):
                    # A whole frame without detail shows nothing.
                    errors["base"] = "blank_frame" if error == "blank_region" else error
                else:
                    self._frame = frame
                    self._options = {CONF_TYPE: TYPE_STATE, **user_input}
                    return await self.async_step_region()
        return self.async_show_form(
            step_id="state",
            data_schema=self.add_suggested_values_to_schema(STATE_SCHEMA, user_input),
            errors=errors,
        )

    async def async_step_region(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Set the region in percent of the frame."""
        errors: dict[str, str] = {}
        if user_input is not None:
            if error := await _async_frame_error(self.hass, self._frame, user_input):
                errors["base"] = error
            else:
                self._options[CONF_REGION] = user_input
                return await self.async_step_region_confirm()
        region = user_input or self._options.get(CONF_REGION, DEFAULT_REGION)
        try:
            rois = [region_to_roi(region)]
        except RoiError:
            rois = []
        return self.async_show_form(
            step_id="region",
            data_schema=self.add_suggested_values_to_schema(REGION_SCHEMA, region),
            errors=errors,
            description_placeholders={
                "preview": await async_preview_url(self.hass, self._frame, rois)
            },
        )

    async def async_step_region_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the region on the current frame."""
        url = await async_preview_url(
            self.hass, self._frame, region_to_roi(self._options[CONF_REGION])
        )
        return self.async_show_menu(
            step_id="region_confirm",
            menu_options=["finish", "region"],
            description_placeholders={"preview": url},
        )

    async def async_step_finish(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Create the entry."""
        name = self._options.pop(CONF_NAME)
        return self.async_create_entry(title=name, data={}, options=self._options)

    async def async_step_meter(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick the camera that shows the meter."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                frame = await async_fetch_frame(self.hass, user_input[CONF_ENTITY_ID])
            except FrameUnavailable:
                errors["base"] = "cannot_connect"
            else:
                try:
                    await self.hass.async_add_executor_job(load_image, frame)
                except ImageError:
                    errors["base"] = "invalid_frame"
                else:
                    self._frame = frame
                    self._options = {CONF_TYPE: TYPE_METER, **user_input}
                    return await self.async_step_meter_unit()
        return self.async_show_form(
            step_id="meter",
            data_schema=self.add_suggested_values_to_schema(METER_SCHEMA, user_input),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the unit, the maximum rate and the regions of a meter.

        The value and its checks are kept unless the counter reads a
        different number afterwards, then the value is asked again.
        """
        entry = self._get_reconfigure_entry()
        if entry.options[CONF_TYPE] != TYPE_METER:
            return self.async_abort(reason="reconfigure_state")
        try:
            frame = await async_fetch_frame(self.hass, entry.options[CONF_ENTITY_ID])
        except FrameUnavailable:
            return self.async_abort(reason="cannot_connect")
        try:
            await self.hass.async_add_executor_job(load_image, frame)
        except ImageError:
            return self.async_abort(reason="invalid_frame")
        self._frame = frame
        self._options = dict(entry.options)
        self._suggested = {
            key: entry.options[key]
            for key in (
                CONF_DIGITS,
                CONF_ROTATION,
                CONF_WHEELS,
                CONF_DECIMAL_WHEELS,
                CONF_DIALS,
            )
        }
        return await self.async_step_meter_unit()

    async def async_step_meter_unit(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose the unit the meter counts in."""
        device_class = self._options[CONF_DEVICE_CLASS]
        errors: dict[str, str] = {}
        if user_input is not None:
            unit = user_input[CONF_UNIT]
            max_rate = user_input.get(CONF_MAX_RATE)
            if max_rate is not None and not max_rate > 0:
                errors[CONF_MAX_RATE] = "invalid_max_rate"
            else:
                self._options[CONF_UNIT] = unit
                self._options[CONF_MAX_RATE] = (
                    DEFAULT_MAX_RATES[device_class][unit]
                    if max_rate is None
                    else max_rate
                )
                return await self.async_step_meter_regions()
        suggested = user_input
        if suggested is None and self.source == SOURCE_RECONFIGURE:
            suggested = {
                CONF_UNIT: self._options[CONF_UNIT],
                CONF_MAX_RATE: self._options[CONF_MAX_RATE],
            }
        return self.async_show_form(
            step_id="meter_unit",
            data_schema=self.add_suggested_values_to_schema(
                _unit_schema(device_class), suggested
            ),
            errors=errors,
        )

    async def async_step_meter_regions(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Offer the region suggestion before the forms."""
        return self.async_show_menu(
            step_id="meter_regions",
            # Finding the counter is the easy way; the forms stay open after it.
            menu_options=["suggest", "digits"],
            description_placeholders={
                "preview": await self._async_meter_preview(self._suggested)
            },
        )

    async def async_step_suggest(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the alpha finder fill in the regions."""
        try:
            suggestion = await self.hass.async_add_executor_job(suggest, self._frame)
        except Exception:
            LOGGER.exception("The region finder failed")
            suggestion = None
        if suggestion is None:
            return await self.async_step_digits(errors={"base": "no_suggestion"})
        self._suggested = suggestion
        return await self.async_step_digits()

    async def async_step_digits(
        self,
        user_input: dict[str, Any] | None = None,
        errors: dict[str, str] | None = None,
    ) -> ConfigFlowResult:
        """Set the window with the counter wheels."""
        errors = errors or {}
        if user_input is not None:
            region = _region(user_input)
            wheels = int(user_input[CONF_WHEELS])
            decimal_wheels = int(user_input[CONF_DECIMAL_WHEELS])
            if error := _region_error(region):
                errors["base"] = error
            elif decimal_wheels > wheels:
                errors["base"] = "too_many_decimal_wheels"
            else:
                digits = {
                    CONF_DIGITS: region,
                    CONF_ROTATION: user_input[CONF_ROTATION],
                    CONF_WHEELS: wheels,
                    CONF_DECIMAL_WHEELS: decimal_wheels,
                }
                self._options |= {**digits, CONF_DIALS: []}
                # Former dials stay suggested for the dial forms.
                dial_count = int(user_input[CONF_DIAL_COUNT])
                dials = [*self._suggested.get(CONF_DIALS, []), *[{}] * dial_count]
                self._suggested |= {**digits, CONF_DIALS: dials[:dial_count]}
                self._dial = 0
                if dial_count:
                    return await self.async_step_dial()
                return await self.async_step_meter_confirm()
        suggested = user_input or {
            **self._suggested.get(CONF_DIGITS, DEFAULT_REGION),
            CONF_ROTATION: self._suggested.get(CONF_ROTATION, 0),
            CONF_WHEELS: self._suggested.get(CONF_WHEELS),
            CONF_DECIMAL_WHEELS: self._suggested.get(CONF_DECIMAL_WHEELS, 0),
            CONF_DIAL_COUNT: len(self._suggested.get(CONF_DIALS, [])),
        }
        region = _region(suggested)
        preview: dict[str, Any] = {CONF_ROTATION: suggested[CONF_ROTATION]}
        if _region_error(region) is None:
            preview[CONF_DIGITS] = region
        return self.async_show_form(
            step_id="digits",
            data_schema=self.add_suggested_values_to_schema(DIGITS_SCHEMA, suggested),
            errors=errors,
            description_placeholders={
                "preview": await self._async_meter_preview(preview)
            },
        )

    async def async_step_dial(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Set the region of one pointer dial."""
        errors: dict[str, str] = {}
        dials = self._options[CONF_DIALS]
        if user_input is not None:
            if error := _region_error(_region(user_input)):
                errors["base"] = error
            else:
                dials.append(user_input)
                self._suggested[CONF_DIALS][self._dial] = user_input
                self._dial += 1
                if self._dial < len(self._suggested[CONF_DIALS]):
                    return await self.async_step_dial()
                return await self.async_step_meter_confirm()
        suggested = user_input or self._suggested[CONF_DIALS][self._dial]
        return self.async_show_form(
            step_id="dial",
            data_schema=self.add_suggested_values_to_schema(DIAL_SCHEMA, suggested),
            errors=errors,
            description_placeholders={
                "number": str(self._dial + 1),
                "count": str(len(self._suggested[CONF_DIALS])),
                "preview": await self._async_meter_preview(self._options),
            },
        )

    async def async_step_meter_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the regions and what the meter reads now."""
        self._options.pop(CONF_CALIBRATION, None)
        self._candidate = None
        try:
            calibration = await self.hass.async_add_executor_job(
                calibrate, self._frame, self._options
            )
            reading = await self.hass.async_add_executor_job(
                read_once, self._frame, {**self._options, CONF_CALIBRATION: calibration}
            )
        except ImageError, BlankFrameError:
            result = await self._async_status_label("blank")
        except BackendError:
            LOGGER.exception("The meter reader failed")
            result = await self._async_status_label("unknown")
        else:
            self._options[CONF_CALIBRATION] = calibration
            # Only a reading the reader accepted is offered as the value.
            self._candidate = reading.value
            result = f"{reading.candidate} ({await self._async_status_label(reading.status)})"
        keep_value = self.source == SOURCE_RECONFIGURE and self._keeps_value()
        return self.async_show_menu(
            step_id="meter_confirm",
            menu_options=["meter_finish" if keep_value else "meter_value", "digits"],
            description_placeholders={
                "preview": await self._async_meter_preview(self._options),
                "reading": result,
            },
        )

    def _keeps_value(self) -> bool:
        """Return True when a reconfigured meter can keep its value."""
        entry: MeterConfigEntry = self._get_reconfigure_entry()
        if entry.state is ConfigEntryState.LOADED:
            has_value = entry.runtime_data.value is not None
        else:
            has_value = CONF_CONFIRMED in entry.options
        return has_value and _same_scale(entry.options, self._options)

    async def async_step_meter_value(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm the value on the meter; readings are checked against it."""
        if user_input is not None:
            self._options[CONF_CONFIRMED] = _confirmed(user_input[CONF_VALUE])
            return await self.async_step_meter_finish()
        return self.async_show_form(
            step_id="meter_value",
            data_schema=self.add_suggested_values_to_schema(
                VALUE_SCHEMA,
                {} if self._candidate is None else {CONF_VALUE: self._candidate},
            ),
        )

    async def async_step_meter_finish(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Create the meter entry, or update it when reconfiguring."""
        if self.source == SOURCE_RECONFIGURE:
            return self.async_update_reload_and_abort(
                self._get_reconfigure_entry(), options=self._options
            )
        name = self._options.pop(CONF_NAME)
        return self.async_create_entry(
            title=name, data={}, options=self._options, description="meter"
        )

    async def _async_meter_preview(self, options: Mapping[str, Any]) -> str:
        frame = self._frame
        if rotation := options.get(CONF_ROTATION):
            frame = await self.hass.async_add_executor_job(rotate, frame, rotation)
        return await async_preview_url(
            self.hass, frame, layout_rois(options), max_side=PREVIEW_SIDE
        )

    async def _async_status_label(self, key: str) -> str:
        translations = await translation.async_get_translations(
            self.hass, self.hass.config.language, "selector", {DOMAIN}
        )
        return translations.get(
            f"component.{DOMAIN}.selector.reading_status.options.{key}", key
        )


class StateOptionsFlow(OptionsFlow):
    """Teach and remove reference frames."""

    config_entry: StateConfigEntry

    def __init__(self) -> None:
        """Initialize the flow."""
        self._frame = b""

    def _coordinator(self) -> StateCoordinator | None:
        """Return the coordinator, None while the entry is not loaded."""
        if self.config_entry.state is not ConfigEntryState.LOADED:
            return None
        return self.config_entry.runtime_data

    async def _state_labels(self) -> dict[str, str]:
        device_class = self.config_entry.options[CONF_DEVICE_CLASS]
        translations = await translation.async_get_translations(
            self.hass, self.hass.config.language, "selector", {DOMAIN}
        )
        prefix = f"component.{DOMAIN}.selector.state_{device_class}.options"
        return {state: translations.get(f"{prefix}.{state}", state) for state in STATES}

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose what to do."""
        if (coordinator := self._coordinator()) is None:
            return self.async_abort(reason="not_loaded")
        menu = ["teach"]
        if coordinator.taught_frames.frames:
            menu.append("remove")
        return self.async_show_menu(step_id="init", menu_options=menu)

    async def async_step_teach(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Store the shown frame as a reference."""
        # The entry may have reloaded while the dialog was open.
        if (coordinator := self._coordinator()) is None:
            return self.async_abort(reason="not_loaded")
        taught_frames = coordinator.taught_frames
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                await taught_frames.async_teach(self._frame, user_input[CONF_STATE])
            except EntryClosed:
                return self.async_abort(reason="not_loaded")
            # The frame decoded for the preview, so it decodes here too.
            except BlankFrameError:
                errors["base"] = "blank_frame"
            except BlankRegionError:
                errors["base"] = "blank_region"
            except BackendError:
                LOGGER.exception("The recognition model failed")
                errors["base"] = "unknown"
            else:
                await coordinator.async_request_refresh()
                return self.async_abort(reason="teach_successful")
        try:
            frame = await async_fetch_frame(
                self.hass, self.config_entry.options[CONF_ENTITY_ID]
            )
        except FrameUnavailable:
            return self.async_abort(reason="cannot_connect")
        try:
            url = await async_preview_url(
                self.hass,
                frame,
                taught_frames.detector.roi,
                max_side=taught_frames.detector.frame_side,
            )
        except ImageError:
            return self.async_abort(reason="invalid_frame")
        self._frame = frame
        labels = await self._state_labels()
        counts = taught_frames.detector.counts()
        return self.async_show_form(
            step_id="teach",
            data_schema=_teach_schema(self.config_entry.options[CONF_DEVICE_CLASS]),
            errors=errors,
            description_placeholders={
                "preview": url,
                "counts": ", ".join(
                    f"{labels[state]}: {counts.get(state, 0)}" for state in STATES
                ),
            },
        )

    async def async_step_remove(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Delete taught frames."""
        if (coordinator := self._coordinator()) is None:
            return self.async_abort(reason="not_loaded")
        taught_frames = coordinator.taught_frames
        errors: dict[str, str] = {}
        if user_input is not None:
            if not user_input[CONF_FRAMES]:
                errors["base"] = "nothing_selected"
            else:
                try:
                    await taught_frames.async_remove(user_input[CONF_FRAMES])
                except EntryClosed:
                    return self.async_abort(reason="not_loaded")
                await coordinator.async_request_refresh()
                return self.async_abort(reason="remove_successful")
        labels = await self._state_labels()
        options = [
            selector.SelectOptionDict(
                value=frame.id,
                label=f"{labels[frame.label]}, {dt_util.as_local(frame.created):%Y-%m-%d %H:%M}",
            )
            for frame in taught_frames.frames
        ]
        return self.async_show_form(
            step_id="remove",
            data_schema=probatio.Schema(
                {
                    probatio.Required(CONF_FRAMES): selector.SelectSelector(
                        selector.SelectSelectorConfig(options=options, multiple=True)
                    )
                }
            ),
            errors=errors,
        )


class MeterOptionsFlow(OptionsFlow):
    """Correct the value of a meter."""

    config_entry: MeterConfigEntry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Go to the only option."""
        return await self.async_step_correct()

    async def async_step_correct(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Set the value shown on the meter."""
        if self.config_entry.state is not ConfigEntryState.LOADED:
            return self.async_abort(reason="not_loaded")
        coordinator = self.config_entry.runtime_data
        if user_input is not None:
            await coordinator.async_correct(user_input[CONF_VALUE])
            return self.async_abort(reason="correct_successful")
        return self.async_show_form(
            step_id="correct",
            data_schema=self.add_suggested_values_to_schema(
                VALUE_SCHEMA,
                {} if coordinator.value is None else {CONF_VALUE: coordinator.value},
            ),
        )
