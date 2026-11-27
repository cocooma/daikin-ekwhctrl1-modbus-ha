"""Register map tests, with expected values taken from the EKWHCTRL1 manual (N420384A)."""

from __future__ import annotations

import pytest

from daikin_ekwhctrl1_modbus import protocol as p


def _values(**overrides: int) -> dict[int, int]:
    base = {
        p.REG_AIR_TEMP: 245,
        p.REG_ACTIVE_SETPOINT: 220,
        p.REG_STATUS: 0,
        p.REG_ALARMS: 0,
        p.REG_PROGRAM: 0,
        p.REG_SETPOINT: 215,
        p.REG_SEASON: 0,
        p.REG_WEB: 0,
    }
    return base | {int(k.removeprefix("r")): v for k, v in overrides.items()}


def test_manual_register_addresses() -> None:
    assert (p.REG_AIR_TEMP, p.REG_WATER_TEMP, p.REG_ACTIVE_SETPOINT, p.REG_OUTPUTS, p.REG_MOTOR_SPEED) == (0, 1, 8, 9, 15)
    assert (p.REG_STATUS, p.REG_ALARMS, p.REG_ADDRESS, p.REG_PROGRAM) == (104, 105, 200, 201)
    assert (p.REG_SETPOINT, p.REG_SEASON, p.REG_WEB) == (231, 233, 247)


def test_manual_bit_positions() -> None:
    assert (p.PRG_KEYBOARD_LOCK, p.PRG_STANDBY, p.PRG_MASK) == (0x10, 0x80, 0x7)
    assert (p.OUT_EV1, p.OUT_EV2, p.OUT_CHILLER, p.OUT_BOILER) == (1, 2, 4, 8)
    assert (p.STAT_COOLING, p.STAT_HEATING) == (1, 2)
    assert (p.STAT_FAN_STOP_H2, p.STAT_FAN_STOP_H4, p.STAT_WATER_STOP) == (1 << 4, 1 << 5, 1 << 6)
    assert (p.STAT_ANTIFREEZE, p.STAT_ALARM, p.STAT_TEST, p.STAT_STANDBY) == (1 << 8, 1 << 9, 1 << 10, 1 << 11)
    assert (p.STAT_COM_ERROR, p.STAT_H2_ABSENT, p.STAT_H4_ABSENT) == (1 << 12, 1 << 13, 1 << 14)
    expected_alarms = [
        p.ALARM_COM, p.ALARM_AIR_PROBE, p.ALARM_H4_PROBE, p.ALARM_HARMFUL_WATER, p.ALARM_H2_PROBE, p.ALARM_H4_WATER,
        p.ALARM_HIGH_RESISTANCE, p.ALARM_MOTOR, p.ALARM_GRID, p.ALARM_H2_WATER, p.ALARM_FILTER, p.ALARM_AIR_M4,
    ]
    assert expected_alarms == [1 << n for n in range(12)]  # Com. is bit 0 ... "2 AIR M4" is bit 11
    assert [p.WEB_LED_OFF, p.WEB_FORCED_OFF, p.WEB_LOCK_PROGRAM, p.WEB_LOCK_STANDBY, p.WEB_INHIBIT_EXTREMES,
            p.WEB_SETPOINT_RESTRICTION, p.WEB_LOCK_KEYS, p.WEB_BYPASS, p.WEB_LOCK_SEASON] == [1 << n for n in range(9)]


def test_config_table_matches_the_manual() -> None:
    addresses = {spec.address for spec in p.CONFIG_REGISTERS}
    assert addresses == {202, 203, 209, 221, 222, 242, 245, 246, 210, 211, 212, 230, 213, 234, 214, 215, 218, 219, 243, 244}
    assert len(addresses) == len(p.CONFIG_REGISTERS) == len({s.key for s in p.CONFIG_REGISTERS})
    # starred ("INN-FR-B32 only") registers in the manual
    assert {s.address for s in p.CONFIG_REGISTERS if s.board_only} == {210, 211, 212, 213, 214, 215, 218, 219, 230, 234, 243, 244}
    assert p.REG_ADDRESS not in addresses  # the bus address is deliberately not exposed


def test_parse_basic_state() -> None:
    state = p.parse_state(_values())
    assert state.air_temperature == 24.5 and state.current_temperature == 24.5
    assert state.active_setpoint == 22.0 and state.setpoint == 21.5
    assert state.power and not state.keyboard_locked
    assert state.program is p.FanProgram.AUTOMATIC and state.season is p.Season.AUTO
    assert state.water_temperature is None and state.outputs is None and state.motor_speed is None
    assert state.valve_open is None and not state.alarm_active


def test_optional_registers() -> None:
    state = p.parse_state(_values() | {1: 183, 9: p.OUT_EV1 | p.OUT_BOILER, 15: 1100})
    assert state.water_temperature == 18.3 and state.motor_speed == 1100
    assert state.valve_open is True
    assert p.parse_state(_values() | {9: p.OUT_CHILLER}).valve_open is False
    assert p.parse_state(_values() | {1: None, 9: None}).water_temperature is None


