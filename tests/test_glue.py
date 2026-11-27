"""Smoke tests for the Home Assistant glue, run against the gateway simulator.

Uses the stand-ins from ha_stubs.py, so this checks the integration's own logic
(register writes, read-modify-write of flag registers, state patching, entity
set, setup/unload, config flow decisions) but not compatibility with a specific
Home Assistant release.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import dataclasses
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import ha_stubs

ha_stubs.install()

import daikin_ekwhctrl1_modbus as pkg  # noqa: E402  (stub package from conftest)
from conftest import PACKAGE_DIR, open_gateway  # noqa: E402

# Run the real __init__.py inside the stub package so platforms can import from it.
pkg.__package__ = "daikin_ekwhctrl1_modbus"
exec(compile((PACKAGE_DIR / "__init__.py").read_text(), str(PACKAGE_DIR / "__init__.py"), "exec"), pkg.__dict__)

from daikin_ekwhctrl1_modbus import binary_sensor, climate, config_flow, number, sensor, switch  # noqa: E402
from daikin_ekwhctrl1_modbus import protocol as p  # noqa: E402
from daikin_ekwhctrl1_modbus.client import Framing, ModbusGateway  # noqa: E402
from ha_stubs import (  # noqa: E402
    AbortFlow,
    ConfigEntryNotReady,
    ConfigEntryState,
    HomeAssistantError,
    HVACAction,
    HVACMode,
    ServiceValidationError,
)
from simulator import GatewaySim, WallControlSim  # noqa: E402

PLATFORM_MODULES = {
    "climate": climate,
    "sensor": sensor,
    "binary_sensor": binary_sensor,
    "number": number,
    "switch": switch,
}


def _subentry(sid: str, slave: int, name: str = "Unit") -> SimpleNamespace:
    return SimpleNamespace(
        subentry_id=sid, subentry_type="wall_control", data={"name": name, "slave_id": slave}
    )


def _entry(port: int, framing: Framing, subentries: list[SimpleNamespace]) -> SimpleNamespace:
    unload: list = []
    return SimpleNamespace(
        entry_id="gw1",
        title="Gateway",
        unique_id=f"127.0.0.1:{port}",
        data={"host": "127.0.0.1", "port": port, "framing": framing.value, "scan_interval": 30},
        subentries={s.subentry_id: s for s in subentries},
        state=ConfigEntryState.LOADED,
        unload=unload,
        async_on_unload=lambda cb: unload.append(cb),
        add_update_listener=lambda cb: (lambda: None),
    )


def _hass() -> SimpleNamespace:
    return SimpleNamespace(
        config_entries=SimpleNamespace(
            async_forward_entry_setups=AsyncMock(),
            async_unload_platforms=AsyncMock(return_value=True),
            async_reload=AsyncMock(),
            async_update_entry=lambda *a, **k: None,
        )
    )


@asynccontextmanager
async def loaded(framing: Framing, slaves=(1, 2, 3)):
    """Set the integration up against a simulator: unit 1 has the INN-FR-B32 registers,
    unit 2 does not, unit 3 is not connected."""
    sim = GatewaySim(framing=framing.value, units={1: WallControlSim(), 2: WallControlSim(board=False)})
    await sim.start()
    entry = _entry(sim.port, framing, [_subentry(f"s{n}", n, f"Unit {n}") for n in slaves])
    real = ModbusGateway
    pkg.ModbusGateway = lambda host, port, fr: real(host, port, fr, timeout=0.2, retries=0, message_delay=0.0)
    try:
        assert await pkg.async_setup_entry(_hass(), entry) is True
        yield sim, entry
    finally:
        pkg.ModbusGateway = real
        for callback in entry.unload:  # HA accepts both sync and async unload callbacks
            if (result := callback()) is not None:
                await result
        await sim.stop()


async def _entities(platform, entry) -> dict[str, list]:
    """Run a platform's async_setup_entry and group the entities by subentry id."""
    added: dict[str, list] = {}

    def add(entities, config_subentry_id=None):
        added.setdefault(config_subentry_id, []).extend(entities)

    await platform.async_setup_entry(None, entry, add)
    return added


