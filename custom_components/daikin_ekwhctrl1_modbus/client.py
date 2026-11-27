"""Minimal asyncio Modbus client for one RS485-to-Ethernet gateway.

Pure Python, no Home Assistant and no pymodbus, so it works on any Home
Assistant version and can be tested against a simulated gateway.

Supports the four function codes the Daikin interface implements
(03, 04, 06, 10) with two framings:

* ``rtu_over_tcp``: the raw RTU frame (address, PDU, CRC16) sent over a TCP
  socket. This is what a transparent serial gateway needs.
* ``modbus_tcp``: standard Modbus TCP (MBAP header, no CRC).

All units behind one gateway share one RS485 bus, so requests are serialised
with a lock and the connection is shared.
"""

from __future__ import annotations

import asyncio
from enum import StrEnum
import logging
import struct

_LOGGER = logging.getLogger(__name__)

FC_READ_HOLDING = 0x03
FC_READ_INPUT = 0x04
FC_WRITE_SINGLE = 0x06
FC_WRITE_MULTIPLE = 0x10

MAX_READ_REGISTERS = 125


class Framing(StrEnum):
    """Wire framing used by the gateway."""

    RTU_OVER_TCP = "rtu_over_tcp"
    MODBUS_TCP = "modbus_tcp"


class ModbusError(Exception):
    """Base class for all client errors."""


class ModbusConnectionError(ModbusError):
    """The gateway could not be reached or the connection broke."""


class ModbusTimeout(ModbusError):
    """No (complete) response in time, e.g. the unit is not connected."""


class ModbusProtocolError(ModbusError):
    """The response was malformed (bad CRC, wrong unit, wrong length ...)."""


class ModbusExceptionResponse(ModbusError):
    """The unit answered with a Modbus exception code."""

    def __init__(self, code: int) -> None:
        super().__init__(f"Modbus exception code {code}")
        self.code = code


def _build_crc_table() -> tuple[int, ...]:
    table = []
    for byte in range(256):
        crc = byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
        table.append(crc)
    return tuple(table)


_CRC_TABLE = _build_crc_table()


def crc16(data: bytes) -> int:
    """Return the Modbus RTU CRC16 of ``data``."""
    crc = 0xFFFF
    for byte in data:
        crc = (crc >> 8) ^ _CRC_TABLE[(crc ^ byte) & 0xFF]
    return crc


