"""Simulated RS485 gateway with Daikin EKWHCTRL1 wall controls behind it (for tests).

Speaks RTU-over-TCP or Modbus TCP. Units that are not registered never answer,
like a wall control that is not wired to the bus. The units implement only
functions 03 and 06, like the real device (manual N420384A).

The behaviour of the registers (which are read-only, what a write changes) is
the simulator author's reading of the manual, not measured device behaviour.
"""

from __future__ import annotations

import asyncio
import struct
from dataclasses import dataclass, field

from daikin_ekwhctrl1_modbus import protocol as p
from daikin_ekwhctrl1_modbus.client import crc16


class SimError(Exception):
    """A Modbus exception response with the given code."""

    def __init__(self, code: int) -> None:
        self.code = code


_READ_ONLY = {0, 1, 8, 9, 15, 104, 105}


class WallControlSim:
    """One wall control. `board=False` models hardware without the INN-FR-B32-only registers."""

    def __init__(self, *, board: bool = True, standby: bool = True, address: int = 2) -> None:
        self.board = board
        self.registers: dict[int, int] = {
            p.REG_AIR_TEMP: 245,
            p.REG_ACTIVE_SETPOINT: 220,
            p.REG_STATUS: p.STAT_STANDBY if standby else 0,
            p.REG_ALARMS: 0,
            p.REG_ADDRESS: address,
            p.REG_PROGRAM: p.PRG_STANDBY if standby else 0,
            p.REG_SETPOINT: 220,
            p.REG_SEASON: 0,
            p.REG_WEB: 0,
            202: 160, 203: 280, 209: 0, 221: 0, 222: 0, 242: 0, 245: 200, 246: 240,
        }
        if board:
            self.registers |= {
                p.REG_WATER_TEMP: 183, p.REG_OUTPUTS: 0, p.REG_MOTOR_SPEED: 0,
                210: 400, 211: 550, 212: 680, 230: 920, 213: 1100, 234: 1220, 214: 1500, 215: 1700,
                218: 300, 219: 200, 243: 0, 244: 0,
            }
        self.writes: list[tuple[int, int]] = []

    def read(self, address: int, count: int) -> list[int]:
        if any(a not in self.registers for a in range(address, address + count)):
            raise SimError(2)  # illegal data address
        return [self.registers[a] for a in range(address, address + count)]

    def write(self, address: int, value: int) -> None:
        if address not in self.registers:
            raise SimError(2)
        if address in _READ_ONLY:
            raise SimError(3)  # illegal data value (assumption: the device rejects writes to read-only registers)
        self.registers[address] = value
        self.writes.append((address, value))
        status = self.registers[p.REG_STATUS]
        if address == p.REG_PROGRAM:
            status = status | p.STAT_STANDBY if value & p.PRG_STANDBY else status & ~p.STAT_STANDBY
        elif address == p.REG_SEASON:
            if value == 3:
                status = status & ~p.STAT_COOLING | p.STAT_HEATING
            elif value == 5:
                status = status & ~p.STAT_HEATING | p.STAT_COOLING
        elif address == p.REG_SETPOINT:
            self.registers[p.REG_ACTIVE_SETPOINT] = value
        self.registers[p.REG_STATUS] = status


@dataclass
class GatewaySim:
    """TCP server emulating the gateway."""

    framing: str = "rtu_over_tcp"
    units: dict[int, WallControlSim] = field(default_factory=dict)
    # Fault injection
    corrupt_next_crc: int = 0
    drop_next: int = 0
    exception_for: dict[tuple[int, int], int] = field(default_factory=dict)  # (unit, fc) -> code
    response_delay: float = 0.0
    # Bookkeeping
    requests: list[tuple[int, int]] = field(default_factory=list)  # (unit, function)
    connections: int = 0
    active: int = 0
    max_active: int = 0
    _server: asyncio.AbstractServer | None = None
    port: int = 0

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        assert self._server is not None
        self._server.close()
        await self._server.wait_closed()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.connections += 1
        try:
            while True:
                if self.framing == "rtu_over_tcp":
                    unit, pdu, tid = await self._read_rtu_request(reader)
                else:
                    unit, pdu, tid = await self._read_mbap_request(reader)

                self.active += 1
                self.max_active = max(self.max_active, self.active)
                try:
                    if self.response_delay:
                        await asyncio.sleep(self.response_delay)
                    reply = self._process(unit, pdu)
                finally:
                    self.active -= 1

                if reply is None:
                    continue  # no such unit: stay silent
                if self.drop_next:
                    self.drop_next -= 1
                    writer.close()
                    return
                if self.framing == "rtu_over_tcp":
                    frame = bytes([unit]) + reply
                    crc = crc16(frame)
                    if self.corrupt_next_crc:
                        self.corrupt_next_crc -= 1
                        crc ^= 0xFFFF
                    writer.write(frame + struct.pack("<H", crc))
                else:
                    writer.write(struct.pack(">HHHB", tid, 0, len(reply) + 1, unit) + reply)
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()

    @staticmethod
    async def _read_rtu_request(reader: asyncio.StreamReader) -> tuple[int, bytes, int]:
        head = await reader.readexactly(2)
        if head[1] in (3, 4, 6):
            rest = await reader.readexactly(6)
            frame = head + rest
        else:  # 0x10
            part = await reader.readexactly(5)
            tail = await reader.readexactly(part[4] + 2)
            frame = head + part + tail
        assert struct.unpack("<H", frame[-2:])[0] == crc16(frame[:-2]), "client sent bad CRC"
        return frame[0], frame[1:-2], 0

    @staticmethod
    async def _read_mbap_request(reader: asyncio.StreamReader) -> tuple[int, bytes, int]:
        header = await reader.readexactly(7)
        tid, _proto, length, unit = struct.unpack(">HHHB", header)
        pdu = await reader.readexactly(length - 1)
        return unit, pdu, tid

    def _process(self, unit: int, pdu: bytes) -> bytes | None:
        function = pdu[0]
        self.requests.append((unit, function))
        sim = self.units.get(unit)
        if sim is None:
            return None
        if (unit, function) in self.exception_for:
            return bytes([function | 0x80, self.exception_for[(unit, function)]])

        try:
            if function == 3:
                address, count = struct.unpack(">HH", pdu[1:5])
                values = sim.read(address, count)
                return bytes([function, count * 2]) + struct.pack(f">{count}H", *values)
            if function == 6:
                address, value = struct.unpack(">HH", pdu[1:5])
                sim.write(address, value)
                return pdu
        except SimError as err:
            return bytes([function | 0x80, err.code])
        return bytes([function | 0x80, 1])  # only 03 and 06 are implemented