def _by_key(entities: list) -> dict:
    return {e.entity_description.key: e for e in entities}


# ------------------------------------------------------------------ setup


def test_setup_creates_a_coordinator_per_wall_control_and_survives_a_dead_unit(framing) -> None:
    async def scenario() -> None:
        async with loaded(framing) as (sim, entry):
            coords = entry.runtime_data.coordinators
            assert set(coords) == {"s1", "s2", "s3"}
            assert coords["s1"].data.air_temperature == 24.5 and coords["s2"].data.air_temperature == 24.5
            assert coords["s3"].last_update_success is False  # nobody home at slave 3

            expected = {
                "climate": 1,
                "sensor": len(sensor.SENSORS),
                "binary_sensor": len(binary_sensor.BINARY_SENSORS),
                "number": len(p.CONFIG_REGISTERS),
                "switch": len(switch.SWITCHES),
            }
            ids: list[str] = []
            for name, module in PLATFORM_MODULES.items():
                added = await _entities(module, entry)
                assert {k: len(v) for k, v in added.items()} == dict.fromkeys(("s1", "s2", "s3"), expected[name])
                ids += [e._attr_unique_id for entities in added.values() for e in entities]
            assert len(ids) == len(set(ids))  # unique per unit and key
            assert all(i.startswith(("s1_", "s2_", "s3_")) for i in ids)

            assert (await _entities(sensor, entry))["s3"][0].available is False

    asyncio.run(scenario())


def test_hardware_without_the_board_registers_is_detected_and_not_asked_again(framing) -> None:
    async def scenario() -> None:
        async with loaded(framing, slaves=(1, 2)) as (sim, entry):
            board, plain = entry.runtime_data.coordinators["s1"], entry.runtime_data.coordinators["s2"]
            board_only = {p.REG_WATER_TEMP, p.REG_OUTPUTS, p.REG_MOTOR_SPEED} | {s.address for s in p.CONFIG_REGISTERS if s.board_only}
            assert board.unsupported == set()
            assert plain.unsupported == board_only

            sensors = {u: _by_key(e) for u, e in (await _entities(sensor, entry)).items()}
            assert sensors["s1"]["water_temperature"].available and sensors["s1"]["motor_speed"].available
            assert not sensors["s2"]["water_temperature"].available and not sensors["s2"]["motor_speed"].available
            assert sensors["s2"]["air_temperature"].available

            numbers = {u: _by_key(e) for u, e in (await _entities(number, entry)).items()}
            assert numbers["s1"]["speed_auto_max"].available and numbers["s1"]["speed_auto_max"].native_value == 1100
            assert not numbers["s2"]["speed_auto_max"].available
            assert numbers["s2"]["min_setpoint"].available and numbers["s2"]["min_setpoint"].native_value == 16.0

            flags = {u: _by_key(e) for u, e in (await _entities(binary_sensor, entry)).items()}
            assert flags["s1"]["valve_1"].available and not flags["s2"]["valve_1"].available

            # a second poll must not ask the unit for those registers again
            asked = len(sim.requests)
            await plain.async_refresh()
            assert len(sim.requests) - asked == len(p.REQUIRED_REGISTERS)

            sim.units[2].registers[p.REG_PROGRAM] = 0  # switch it on
            await plain.async_refresh()
            climates = {u: e[0] for u, e in (await _entities(climate, entry)).items()}
            assert climates["s2"].hvac_action is None  # no outputs register: demand unknown
            assert climates["s1"].hvac_action == HVACAction.OFF  # unit 1 is still in stand-by

    asyncio.run(scenario())


def test_config_registers_are_read_every_tenth_poll_only(framing) -> None:
    async def scenario() -> None:
        async with loaded(framing, slaves=(1,)) as (sim, entry):
            coord = entry.runtime_data.coordinators["s1"]
            fast = len(p.REQUIRED_REGISTERS) + len(p.OPTIONAL_REGISTERS)
            sim.requests.clear()
            await coord.async_refresh()
            assert len(sim.requests) == fast  # poll 2: state only
            sim.units[1].registers[202] = 150  # SPL changed at the unit
            for _ in range(8):
                await coord.async_refresh()
            assert coord.data.config["min_setpoint"] == 16.0  # not re-read yet
            sim.requests.clear()
            await coord.async_refresh()  # poll 11 re-reads the configuration
            assert len(sim.requests) == fast + len(p.CONFIG_REGISTERS)
            assert coord.data.config["min_setpoint"] == 15.0

    asyncio.run(scenario())


