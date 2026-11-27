"""Lock and restriction flags of a wall control as switches."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import WallControlConfigEntry
from .coordinator import WallControlCoordinator
from .entity import WallControlEntity
from .protocol import (
    PRG_KEYBOARD_LOCK,
    REG_PROGRAM,
    REG_WEB,
    WEB_FORCED_OFF,
    WEB_INHIBIT_EXTREMES,
    WEB_LED_OFF,
    WEB_LOCK_KEYS,
    WEB_LOCK_PROGRAM,
    WEB_LOCK_SEASON,
    WEB_LOCK_STANDBY,
    WEB_SETPOINT_RESTRICTION,
    WallControlState,
)

PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class WallControlSwitchDescription(SwitchEntityDescription):
    """A single bit of a flag register."""

    address: int
    field: str  # name of the state field that holds the register
    bit: int


def _web(key: str, bit: int) -> WallControlSwitchDescription:
    return WallControlSwitchDescription(
        key=key,
        translation_key=key,
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        address=REG_WEB,
        field="web_flags",
        bit=bit,
    )


SWITCHES: tuple[WallControlSwitchDescription, ...] = (
    WallControlSwitchDescription(
        key="keyboard_lock",
        translation_key="keyboard_lock",
        entity_category=EntityCategory.CONFIG,
        address=REG_PROGRAM,
        field="program_register",
        bit=PRG_KEYBOARD_LOCK,
    ),
    _web("web_led_off", WEB_LED_OFF),
    _web("web_forced_off", WEB_FORCED_OFF),
    _web("web_lock_program", WEB_LOCK_PROGRAM),
    _web("web_lock_standby", WEB_LOCK_STANDBY),
    _web("web_inhibit_extremes", WEB_INHIBIT_EXTREMES),
    _web("web_setpoint_restriction", WEB_SETPOINT_RESTRICTION),
    _web("web_lock_keys", WEB_LOCK_KEYS),
    _web("web_lock_season", WEB_LOCK_SEASON),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WallControlConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the switches of every wall control."""
    for subentry_id, coordinator in entry.runtime_data.coordinators.items():
        async_add_entities(
            [WallControlSwitch(coordinator, description) for description in SWITCHES],
            config_subentry_id=subentry_id,
        )


class WallControlSwitch(WallControlEntity, SwitchEntity):
    """One flag bit."""

    entity_description: WallControlSwitchDescription

    def __init__(self, coordinator: WallControlCoordinator, description: WallControlSwitchDescription) -> None:
        """Initialize the switch."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def available(self) -> bool:
        """Unavailable while the unit is unreachable."""
        return super().available and self.coordinator.data is not None

    @property
    def is_on(self) -> bool:
        """Return the flag."""
        description = self.entity_description
        return bool(getattr(self.coordinator.data, description.field) & description.bit)

    async def _set(self, on: bool) -> None:
        description = self.entity_description
        await self.coordinator.async_update_bits(
            description.address,
            description.field,
            set_mask=description.bit if on else 0,
            clear_mask=0 if on else description.bit,
        )

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Set the flag."""
        await self._set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Clear the flag."""
        await self._set(False)
