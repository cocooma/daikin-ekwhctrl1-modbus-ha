"""Daikin EKWHCTRL1 Modbus Home Assistant Integration integration.

One config entry is one Modbus TCP gateway; every wall control behind it is a
"wall_control" subentry with its own Modbus slave address.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr

from .client import Framing, ModbusConnectionError, ModbusGateway
from .const import CONF_FRAMING, CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL, DOMAIN, SUBENTRY_WALL_CONTROL
from .coordinator import WallControlCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.CLIMATE, Platform.SENSOR, Platform.BINARY_SENSOR, Platform.NUMBER, Platform.SWITCH]


@dataclass
class GatewayData:
    """Runtime data of one gateway entry."""

    gateway: ModbusGateway
    coordinators: dict[str, WallControlCoordinator] = field(default_factory=dict)  # keyed by subentry id


type WallControlConfigEntry = ConfigEntry[GatewayData]


async def async_setup_entry(hass: HomeAssistant, entry: WallControlConfigEntry) -> bool:
    """Connect to the gateway and set up one coordinator per wall control."""
    gateway = ModbusGateway(
        entry.data[CONF_HOST],
        entry.data[CONF_PORT],
        Framing(entry.data[CONF_FRAMING]),
    )
    try:
        await gateway.connect()
    except ModbusConnectionError as err:
        raise ConfigEntryNotReady(str(err)) from err
    entry.async_on_unload(gateway.close)

    scan_interval = entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    coordinators = {
        subentry.subentry_id: WallControlCoordinator(hass, entry, subentry, gateway, scan_interval)
        for subentry in entry.subentries.values()
        if subentry.subentry_type == SUBENTRY_WALL_CONTROL
    }
    entry.runtime_data = GatewayData(gateway, coordinators)

    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        name=entry.title,
        manufacturer="Daikin",
        model="Modbus gateway",
    )

    # A unit that does not answer must not keep the others from loading:
    # its entities simply show as unavailable until it responds.
    await asyncio.gather(*(coordinator.async_refresh() for coordinator in coordinators.values()))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload_on_change))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: WallControlConfigEntry) -> bool:
    """Unload the platforms (the gateway connection closes via on_unload)."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_reload_on_change(hass: HomeAssistant, entry: WallControlConfigEntry) -> None:
    """Reload when the gateway settings or its list of wall controls change."""
    await hass.config_entries.async_reload(entry.entry_id)
