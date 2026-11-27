"""Base entity shared by all platforms."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import WallControlCoordinator


class WallControlEntity(CoordinatorEntity[WallControlCoordinator]):
    """An entity belonging to one wall control."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: WallControlCoordinator, key: str) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        # The subentry id is stable even if the user changes the slave id later.
        self._attr_unique_id = f"{coordinator.subentry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.subentry_id)},
            name=coordinator.unit_name,
            manufacturer="Daikin",
            model="EKWHCTRL1 / EKRTCTRL1 wall control",
            via_device=(DOMAIN, coordinator.entry_id),
        )
