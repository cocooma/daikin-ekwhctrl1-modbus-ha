"""Client tests against the simulated gateway (both framings)."""

from __future__ import annotations

import asyncio

import pytest

from conftest import open_gateway
from daikin_ekwhctrl1_modbus import protocol as p
from daikin_ekwhctrl1_modbus.client import (
    Framing,
    ModbusConnectionError,
    ModbusExceptionResponse,
    ModbusGateway,
    ModbusProtocolError,
    ModbusTimeout,
    crc16,
)
from simulator import WallControlSim


@pytest.mark.parametrize(
    ("frame", "wire_crc"),
    [
        ("01 03 00 00 00 02", "C4 0B"),  # widely published reference frames,
        ("01 03 00 00 00 01", "84 0A"),  # CRC bytes shown in on-the-wire order
        ("01 04 00 00 00 01", "31 CA"),  # (low byte first)
    ],
)
def test_crc16_known_vectors(frame: str, wire_crc: str) -> None:
    crc = crc16(bytes.fromhex(frame))
    assert f"{crc & 0xFF:02X} {crc >> 8:02X}" == wire_crc


def test_read_one_and_several_registers(framing: Framing) -> None:
    async def scenario() -> None:
        async with open_gateway(framing) as (sim, client):
            assert await client.read_holding_registers(1, p.REG_AIR_TEMP, 1) == [245]
            assert await client.read_holding_registers(1, p.REG_AIR_TEMP, 2) == [245, 183]
            assert sim.requests == [(1, 3), (1, 3)]

    asyncio.run(scenario())


def test_write_single_register_and_read_back(framing: Framing) -> None:
    async def scenario() -> None:
        async with open_gateway(framing) as (sim, client):
            await client.write_register(1, p.REG_SETPOINT, p.encode_temperature(21.5))
            await client.write_register(1, p.REG_SEASON, p.SEASON_VALUE[p.Season.HEAT])
            await client.write_register(1, p.REG_PROGRAM, p.PROGRAM_VALUE[p.FanProgram.NIGHT])  # leaves stand-by

            assert await client.read_holding_registers(1, p.REG_SETPOINT, 1) == [215]
            assert await client.read_holding_registers(1, p.REG_ACTIVE_SETPOINT, 1) == [215]
            status = (await client.read_holding_registers(1, p.REG_STATUS, 1))[0]
            assert status & p.STAT_HEATING and not status & p.STAT_STANDBY
            assert sim.units[1].writes == [(231, 215), (233, 3), (201, 2)]

    asyncio.run(scenario())


def test_only_functions_03_and_06_are_implemented(framing: Framing) -> None:
    async def scenario() -> None:
        async with open_gateway(framing) as (sim, client):
            with pytest.raises(ModbusExceptionResponse) as err:
                await client.write_registers(1, p.REG_SETPOINT, [200])  # function 16
            assert err.value.code == 1
            with pytest.raises(ModbusExceptionResponse) as err:
                await client.read_input_registers(1, 0, 1)  # function 04
            assert err.value.code == 1

    asyncio.run(scenario())


def test_two_units_behind_one_gateway_are_independent(framing: Framing) -> None:
    async def scenario() -> None:
        async with open_gateway(framing) as (sim, client):
            await client.write_register(1, p.REG_PROGRAM, 0)  # unit 1 on, unit 2 stays in stand-by
            one = (await client.read_holding_registers(1, p.REG_PROGRAM, 1))[0]
            two = (await client.read_holding_registers(2, p.REG_PROGRAM, 1))[0]
            assert not one & p.PRG_STANDBY and two & p.PRG_STANDBY
            await client.read_holding_registers(1, p.REG_WATER_TEMP, 1)  # unit 1 has the board registers
            with pytest.raises(ModbusExceptionResponse):
                await client.read_holding_registers(2, p.REG_WATER_TEMP, 1)  # unit 2 does not
            assert sim.connections == 1  # one shared connection

    asyncio.run(scenario())


def test_absent_unit_times_out_without_breaking_the_others(framing: Framing) -> None:
    async def scenario() -> None:
        async with open_gateway(framing, timeout=0.2, retries=0) as (sim, client):
            with pytest.raises(ModbusTimeout):
                await client.read_holding_registers(3, 0, 1)  # unit 3 not connected
            # the unit that exists still answers afterwards (client reconnects)
            assert await client.read_holding_registers(1, 0, 1) == [245]
            assert sim.connections == 2

    asyncio.run(scenario())


