"""Data update coordinator: one per wall control, sharing its gateway's connection."""

from __future__ import annotations

import dataclasses
from datetime import timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .client import ModbusError, ModbusExceptionResponse, ModbusGateway
from .const import CONF_SLAVE_ID, CONFIG_POLL_EVERY, DOMAIN
from .protocol import (
    CONFIG_REGISTERS,
    OPTIONAL_REGISTERS,
    REQUIRED_REGISTERS,
    WallControlState,
    apply_bits,
    decode_config,
    parse_state,
)

_LOGGER = logging.getLogger(__name__)


class WallControlCoordinator(DataUpdateCoordinator[WallControlState]):
    """Polls one wall control and sends commands to it.

    The device implements only "read one register" (03) and "write one
    register" (06), so every register is a request of its own.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        subentry: ConfigSubentry,
        gateway: ModbusGateway,
        scan_interval: int,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {subentry.data[CONF_NAME]}",
            update_interval=timedelta(seconds=scan_interval),
        )
        self.gateway = gateway
        self.entry_id = entry.entry_id
        self.subentry_id = subentry.subentry_id
        self.unit_name: str = subentry.data[CONF_NAME]
        self.slave_id: int = int(subentry.data[CONF_SLAVE_ID])
        # Registers the unit answered with an exception response (e.g. "INN-FR-B32 only"
        # registers on other hardware). They are not asked for again.
        self.unsupported: set[int] = set()
        self._config: dict[str, float] = {}
        self._polls = 0

    async def _read(self, address: int) -> int:
        return (await self.gateway.read_holding_registers(self.slave_id, address, 1))[0]

    async def _read_optional(self, address: int) -> int | None:
        """Read a register that the hardware may not have; None if it is not supported."""
        if address in self.unsupported:
            return None
        try:
            return await self._read(address)
        except ModbusExceptionResponse as err:
            _LOGGER.debug("Unit %s does not support register %s (%s)", self.slave_id, address, err)
            self.unsupported.add(address)
            return None

    async def _async_update_data(self) -> WallControlState:
        """Read the state registers (and, now and then, the configuration registers)."""
        try:
            values: dict[int, int | None] = {}
            for address in REQUIRED_REGISTERS:
                values[address] = await self._read(address)
            for address in OPTIONAL_REGISTERS:
                values[address] = await self._read_optional(address)

            if self._polls % CONFIG_POLL_EVERY == 0 or not self._config:
                config: dict[str, float] = {}
                for spec in CONFIG_REGISTERS:
                    if (raw := await self._read_optional(spec.address)) is not None:
                        config[spec.key] = decode_config(spec, raw)
                self._config = config
            self._polls += 1
        except ModbusError as err:
            raise UpdateFailed(f"Unit {self.slave_id} on {self.gateway.host}: {err}") from err
        return parse_state(values, self._config)

    # ------------------------------------------------------------------ commands

    def _apply(self, **changes: Any) -> None:
        """Show the expected result straight away; the next poll confirms or corrects it."""
        if "config" in changes:
            self._config = dict(changes["config"])  # the next poll must not bring the old value back
        if self.data is not None:
            self.async_set_updated_data(dataclasses.replace(self.data, **changes))

    async def async_write(self, address: int, value: int, **changes: Any) -> None:
        """Write one register (function 06)."""
        try:
            await self.gateway.write_register(self.slave_id, address, value)
        except ModbusError as err:
            raise HomeAssistantError(f"Could not write register {address} of unit {self.slave_id}: {err}") from err
        self._apply(**changes)

    async def async_update_bits(
        self, address: int, field: str, *, set_mask: int = 0, clear_mask: int = 0
    ) -> None:
        """Change some bits of a flag register and leave the others as the unit has them.

        The register is read again first, so a change made at the wall control
        in the meantime (or a reserved bit) is never overwritten.
        """
        try:
            current = await self._read(address)
            new = apply_bits(current, set_mask=set_mask, clear_mask=clear_mask)
            if new != current:
                await self.gateway.write_register(self.slave_id, address, new)
        except ModbusError as err:
            raise HomeAssistantError(f"Could not update register {address} of unit {self.slave_id}: {err}") from err
        self._apply(**{field: new})