def test_every_entity_key_has_a_translation() -> None:
    names = json.loads((PACKAGE_DIR / "translations" / "en.json").read_text())["entity"]
    keys = {
        "sensor": [d.translation_key for d in sensor.SENSORS],
        "binary_sensor": [d.translation_key for d in binary_sensor.BINARY_SENSORS],
        "number": [s.key for s in p.CONFIG_REGISTERS],
        "switch": [d.translation_key for d in switch.SWITCHES],
    }
    for platform, platform_keys in keys.items():
        missing = [k for k in platform_keys if k not in names[platform]]
        assert not missing, f"{platform}: {missing}"
    fan_states = names["climate"]["wall_control"]["state_attributes"]["fan_mode"]["state"]
    assert set(fan_states) == {program.value for program in p.FanProgram}
    assert set(names["sensor"]["active_mode"]["state"]) == set(sensor.SENSORS[-1].options)


def test_setup_fails_cleanly_when_the_gateway_is_unreachable() -> None:
    async def scenario() -> None:
        entry = _entry(1, Framing.RTU_OVER_TCP, [_subentry("s1", 1)])
        real = ModbusGateway
        pkg.ModbusGateway = lambda host, port, fr: real(host, port, fr, timeout=0.2, retries=0)
        try:
            with pytest.raises(ConfigEntryNotReady):
                await pkg.async_setup_entry(_hass(), entry)
        finally:
            pkg.ModbusGateway = real

    asyncio.run(scenario())


def test_two_gateways_have_independent_units() -> None:
    async def scenario() -> None:
        async with loaded(Framing.RTU_OVER_TCP, slaves=(1,)) as (_, a), loaded(Framing.MODBUS_TCP, slaves=(1,)) as (_, b):
            ca = a.runtime_data.coordinators["s1"]
            cb = b.runtime_data.coordinators["s1"]
            await ca.async_update_bits(p.REG_PROGRAM, "program_register", clear_mask=p.PRG_STANDBY)
            await cb.async_refresh()
            assert ca.data.power and not cb.data.power

    asyncio.run(scenario())


def test_unload_closes_the_connection(framing) -> None:
    async def scenario() -> None:
        async with loaded(framing, slaves=(1,)) as (sim, entry):
            hass = _hass()
            assert await pkg.async_unload_entry(hass, entry) is True
            hass.config_entries.async_unload_platforms.assert_awaited_once()
        await entry.runtime_data.gateway.close()  # a second close must be harmless

    asyncio.run(scenario())


# ------------------------------------------------------------------ climate


