"""Sensors of a wall control."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import WallControlConfigEntry
from .coordinator import WallControlCoordinator
from .entity import WallControlEntity
from .protocol import WallControlState

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class WallControlSensorDescription(SensorEntityDescription):
    """Sensor description with a value getter and a capability check."""

    value_fn: Callable[[WallControlState], str | int | float | None]
    supported_fn: Callable[[WallControlState], bool] = lambda _state: True


SENSORS: tuple[WallControlSensorDescription, ...] = (
    WallControlSensorDescription(
        key="air_temperature",
        translation_key="air_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        value_fn=lambda s: s.current_temperature,
    ),
    WallControlSensorDescription(
        key="water_temperature",
        translation_key="water_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        value_fn=lambda s: s.water_temperature,
        supported_fn=lambda s: s.water_temperature is not None,  # INN-FR-B32 only
    ),
    WallControlSensorDescription(
        key="active_setpoint",
        translation_key="active_setpoint",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        value_fn=lambda s: s.active_setpoint,
    ),
    WallControlSensorDescription(
        key="motor_speed",
        translation_key="motor_speed",
        native_unit_of_measurement="rpm",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda s: s.motor_speed,
        supported_fn=lambda s: s.motor_speed is not None,  # INN-FR-B32 only
    ),
    WallControlSensorDescription(
        key="active_mode",
        translation_key="active_mode",
        device_class=SensorDeviceClass.ENUM,
        options=["cooling", "heating"],
        value_fn=lambda s: "cooling" if s.cooling_active else "heating" if s.heating_active else None,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WallControlConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the sensors of every wall control."""
    for subentry_id, coordinator in entry.runtime_data.coordinators.items():
        async_add_entities(
            [WallControlSensor(coordinator, description) for description in SENSORS],
            config_subentry_id=subentry_id,
        )


class WallControlSensor(WallControlEntity, SensorEntity):
    """A value read from the wall control."""

    entity_description: WallControlSensorDescription

    def __init__(self, coordinator: WallControlCoordinator, description: WallControlSensorDescription) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def available(self) -> bool:
        """Registers the hardware does not have make their sensor unavailable."""
        data = self.coordinator.data
        return super().available and data is not None and self.entity_description.supported_fn(data)

    @property
    def native_value(self) -> str | int | float | None:
        """Return the value."""
        return self.entity_description.value_fn(self.coordinator.data)
