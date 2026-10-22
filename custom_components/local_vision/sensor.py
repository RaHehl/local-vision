"""Sensors for Local vision meter readings."""

from typing import Any, override

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import (
    CONF_DEVICE_CLASS,
    CONF_ENTITY_ID,
    PERCENTAGE,
    EntityCategory,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device import async_entity_id_to_device
from homeassistant.helpers.device_registry import AnyDeviceEntry
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_UNIT
from .coordinator import MeterConfigEntry, MeterCoordinator
from .meter_reader import STATUSES

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MeterConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the meter sensors."""
    coordinator = entry.runtime_data
    device = async_entity_id_to_device(hass, entry.options[CONF_ENTITY_ID])
    async_add_entities(
        [
            MeterValueSensor(coordinator, device),
            MeterStatusSensor(coordinator, device),
            MeterConfidenceSensor(coordinator, device),
        ]
    )


class MeterEntity(CoordinatorEntity[MeterCoordinator]):
    """An entity of a meter, added to the device of its camera."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: MeterCoordinator, device: AnyDeviceEntry | None
    ) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self.device_entry = device


class MeterValueSensor(MeterEntity, SensorEntity):
    """The last accepted reading.

    A total rather than total_increasing: a corrected or re-agreed lower
    value then counts as a negative change and nets out the wrong rise,
    instead of starting a new meter cycle.
    """

    _attr_state_class = SensorStateClass.TOTAL

    def __init__(
        self, coordinator: MeterCoordinator, device: AnyDeviceEntry | None
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, device)
        entry = coordinator.config_entry
        self._attr_name = entry.title
        self._attr_unique_id = entry.entry_id
        self._attr_device_class = SensorDeviceClass(entry.options[CONF_DEVICE_CLASS])
        self._attr_native_unit_of_measurement = entry.options[CONF_UNIT]
        self._attr_suggested_display_precision = coordinator.session.layout.decimals

    @property
    @override
    def native_value(self) -> float | None:
        """Return the last accepted value."""
        return self.coordinator.value


class MeterDiagnosticSensor(MeterEntity, SensorEntity):
    """A diagnostic sensor about the last reading."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self, coordinator: MeterCoordinator, device: AnyDeviceEntry | None
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, device)
        entry = coordinator.config_entry
        self._attr_unique_id = f"{entry.entry_id}_{self.translation_key}"
        self._attr_translation_placeholders = {"meter": entry.title}


class MeterStatusSensor(MeterDiagnosticSensor):
    """Whether the last reading was accepted, or why not."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = STATUSES
    _attr_translation_key = "reading_status"

    @property
    @override
    def native_value(self) -> str | None:
        """Return the status, None without a reading."""
        if (reading := self.coordinator.data) is None:
            return None
        return reading.status

    @property
    @override
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return the value read, also when it was rejected."""
        if (reading := self.coordinator.data) is None:
            return None
        return {"candidate": reading.candidate}


class MeterConfidenceSensor(MeterDiagnosticSensor):
    """How sure the least sure wheel or dial of the last reading was."""

    _attr_entity_registry_enabled_default = False
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_suggested_display_precision = 0
    _attr_translation_key = "reading_confidence"

    @property
    @override
    def native_value(self) -> float | None:
        """Return the confidence in percent, None without a reading."""
        if (reading := self.coordinator.data) is None:
            return None
        return reading.confidence * 100