@pytest.mark.parametrize(
    ("raw", "program"),
    [(0, p.FanProgram.AUTOMATIC), (1, p.FanProgram.SILENT), (2, p.FanProgram.NIGHT), (3, p.FanProgram.MAXIMUM), (4, None), (7, None)],
)
def test_fan_program_decoding_ignores_other_bits(raw: int, program: p.FanProgram | None) -> None:
    state = p.parse_state(_values(r201=raw | p.PRG_STANDBY | p.PRG_KEYBOARD_LOCK | 0x0100))
    assert state.program is program
    assert not state.power and state.keyboard_locked


@pytest.mark.parametrize(("raw", "season"), [(0, p.Season.AUTO), (3, p.Season.HEAT), (5, p.Season.COOL), (1, None), (4, None)])
def test_season_decoding(raw: int, season: p.Season | None) -> None:
    assert p.parse_state(_values(r233=raw)).season is season
    if season is not None:
        assert p.SEASON_VALUE[season] == raw


def test_program_and_season_values_round_trip() -> None:
    for program, value in p.PROGRAM_VALUE.items():
        assert p.parse_state(_values(r201=value)).program is program
    assert p.PROGRAM_VALUE == {p.FanProgram.AUTOMATIC: 0, p.FanProgram.SILENT: 1, p.FanProgram.NIGHT: 2, p.FanProgram.MAXIMUM: 3}


def test_active_mode_bits() -> None:
    cooling = p.parse_state(_values(r104=p.STAT_COOLING))
    heating = p.parse_state(_values(r104=p.STAT_HEATING))
    assert cooling.cooling_active and not cooling.heating_active
    assert heating.heating_active and not heating.cooling_active


def test_alarm_handling() -> None:
    assert p.parse_state(_values(r105=p.ALARM_FILTER)).alarm_active
    assert p.parse_state(_values(r104=p.STAT_ALARM)).alarm_active
    assert not p.parse_state(_values(r105=0xF000)).alarm_active  # bits 12-15 are unused
    fault = p.parse_state(_values(r105=p.ALARM_AIR_PROBE))
    assert fault.air_probe_fault and fault.current_temperature is None and fault.air_temperature == 24.5


def test_setpoint_limits_follow_the_unit_configuration() -> None:
    config = {"min_setpoint": 16.0, "max_setpoint": 28.0, "web_min_setpoint": 20.0, "web_max_setpoint": 24.0}
    normal = p.parse_state(_values(), config)
    assert (normal.min_setpoint, normal.max_setpoint) == (16.0, 28.0)
    restricted = p.parse_state(_values(r247=p.WEB_SETPOINT_RESTRICTION), config)
    assert (restricted.min_setpoint, restricted.max_setpoint) == (20.0, 24.0)
    unknown = p.parse_state(_values())
    assert (unknown.min_setpoint, unknown.max_setpoint) == (p.DEFAULT_MIN_SETPOINT, p.DEFAULT_MAX_SETPOINT)


def test_temperature_codec() -> None:
    assert p.decode_temperature(245) == 24.5 and p.encode_temperature(21.5) == 215
    assert p.encode_temperature(19.9) == 199  # no float drift
    assert all(p.decode_temperature(p.encode_temperature(t / 10)) == t / 10 for t in range(50, 401))


def test_apply_bits_keeps_reserved_bits() -> None:
    reserved = 0x0300
    assert p.apply_bits(reserved | 0b010, set_mask=p.PRG_STANDBY) == reserved | 0b010 | p.PRG_STANDBY
    assert p.apply_bits(reserved | p.PRG_STANDBY, clear_mask=p.PRG_STANDBY) == reserved
    assert p.apply_bits(0x0300 | 0b010, set_mask=0b011, clear_mask=p.PRG_MASK) == 0x0300 | 0b011
    assert p.apply_bits(0xFFFF, clear_mask=0) == 0xFFFF


def test_config_codec() -> None:
    spl = p.CONFIG_BY_KEY["min_setpoint"]
    assert p.decode_config(spl, 160) == 16.0 and p.encode_config(spl, 16.5) == 165
    offset = p.CONFIG_BY_KEY["air_probe_offset"]
    assert p.encode_config(offset, -1.5) == 0xFFF1 and p.decode_config(offset, 0xFFF1) == -1.5
    assert p.decode_config(offset, 12) == 1.2
    speed = p.CONFIG_BY_KEY["speed_auto_max"]
    assert p.decode_config(speed, 1100) == 1100 and p.encode_config(speed, 1100) == 1100
    hours = p.CONFIG_BY_KEY["fan_hours"]
    assert p.encode_config(hours, 0) == 0
    for spec, bad in ((spl, 4.9), (spl, 35.1), (offset, 12.1), (speed, 399), (speed, 1701)):
        with pytest.raises(ValueError):
            p.encode_config(spec, bad)


def test_every_config_range_round_trips() -> None:
    for spec in p.CONFIG_REGISTERS:
        for value in (spec.minimum, spec.maximum):
            raw = p.encode_config(spec, value)
            assert 0 <= raw <= 0xFFFF
            assert p.decode_config(spec, raw) == value