def test_climate_commands_reach_the_unit_and_survive_a_refresh(framing) -> None:
    async def scenario() -> None:
        async with loaded(framing) as (sim, entry):
            ent = (await _entities(climate, entry))["s1"][0]
            coord = entry.runtime_data.coordinators["s1"]
            regs = sim.units[1].registers

            assert ent.hvac_mode == HVACMode.OFF and ent.hvac_action == HVACAction.OFF
            assert ent.target_temperature == 22.0 and ent.current_temperature == 24.5
            assert ent.fan_mode == "automatic" and (ent.min_temp, ent.max_temp) == (16.0, 28.0)

            regs[p.REG_PROGRAM] |= 0x0300  # reserved bits set by the firmware must survive every write
            await ent.async_set_hvac_mode(HVACMode.HEAT)
            assert regs[p.REG_SEASON] == 3 and not regs[p.REG_PROGRAM] & p.PRG_STANDBY
            assert regs[p.REG_PROGRAM] & 0x0300 == 0x0300
            assert ent.hvac_mode == HVACMode.HEAT  # optimistic
            await coord.async_refresh()
            assert ent.hvac_mode == HVACMode.HEAT  # and confirmed by the unit
            assert coord.data.heating_active

            await ent.async_set_hvac_mode(HVACMode.COOL)
            assert regs[p.REG_SEASON] == 5 and coord.data.season is p.Season.COOL
            await ent.async_set_hvac_mode(HVACMode.AUTO)
            assert regs[p.REG_SEASON] == 0
            await ent.async_set_hvac_mode(HVACMode.OFF)
            assert regs[p.REG_PROGRAM] & p.PRG_STANDBY and regs[p.REG_SEASON] == 0  # mode untouched
            assert ent.hvac_mode == HVACMode.OFF

            await ent.async_set_temperature(temperature=21.3)
            assert regs[p.REG_SETPOINT] == 213 and ent.target_temperature == 21.3
            await ent.async_set_temperature(temperature=19, hvac_mode=HVACMode.COOL)
            assert regs[p.REG_SEASON] == 5 and regs[p.REG_SETPOINT] == 190
            assert not regs[p.REG_PROGRAM] & p.PRG_STANDBY

            before = list(sim.units[1].writes)
            for bad in (15.9, 28.1):
                with pytest.raises(ServiceValidationError):
                    await ent.async_set_temperature(temperature=bad, hvac_mode=HVACMode.HEAT)
            assert sim.units[1].writes == before  # validation happens before anything is written

            for mode, value in (("silent", 1), ("night", 2), ("maximum", 3), ("automatic", 0)):
                await ent.async_set_fan_mode(mode)
                assert regs[p.REG_PROGRAM] & p.PRG_MASK == value and ent.fan_mode == mode
                assert regs[p.REG_PROGRAM] & 0x0300 == 0x0300 and not regs[p.REG_PROGRAM] & p.PRG_STANDBY

            await ent.async_turn_off()
            assert regs[p.REG_PROGRAM] & p.PRG_STANDBY
            await ent.async_turn_on()
            assert not regs[p.REG_PROGRAM] & p.PRG_STANDBY and ent.hvac_mode == HVACMode.COOL  # keeps its mode

    asyncio.run(scenario())


def test_a_change_made_at_the_wall_control_is_not_overwritten(framing) -> None:
    async def scenario() -> None:
        async with loaded(framing) as (sim, entry):
            ent = (await _entities(climate, entry))["s1"][0]
            regs = sim.units[1].registers
            regs[p.REG_PROGRAM] |= p.PRG_KEYBOARD_LOCK | 0b010  # locked + night, after the last poll
            await ent.async_set_fan_mode("maximum")
            assert regs[p.REG_PROGRAM] & p.PRG_KEYBOARD_LOCK and regs[p.REG_PROGRAM] & p.PRG_MASK == 3

    asyncio.run(scenario())


def test_unchanged_flag_register_is_not_rewritten(framing) -> None:
    async def scenario() -> None:
        async with loaded(framing) as (sim, entry):
            ent = (await _entities(climate, entry))["s1"][0]
            await ent.async_turn_off()  # already in stand-by
            assert sim.units[1].writes == []

    asyncio.run(scenario())


def test_climate_action_and_setpoint_limits(framing) -> None:
    async def scenario() -> None:
        async with loaded(framing) as (sim, entry):
            ent = (await _entities(climate, entry))["s1"][0]
            coord = entry.runtime_data.coordinators["s1"]
            on = dataclasses.replace(coord.data, program_register=0, season_register=5, status=p.STAT_COOLING, outputs=0)
            for changes, expected in (
                ({}, HVACAction.IDLE),
                ({"outputs": p.OUT_EV1}, HVACAction.COOLING),
                ({"status": p.STAT_HEATING}, HVACAction.HEATING),
                ({"outputs": p.OUT_EV2, "status": 0}, HVACAction.IDLE),  # valve open but no active mode known
                ({"program_register": p.PRG_STANDBY}, HVACAction.OFF),
            ):
                on = dataclasses.replace(on, **changes)
                coord.data = on
                assert ent.hvac_action == expected

            coord.data = dataclasses.replace(on, web_flags=p.WEB_SETPOINT_RESTRICTION)
            assert (ent.min_temp, ent.max_temp) == (20.0, 24.0)  # SPL_W / SPH_W
            with pytest.raises(ServiceValidationError):
                await ent.async_set_temperature(temperature=25)

            coord.data = dataclasses.replace(on, alarms=p.ALARM_AIR_PROBE)
            assert ent.current_temperature is None

    asyncio.run(scenario())