def test_exception_response_is_reported_and_keeps_the_connection(framing: Framing) -> None:
    async def scenario() -> None:
        async with open_gateway(framing) as (sim, client):
            with pytest.raises(ModbusExceptionResponse) as err:
                await client.read_holding_registers(1, 100, 1)  # illegal address
            assert err.value.code == 2
            with pytest.raises(ModbusExceptionResponse) as err:
                await client.write_register(1, p.REG_AIR_TEMP, 0)  # read-only register
            assert err.value.code == 3
            await client.read_holding_registers(1, 0, 1)
            assert sim.connections == 1  # no reconnect needed after a clean exception

    asyncio.run(scenario())


def test_forced_exception_code(framing: Framing) -> None:
    async def scenario() -> None:
        async with open_gateway(framing) as (sim, client):
            sim.exception_for[(1, 3)] = 4
            with pytest.raises(ModbusExceptionResponse) as err:
                await client.read_holding_registers(1, 0, 1)
            assert err.value.code == 4

    asyncio.run(scenario())


def test_dropped_connection_is_retried_transparently(framing: Framing) -> None:
    async def scenario() -> None:
        async with open_gateway(framing) as (sim, client):
            await client.read_holding_registers(1, 0, 1)
            sim.drop_next = 1  # gateway closes the socket instead of answering once
            assert await client.read_holding_registers(1, 0, 2) == [245, 183]
            assert sim.connections == 2

    asyncio.run(scenario())


def test_dropped_connection_without_retries_raises(framing: Framing) -> None:
    async def scenario() -> None:
        async with open_gateway(framing, retries=0) as (sim, client):
            sim.drop_next = 1
            with pytest.raises(ModbusConnectionError):
                await client.read_holding_registers(1, 0, 1)

    asyncio.run(scenario())


def test_bad_crc_is_rejected_then_retried() -> None:
    async def scenario() -> None:
        async with open_gateway(Framing.RTU_OVER_TCP, retries=0) as (sim, client):
            sim.corrupt_next_crc = 1
            with pytest.raises(ModbusProtocolError):
                await client.read_holding_registers(1, 0, 1)

        async with open_gateway(Framing.RTU_OVER_TCP, retries=1) as (sim, client):
            sim.corrupt_next_crc = 1
            assert await client.read_holding_registers(1, 0, 1)  # second attempt is clean

    asyncio.run(scenario())


def test_requests_are_serialised_on_the_shared_bus(framing: Framing) -> None:
    async def scenario() -> None:
        async with open_gateway(framing) as (sim, client):
            sim.response_delay = 0.03
            await asyncio.gather(*(client.read_holding_registers(unit, 0, 1) for unit in (1, 2, 1, 2, 1, 2)))
            assert sim.max_active == 1  # never two requests in flight on the RS485 bus
            assert len(sim.requests) == 6

    asyncio.run(scenario())


def test_unreachable_gateway() -> None:
    async def scenario() -> None:
        client = ModbusGateway("127.0.0.1", 1, Framing.RTU_OVER_TCP, timeout=0.3, retries=0)
        with pytest.raises(ModbusConnectionError):
            await client.connect()
        with pytest.raises(ModbusConnectionError):
            await client.read_input_registers(1, 0, 1)

    asyncio.run(scenario())


def test_two_gateways_do_not_share_state() -> None:
    """The integration creates one client per gateway; they must be independent."""

    async def scenario() -> None:
        async with (
            open_gateway(Framing.RTU_OVER_TCP, units={1: WallControlSim()}) as (sim_a, client_a),
            open_gateway(Framing.MODBUS_TCP, units={1: WallControlSim()}) as (sim_b, client_b),
        ):
            await client_a.write_register(1, p.REG_PROGRAM, 0)
            a = (await client_a.read_holding_registers(1, p.REG_PROGRAM, 1))[0]
            b = (await client_b.read_holding_registers(1, p.REG_PROGRAM, 1))[0]
            assert not a & p.PRG_STANDBY and b & p.PRG_STANDBY  # same slave id, different gateways
            assert sim_a.port != sim_b.port

    asyncio.run(scenario())


def test_count_limits() -> None:
    async def scenario() -> None:
        client = ModbusGateway("127.0.0.1", 1)
        with pytest.raises(ValueError):
            await client.read_input_registers(1, 0, 0)
        with pytest.raises(ValueError):
            await client.read_input_registers(1, 0, 126)

    asyncio.run(scenario())