class ModbusGateway:
    """Shared connection to one Modbus gateway."""

    def __init__(
        self,
        host: str,
        port: int,
        framing: Framing | str = Framing.RTU_OVER_TCP,
        *,
        timeout: float = 3.0,
        retries: int = 1,
        message_delay: float = 0.05,
    ) -> None:
        self.host = host
        self.port = port
        self.framing = Framing(framing)
        self._timeout = timeout
        self._retries = retries
        self._message_delay = message_delay
        self._lock = asyncio.Lock()
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._transaction_id = 0
        self._last_activity = 0.0

    # ------------------------------------------------------------------ connection

    async def connect(self) -> None:
        """Open the TCP connection (no-op if already open)."""
        async with self._lock:
            await self._connect()

    async def _connect(self) -> None:
        if self._writer is not None and not self._writer.is_closing():
            return
        try:
            async with asyncio.timeout(self._timeout):
                self._reader, self._writer = await asyncio.open_connection(self.host, self.port)
        except (OSError, TimeoutError) as err:
            self._reader = self._writer = None
            raise ModbusConnectionError(f"cannot connect to {self.host}:{self.port}: {err}") from err
        _LOGGER.debug("Connected to %s:%s (%s)", self.host, self.port, self.framing)

    async def close(self) -> None:
        """Close the connection."""
        async with self._lock:
            await self._close()

    async def _close(self) -> None:
        writer, self._reader, self._writer = self._writer, None, None
        if writer is None:
            return
        writer.close()
        try:
            async with asyncio.timeout(1):
                await writer.wait_closed()
        except (OSError, TimeoutError):
            pass

    # ------------------------------------------------------------------ public API

    async def read_input_registers(self, unit: int, address: int, count: int) -> list[int]:
        """Read input registers (function 04)."""
        return await self._read(FC_READ_INPUT, unit, address, count)

    async def read_holding_registers(self, unit: int, address: int, count: int) -> list[int]:
        """Read holding registers (function 03)."""
        return await self._read(FC_READ_HOLDING, unit, address, count)

    async def write_register(self, unit: int, address: int, value: int) -> None:
        """Write a single holding register (function 06)."""
        pdu = struct.pack(">BHH", FC_WRITE_SINGLE, address, value & 0xFFFF)
        response = await self._transact(unit, pdu)
        if response != pdu:
            raise ModbusProtocolError("write echo does not match the request")

    async def write_registers(self, unit: int, address: int, values: list[int]) -> None:
        """Write several holding registers (function 10)."""
        pdu = struct.pack(">BHHB", FC_WRITE_MULTIPLE, address, len(values), len(values) * 2)
        pdu += b"".join(struct.pack(">H", value & 0xFFFF) for value in values)
        response = await self._transact(unit, pdu)
        if response != pdu[:5]:
            raise ModbusProtocolError("write echo does not match the request")

    # ---------------------------------------------------------------------- internals

    async def _read(self, function: int, unit: int, address: int, count: int) -> list[int]:
        if not 1 <= count <= MAX_READ_REGISTERS:
            raise ValueError(f"count must be 1..{MAX_READ_REGISTERS}")
        response = await self._transact(unit, struct.pack(">BHH", function, address, count))
        if len(response) < 2 or response[1] != count * 2 or len(response) != 2 + count * 2:
            raise ModbusProtocolError("unexpected read response length")
        return list(struct.unpack(f">{count}H", response[2:]))

    async def _transact(self, unit: int, pdu: bytes) -> bytes:
        """Send one request and return the response PDU, retrying transport errors."""
        attempts = self._retries + 1
        async with self._lock:
            for attempt in range(1, attempts + 1):
                try:
                    return await self._exchange(unit, pdu)
                except (ModbusConnectionError, ModbusTimeout, ModbusProtocolError) as err:
                    # The stream may be out of sync, so always start over.
                    await self._close()
                    if attempt == attempts:
                        raise
                    _LOGGER.debug("Request to unit %s failed (%s), retrying", unit, err)
        raise AssertionError("unreachable")  # pragma: no cover

    async def _exchange(self, unit: int, pdu: bytes) -> bytes:
        await self._connect()
        assert self._reader is not None and self._writer is not None  # noqa: S101

        loop = asyncio.get_running_loop()
        wait = self._message_delay - (loop.time() - self._last_activity)
        if wait > 0:
            await asyncio.sleep(wait)

        try:
            async with asyncio.timeout(self._timeout):
                if self.framing is Framing.RTU_OVER_TCP:
                    frame = bytes([unit]) + pdu
                    frame += struct.pack("<H", crc16(frame))
                    self._writer.write(frame)
                    await self._writer.drain()
                    response = await self._read_rtu(unit, pdu[0])
                else:
                    self._transaction_id = (self._transaction_id + 1) & 0xFFFF
                    header = struct.pack(">HHHB", self._transaction_id, 0, len(pdu) + 1, unit)
                    self._writer.write(header + pdu)
                    await self._writer.drain()
                    response = await self._read_mbap(unit, pdu[0], self._transaction_id)
        except TimeoutError as err:
            raise ModbusTimeout(f"no response from unit {unit}") from err
        except (OSError, asyncio.IncompleteReadError) as err:
            raise ModbusConnectionError(f"connection to {self.host}:{self.port} lost: {err}") from err
        finally:
            self._last_activity = loop.time()

        if response[0] & 0x80:
            raise ModbusExceptionResponse(response[1])
        if response[0] != pdu[0]:
            raise ModbusProtocolError("function code mismatch")
        return response

    async def _read_rtu(self, unit: int, function: int) -> bytes:
        """Read one RTU frame and return its PDU (function code onwards)."""
        assert self._reader is not None  # noqa: S101
        head = await self._reader.readexactly(2)
        if head[1] & 0x80:
            frame = head + await self._reader.readexactly(3)  # exception code + CRC
        elif head[1] in (FC_READ_HOLDING, FC_READ_INPUT):
            length = await self._reader.readexactly(1)
            frame = head + length + await self._reader.readexactly(length[0] + 2)
        elif head[1] in (FC_WRITE_SINGLE, FC_WRITE_MULTIPLE):
            frame = head + await self._reader.readexactly(6)  # 4 data bytes + CRC
        else:
            raise ModbusProtocolError(f"unsupported function code {head[1]:#04x} in response")

        if struct.unpack("<H", frame[-2:])[0] != crc16(frame[:-2]):
            raise ModbusProtocolError("CRC mismatch")
        if frame[0] != unit:
            raise ModbusProtocolError(f"response from unit {frame[0]}, expected {unit}")
        if (frame[1] & 0x7F) != function:
            raise ModbusProtocolError("function code mismatch")
        return frame[1:-2]

    async def _read_mbap(self, unit: int, function: int, transaction_id: int) -> bytes:
        """Read one Modbus TCP frame and return its PDU."""
        assert self._reader is not None  # noqa: S101
        header = await self._reader.readexactly(7)
        tid, protocol, length, resp_unit = struct.unpack(">HHHB", header)
        if tid != transaction_id or protocol != 0 or not 2 <= length <= 254:
            raise ModbusProtocolError("bad MBAP header")
        pdu = await self._reader.readexactly(length - 1)
        if resp_unit != unit:
            raise ModbusProtocolError(f"response from unit {resp_unit}, expected {unit}")
        if (pdu[0] & 0x7F) != function:
            raise ModbusProtocolError("function code mismatch")
        return pdu