def test_failed_write_raises_and_leaves_state_alone() -> None:
    async def scenario() -> None:
        async with loaded(Framing.RTU_OVER_TCP) as (sim, entry):
            ent = (await _entities(climate, entry))["s3"][0]  # unit 3 is absent
            coord = entry.runtime_data.coordinators["s3"]
            coord.data = entry.runtime_data.coordinators["s1"].data  # pretend it had data
            before = coord.data
            with pytest.raises(HomeAssistantError):
                await ent.async_turn_on()
            with pytest.raises(HomeAssistantError):
                await ent.async_set_temperature(temperature=20)
            assert coord.data is before and not coord.updates

    asyncio.run(scenario())


# ------------------------------------------------------------------ number / switch


def test_number_writes_scaled_and_signed_values_and_keeps_them(framing) -> None:
    async def scenario() -> None:
        async with loaded(framing) as (sim, entry):
            numbers = _by_key((await _entities(number, entry))["s1"])
            coord = entry.runtime_data.coordinators["s1"]
            regs = sim.units[1].registers

            await numbers["min_setpoint"].async_set_native_value(15.5)
            assert regs[202] == 155 and numbers["min_setpoint"].native_value == 15.5
            await numbers["air_probe_offset"].async_set_native_value(-1.5)
            assert regs[242] == 0xFFF1 and numbers["air_probe_offset"].native_value == -1.5
            await numbers["speed_maximum"].async_set_native_value(1400)
            assert regs[214] == 1400
            await numbers["fan_hours"].async_set_native_value(0)
            assert regs[222] == 0

            await coord.async_refresh()  # a normal poll must not bring the old value back
            assert numbers["min_setpoint"].native_value == 15.5 and numbers["speed_maximum"].native_value == 1400
            assert numbers["min_setpoint"].entity_description.native_step == 0.1
            assert numbers["speed_maximum"].entity_description.entity_registry_enabled_default is False
            assert numbers["min_setpoint"].entity_description.entity_registry_enabled_default is True

            before = list(sim.units[1].writes)
            with pytest.raises(ServiceValidationError):
                await numbers["speed_maximum"].async_set_native_value(5000)
            assert sim.units[1].writes == before

    asyncio.run(scenario())


def test_switches_change_single_bits_only(framing) -> None:
    async def scenario() -> None:
        async with loaded(framing) as (sim, entry):
            switches = _by_key((await _entities(switch, entry))["s1"])
            regs = sim.units[1].registers
            regs[p.REG_WEB] = p.WEB_BYPASS  # a bit we never write

            lock = switches["keyboard_lock"]
            assert lock.is_on is False and lock.entity_description.entity_registry_enabled_default is True
            await lock.async_turn_on()
            assert regs[p.REG_PROGRAM] == p.PRG_STANDBY | p.PRG_KEYBOARD_LOCK and lock.is_on
            await lock.async_turn_off()
            assert regs[p.REG_PROGRAM] == p.PRG_STANDBY and not lock.is_on

            forced = switches["web_forced_off"]
            assert forced.entity_description.entity_registry_enabled_default is False
            await forced.async_turn_on()
            assert regs[p.REG_WEB] == p.WEB_BYPASS | p.WEB_FORCED_OFF
            await switches["web_lock_keys"].async_turn_on()
            assert regs[p.REG_WEB] == p.WEB_BYPASS | p.WEB_FORCED_OFF | p.WEB_LOCK_KEYS
            await forced.async_turn_off()
            assert regs[p.REG_WEB] == p.WEB_BYPASS | p.WEB_LOCK_KEYS and not forced.is_on

    asyncio.run(scenario())


# ------------------------------------------------------------------ sensors


