"""Binary sensors of a wall control: alarms, status flags and relay outputs."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import WallControlConfigEntry
from .coordinator import WallControlCoordinator
from .entity import WallControlEntity
from .protocol import (
    ALARM_AIR_M4,
    ALARM_AIR_PROBE,
    ALARM_COM,
    ALARM_FILTER,
    ALARM_GRID,
    ALARM_H2_PROBE,
    ALARM_H2_WATER,
    ALARM_H4_PROBE,
    ALARM_H4_WATER,
    ALARM_HARMFUL_WATER,
    ALARM_HIGH_RESISTANCE,
    ALARM_MOTOR,
    OUT_BOILER,
    OUT_CHILLER,
    OUT_EV1,
    OUT_EV2,
    STAT_ANTIFREEZE,
    STAT_COM_ERROR,
    STAT_FAN_STOP_H2,
    STAT_FAN_STOP_H4,
    STAT_H2_ABSENT,
    STAT_H4_ABSENT,
    STAT_TEST,
    STAT_WATER_STOP,
    WallControlState,
)

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class WallControlBinarySensorDescription(BinarySensorEntityDescription):
    """Binary sensor description with a state getter and a capability check."""

    is_on_fn: Callable[[WallControlState], bool]
    supported_fn: Callable[[WallControlState], bool] = lambda _state: True


def _alarm(key: str, bit: int, *, default: bool = False) -> WallControlBinarySensorDescription:
    return WallControlBinarySensorDescription(
        key=key,
        translation_key=key,
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=default,
        is_on_fn=lambda s: bool(s.alarms & bit),
    )


def _status(key: str, bit: int) -> WallControlBinarySensorDescription:
    return WallControlBinarySensorDescription(
        key=key,
        translation_key=key,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        is_on_fn=lambda s: bool(s.status & bit),
    )


def _output(key: str, bit: int) -> WallControlBinarySensorDescription:
    return WallControlBinarySensorDescription(
        key=key,
        translation_key=key,
        device_class=BinarySensorDeviceClass.RUNNING,
        is_on_fn=lambda s: bool((s.outputs or 0) & bit),
        supported_fn=lambda s: s.outputs is not None,  # INN-FR-B32 only
    )


BINARY_SENSORS: tuple[WallControlBinarySensorDescription, ...] = (
    WallControlBinarySensorDescription(
        key="alarm",
        translation_key="alarm",
        device_class=BinarySensorDeviceClass.PROBLEM,
        is_on_fn=lambda s: s.alarm_active,
    ),
    _alarm("filter", ALARM_FILTER, default=True),
    _alarm("motor_fault", ALARM_MOTOR, default=True),
    _alarm("air_probe", ALARM_AIR_PROBE, default=True),
    _alarm("grid_open", ALARM_GRID, default=True),
    _alarm("h2_probe", ALARM_H2_PROBE),
    _alarm("h4_probe", ALARM_H4_PROBE),
    _alarm("harmful_water", ALARM_HARMFUL_WATER),
    _alarm("h2_water_unsuitable", ALARM_H2_WATER),
    _alarm("h4_water_unsuitable", ALARM_H4_WATER),
    _alarm("high_temperature_resistance", ALARM_HIGH_RESISTANCE),
    _alarm("communication_error", ALARM_COM),
    _alarm("air_probe_m4", ALARM_AIR_M4),
    _status("antifreeze", STAT_ANTIFREEZE),
    _status("test_mode", STAT_TEST),
    _status("fan_stop_h2", STAT_FAN_STOP_H2),
    _status("fan_stop_h4", STAT_FAN_STOP_H4),
    _status("water_stop", STAT_WATER_STOP),
    _status("master_timeout", STAT_COM_ERROR),
    _status("h2_probe_absent", STAT_H2_ABSENT),
    _status("h4_probe_absent", STAT_H4_ABSENT),
    _output("valve_1", OUT_EV1),
    _output("valve_2", OUT_EV2),
    _output("chiller_request", OUT_CHILLER),
    _output("boiler_request", OUT_BOILER),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WallControlConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the binary sensors of every wall control."""
    for subentry_id, coordinator in entry.runtime_data.coordinators.items():
        async_add_entities(
            [WallControlBinarySensor(coordinator, description) for description in BINARY_SENSORS],
            config_subentry_id=subentry_id,
        )


class WallControlBinarySensor(WallControlEntity, BinarySensorEntity):
    """A flag read from the wall control."""

    entity_description: WallControlBinarySensorDescription

    def __init__(self, coordinator: WallControlCoordinator, description: WallControlBinarySensorDescription) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def available(self) -> bool:
        """Registers the hardware does not have make their entity unavailable."""
        data = self.coordinator.data
        return super().available and data is not None and self.entity_description.supported_fn(data)

    @property
    def is_on(self) -> bool:
        """Return the flag."""
        return self.entity_description.is_on_fn(self.coordinator.data)
