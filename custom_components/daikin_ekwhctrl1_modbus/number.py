"""Configuration registers of a wall control as numbers."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberEntityDescription, NumberMode
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import WallControlConfigEntry
from .coordinator import WallControlCoordinator
from .entity import WallControlEntity
from .protocol import CONFIG_REGISTERS, ConfigRegister, encode_config

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WallControlConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add a number for every configuration register of every wall control."""
    for subentry_id, coordinator in entry.runtime_data.coordinators.items():
        async_add_entities(
            [WallControlNumber(coordinator, spec) for spec in CONFIG_REGISTERS],
            config_subentry_id=subentry_id,
        )


class WallControlNumber(WallControlEntity, NumberEntity):
    """One configuration register."""

    _attr_mode = NumberMode.BOX

    def __init__(self, coordinator: WallControlCoordinator, spec: ConfigRegister) -> None:
        """Initialize the number."""
        super().__init__(coordinator, f"config_{spec.key}")
        self._spec = spec
        self.entity_description = NumberEntityDescription(
            key=spec.key,
            translation_key=spec.key,
            entity_category=EntityCategory.CONFIG,
            entity_registry_enabled_default=not spec.board_only,
            native_min_value=spec.minimum,
            native_max_value=spec.maximum,
            native_step=spec.scale,
            native_unit_of_measurement=spec.unit,
        )

    @property
    def available(self) -> bool:
        """Registers the hardware does not have (INN-FR-B32 only ones) are unavailable."""
        data = self.coordinator.data
        return super().available and data is not None and self._spec.key in data.config

    @property
    def native_value(self) -> float | None:
        """Return the value."""
        return self.coordinator.data.config.get(self._spec.key)

    async def async_set_native_value(self, value: float) -> None:
        """Write the register."""
        try:
            raw = encode_config(self._spec, value)
        except ValueError as err:
            raise ServiceValidationError(str(err)) from err
        data = self.coordinator.data
        await self.coordinator.async_write(
            self._spec.address, raw, config={**data.config, self._spec.key: round(value, 1)}
        )