def test_sensor_and_binary_sensor_values(framing) -> None:
    async def scenario() -> None:
        async with loaded(framing) as (sim, entry):
            coord = entry.runtime_data.coordinators["s1"]
            sensors = _by_key((await _entities(sensor, entry))["s1"])
            assert sensors["air_temperature"].native_value == 24.5
            assert sensors["water_temperature"].native_value == 18.3
            assert sensors["active_setpoint"].native_value == 22.0
            assert sensors["motor_speed"].native_value == 0
            assert sensors["active_mode"].native_value is None

            coord.data = dataclasses.replace(coord.data, status=p.STAT_HEATING, motor_speed=900, alarms=p.ALARM_AIR_PROBE)
            assert sensors["active_mode"].native_value == "heating" and sensors["motor_speed"].native_value == 900
            assert sensors["air_temperature"].native_value is None  # probe alarm
            coord.data = dataclasses.replace(coord.data, status=p.STAT_COOLING)
            assert sensors["active_mode"].native_value == "cooling"

            flags = _by_key((await _entities(binary_sensor, entry))["s1"])
            coord.data = dataclasses.replace(coord.data, status=0, alarms=0, outputs=0)
            assert not any(flag.is_on for flag in flags.values())
            coord.data = dataclasses.replace(coord.data, alarms=p.ALARM_FILTER | p.ALARM_MOTOR)
            assert flags["alarm"].is_on and flags["filter"].is_on and flags["motor_fault"].is_on
            assert not flags["air_probe"].is_on and not flags["grid_open"].is_on
            coord.data = dataclasses.replace(coord.data, alarms=0, status=p.STAT_ALARM | p.STAT_ANTIFREEZE | p.STAT_H2_ABSENT)
            assert flags["alarm"].is_on and flags["antifreeze"].is_on and flags["h2_probe_absent"].is_on
            assert not flags["test_mode"].is_on and not flags["master_timeout"].is_on
            coord.data = dataclasses.replace(coord.data, status=0, outputs=p.OUT_EV2 | p.OUT_BOILER)
            assert flags["valve_2"].is_on and flags["boiler_request"].is_on
            assert not flags["valve_1"].is_on and not flags["chiller_request"].is_on
            enabled = {k for k, f in flags.items() if f.entity_description.entity_registry_enabled_default}
            assert enabled >= {"alarm", "filter", "motor_fault", "air_probe", "grid_open", "valve_1", "valve_2"}
            assert "h4_probe" not in enabled

    asyncio.run(scenario())


# ------------------------------------------------------------------ config flow


def _subflow(entry, subentry=None) -> config_flow.WallControlSubentryFlow:
    flow = config_flow.WallControlSubentryFlow()
    flow.entry, flow.subentry = entry, subentry
    return flow


def _unit_input(slave: int, **extra) -> dict:
    return {"name": "Living Room", "slave_id": slave} | extra


def test_subentry_flow_add_probe_and_duplicates(framing) -> None:
    async def scenario() -> None:
        async with open_gateway(framing) as (sim, client):
            entry = SimpleNamespace(
                state=ConfigEntryState.LOADED,
                runtime_data=SimpleNamespace(gateway=client),
                subentries={"existing": _subentry("existing", 2)},
            )
            assert (await _subflow(entry).async_step_user())["type"] == "form"

            ok = await _subflow(entry).async_step_user(_unit_input(1))
            assert ok["type"] == "create_entry" and ok["title"] == "Living Room"
            assert ok["data"] == {"name": "Living Room", "slave_id": 1}

            dup = await _subflow(entry).async_step_user(_unit_input(2))
            assert dup["errors"] == {"slave_id": "slave_in_use"}

            # Unit 3 is not connected: first submit warns, the same submit again is accepted.
            flow = _subflow(entry)
            first = await flow.async_step_user(_unit_input(3))
            assert first["errors"] == {"base": "no_response"}
            assert (await flow.async_step_user(_unit_input(3)))["type"] == "create_entry"
            # a different unanswered address warns again
            assert (await flow.async_step_user(_unit_input(4)))["errors"] == {"base": "no_response"}

    asyncio.run(scenario())


def test_slave_address_range_matches_the_manual() -> None:
    from daikin_ekwhctrl1_modbus.const import MAX_SLAVE_ID

    assert MAX_SLAVE_ID == 255  # register 200 (ADR): 1...255
    form = asyncio.run(_subflow(SimpleNamespace(state=ConfigEntryState.NOT_LOADED, subentries={})).async_step_user())
    selector_config = form["data_schema"].schema["slave_id"].args[0].kwargs
    assert (selector_config["min"], selector_config["max"], selector_config["step"]) == (1, 255, 1)


def test_subentry_flow_without_a_connection_accepts_the_address() -> None:
    async def scenario() -> None:
        entry = SimpleNamespace(state=ConfigEntryState.NOT_LOADED, subentries={})
        result = await _subflow(entry).async_step_user(_unit_input(200))
        assert result["type"] == "create_entry" and result["data"]["slave_id"] == 200

    asyncio.run(scenario())


def test_subentry_reconfigure_may_keep_its_own_address(framing) -> None:
    async def scenario() -> None:
        async with open_gateway(framing) as (sim, client):
            own = _subentry("a", 1)
            entry = SimpleNamespace(
                state=ConfigEntryState.LOADED,
                runtime_data=SimpleNamespace(gateway=client),
                subentries={"a": own, "b": _subentry("b", 2)},
            )
            form = await _subflow(entry, own).async_step_reconfigure()
            assert form["step_id"] == "reconfigure"
            ok = await _subflow(entry, own).async_step_reconfigure(_unit_input(1, name="Renamed"))
            assert ok["reason"] == "reconfigure_successful" and ok["title"] == "Renamed"
            clash = await _subflow(entry, own).async_step_reconfigure(_unit_input(2))
            assert clash["errors"] == {"slave_id": "slave_in_use"}

    asyncio.run(scenario())


def test_gateway_flow_checks_the_connection_and_rejects_duplicates(framing) -> None:
    async def scenario() -> None:
        async with open_gateway(framing) as (sim, _client):
            good = {"name": "GW", "host": "127.0.0.1", "port": sim.port, "framing": framing.value, "scan_interval": 30}
            flow = config_flow.WallControlConfigFlow()
            result = await flow.async_step_user(good)
            assert result["type"] == "create_entry" and result["data"] == good
            assert flow.unique_id == f"127.0.0.1:{sim.port}"

            flow = config_flow.WallControlConfigFlow()
            flow.entries = [SimpleNamespace(unique_id=f"127.0.0.1:{sim.port}", entry_id="x")]
            with pytest.raises(AbortFlow):
                await flow.async_step_user(good)

            flow = config_flow.WallControlConfigFlow()
            result = await flow.async_step_user(good | {"port": 1})
            assert result["errors"] == {"base": "cannot_connect"}

    asyncio.run(scenario())


def test_gateway_reconfigure_updates_or_aborts() -> None:
    async def scenario() -> None:
        updates = []
        hass = SimpleNamespace(config_entries=SimpleNamespace(async_update_entry=lambda e, **k: updates.append(k)))
        async with open_gateway(Framing.RTU_OVER_TCP) as (sim, _client):
            entry = SimpleNamespace(
                entry_id="mine", title="Old", unique_id="old:1",
                data={"host": "old", "port": 1, "framing": "rtu_over_tcp", "scan_interval": 30},
            )
            new = {"name": "New", "host": "127.0.0.1", "port": sim.port, "framing": "rtu_over_tcp", "scan_interval": 20}
            flow = config_flow.WallControlConfigFlow()
            flow.hass, flow.reconfigure_entry, flow.entries = hass, entry, [entry]
            assert (await flow.async_step_reconfigure())["step_id"] == "reconfigure"
            result = await flow.async_step_reconfigure(new)
            assert result["reason"] == "reconfigure_successful"
            assert updates[0]["title"] == "New" and updates[0]["unique_id"] == f"127.0.0.1:{sim.port}"

            other = SimpleNamespace(entry_id="other", unique_id=f"127.0.0.1:{sim.port}")
            flow.entries = [entry, other]
            assert (await flow.async_step_reconfigure(new))["reason"] == "already_configured"

    asyncio.run(scenario())
