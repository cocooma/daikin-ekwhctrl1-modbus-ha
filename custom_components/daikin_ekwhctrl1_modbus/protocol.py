"""Register map of the Daikin EKWHCTRL1 / EKRTCTRL1 wall controls (INN-FR-B32 board).

Source: "Installation, connection and settings wall mounted control EKWHCTRL1,
EKRTCTRL1 for use in a Modbus RTU serial network" (manual N420384A).

Serial line: 9600 baud, 8 data bits, no parity, **1 stop bit**.
The device implements function 03 (read a register) and 06 (write a register).
Every register, read-only or writable, is a holding register.

Registers marked "INN-FR-B32 only" in the manual may be absent on other
hardware; the coordinator treats an exception response for them as "not
supported" rather than as an error.

This module is pure Python (no Home Assistant imports).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum

# --- single registers ---------------------------------------------------------
REG_AIR_TEMP = 0  # T1  air temperature, R, x0.1 degC
REG_WATER_TEMP = 1  # T2  hot water temperature H2, R, x0.1 degC   (INN-FR-B32 only)
REG_ACTIVE_SETPOINT = 8  # SP  real setpoint, R, x0.1 degC
REG_OUTPUTS = 9  # OUT output relay flags, R                       (INN-FR-B32 only)
REG_MOTOR_SPEED = 15  # MOT_SET motor speed (set), R, 0..1700        (INN-FR-B32 only)
REG_STATUS = 104  # STAT flags, R
REG_ALARMS = 105  # ALR_STAT alarm flags, R
REG_ADDRESS = 200  # ADR device address 1..255, R/W (not exposed: changing it drops the unit off the bus)
REG_PROGRAM = 201  # PRG flag register, R/W
REG_SETPOINT = 231  # SP  absolute setpoint, R/W, x0.1 degC
REG_SEASON = 233  # Man seasonal auto/manual, R/W: 0 auto, 3 heating, 5 cooling
REG_WEB = 247  # WEB flag register, R/W

REQUIRED_REGISTERS = (
    REG_AIR_TEMP,
    REG_ACTIVE_SETPOINT,
    REG_STATUS,
    REG_ALARMS,
    REG_PROGRAM,
    REG_SETPOINT,
    REG_SEASON,
    REG_WEB,
)
OPTIONAL_REGISTERS = (REG_WATER_TEMP, REG_OUTPUTS, REG_MOTOR_SPEED)

# --- register 201 (PRG) -------------------------------------------------------
PRG_MASK = 0b111  # 000 automatic, 001 silent, 010 night, 011 maximum
PRG_KEYBOARD_LOCK = 1 << 4  # 1 = keyboard disabled locally
PRG_STANDBY = 1 << 7  # 1 = stand-by (unit off)
# bits 8-15 are reserved: "do not change the status", so writes are read-modify-write.

# --- register 9 (OUT) ---------------------------------------------------------
OUT_EV1 = 1 << 0
OUT_EV2 = 1 << 1
OUT_CHILLER = 1 << 2
OUT_BOILER = 1 << 3

# --- register 104 (STAT) ------------------------------------------------------
STAT_COOLING = 1 << 0  # "Mod. Raff." active mode cooling
STAT_HEATING = 1 << 1  # "Mod. Risc." active mode heating
STAT_FAN_STOP_H2 = 1 << 4  # fan stopped, water not suitable on H2
STAT_FAN_STOP_H4 = 1 << 5  # fan stopped, water not suitable on H4
STAT_WATER_STOP = 1 << 6  # "B.A." stop for unsuitable water (standby for 45 min)
STAT_ANTIFREEZE = 1 << 8
STAT_ALARM = 1 << 9  # at least one alarm present
STAT_TEST = 1 << 10
STAT_STANDBY = 1 << 11
STAT_COM_ERROR = 1 << 12  # master timeout of 300 s (centralised B32 version)
STAT_H2_ABSENT = 1 << 13  # H2 probe absent at start-up
STAT_H4_ABSENT = 1 << 14  # H4 probe absent at start-up

# --- register 105 (ALR_STAT) --------------------------------------------------
ALARM_COM = 1 << 0  # communication error ("blind" version)
ALARM_AIR_PROBE = 1 << 1
ALARM_H4_PROBE = 1 << 2
ALARM_HARMFUL_WATER = 1 << 3  # "Acq. Dan." block for harmful water
ALARM_H2_PROBE = 1 << 4
ALARM_H4_WATER = 1 << 5  # coil hot water temperature not suitable (2 and 4 pipe)
ALARM_HIGH_RESISTANCE = 1 << 6  # high temperature resistance
ALARM_MOTOR = 1 << 7
ALARM_GRID = 1 << 8  # GRID contact open
ALARM_H2_WATER = 1 << 9  # main coil water temperature not suitable
ALARM_FILTER = 1 << 10  # maintenance required (dirty filter)
ALARM_AIR_M4 = 1 << 11  # M4 control panel only
ALARM_MASK = 0x0FFF

# --- register 247 (WEB) flags -------------------------------------------------
WEB_LED_OFF = 1 << 0
WEB_FORCED_OFF = 1 << 1
WEB_LOCK_PROGRAM = 1 << 2  # disables rotation of the programmes
WEB_LOCK_STANDBY = 1 << 3  # disables select/deselect of stand-by
WEB_INHIBIT_EXTREMES = 1 << 4  # 5 and 40 degC extremes disabled
WEB_SETPOINT_RESTRICTION = 1 << 5  # user limited to SPL_W..SPH_W
WEB_LOCK_KEYS = 1 << 6  # all keys disabled
WEB_BYPASS = 1 << 7  # user bypass of web settings for 1 h (set by the user, not written here)
WEB_LOCK_SEASON = 1 << 8  # seasonal key disabled

DEFAULT_MIN_SETPOINT = 5.0
DEFAULT_MAX_SETPOINT = 35.0


class FanProgram(StrEnum):
    """Fan programme, PRG bits 2:0."""

    AUTOMATIC = "automatic"
    SILENT = "silent"
    NIGHT = "night"
    MAXIMUM = "maximum"


PROGRAM_VALUE = {
    FanProgram.AUTOMATIC: 0b000,
    FanProgram.SILENT: 0b001,
    FanProgram.NIGHT: 0b010,
    FanProgram.MAXIMUM: 0b011,
}
_PROGRAM_BY_VALUE = {value: program for program, value in PROGRAM_VALUE.items()}


class Season(StrEnum):
    """Seasonal register 233."""

    AUTO = "auto"
    HEAT = "heat"
    COOL = "cool"


SEASON_VALUE = {Season.AUTO: 0, Season.HEAT: 3, Season.COOL: 5}
_SEASON_BY_VALUE = {value: season for season, value in SEASON_VALUE.items()}


@dataclass(frozen=True, slots=True)
class ConfigRegister:
    """A configuration register exposed as a number."""

    key: str
    address: int
    scale: float  # engineering value = raw * scale
    minimum: float
    maximum: float
    unit: str | None
    signed: bool = False
    board_only: bool = False  # "INN-FR-B32 only" in the manual


def _speed(key: str, address: int) -> ConfigRegister:
    return ConfigRegister(key, address, 1, 400, 1700, "rpm", board_only=True)


CONFIG_REGISTERS: tuple[ConfigRegister, ...] = (
    ConfigRegister("min_setpoint", 202, 0.1, 5.0, 35.0, "°C"),  # SPL
    ConfigRegister("max_setpoint", 203, 0.1, 5.0, 35.0, "°C"),  # SPH
    ConfigRegister("presence_offset", 209, 0.1, 0.0, 8.5, "K"),  # E_SAVING
    ConfigRegister("maintenance_interval", 221, 1, 0, 32000, "h"),  # ACL (0 = off)
    ConfigRegister("fan_hours", 222, 1, 0, 32000, "h"),  # ACL_TIM
    ConfigRegister("air_probe_offset", 242, 0.1, -12.0, 12.0, "K", signed=True),  # OS1
    ConfigRegister("web_min_setpoint", 245, 0.1, 5.0, 40.0, "°C"),  # SPL_W
    ConfigRegister("web_max_setpoint", 246, 0.1, 5.0, 40.0, "°C"),  # SPH_W
    _speed("speed_silent_min", 210),  # MVV5
    _speed("speed_night_max", 211),  # MVV4
    _speed("speed_silent_max", 212),  # MVV3
    _speed("speed_silent_max_performance", 230),  # MVVP3
    _speed("speed_auto_max", 213),  # MVV2
    _speed("speed_auto_max_performance", 234),  # MVVP2
    _speed("speed_maximum", 214),  # MVV1
    _speed("speed_maximum_performance", 215),  # MVVP1
    ConfigRegister("water_min_heating", 218, 0.1, 0.0, 100.0, "°C", board_only=True),  # LLO
    ConfigRegister("water_max_cooling", 219, 0.1, 0.0, 100.0, "°C", board_only=True),  # LHI
    ConfigRegister("water_probe_h2_offset", 243, 0.1, -12.0, 12.0, "K", signed=True, board_only=True),  # OS2
    ConfigRegister("water_probe_h4_offset", 244, 0.1, -12.0, 12.0, "K", signed=True, board_only=True),  # OS3
)
CONFIG_BY_KEY = {spec.key: spec for spec in CONFIG_REGISTERS}


def to_signed(raw: int) -> int:
    """Interpret a 16-bit register as two's complement."""
    return raw - 0x10000 if raw & 0x8000 else raw


def decode_config(spec: ConfigRegister, raw: int) -> float:
    """Raw register value to engineering units."""
    value = to_signed(raw) if spec.signed else raw
    return round(value * spec.scale, 1) if spec.scale != 1 else value


def encode_config(spec: ConfigRegister, value: float) -> int:
    """Engineering value to a raw register value (range-checked)."""
    if not spec.minimum <= value <= spec.maximum:
        raise ValueError(f"{spec.key}: {value} is outside {spec.minimum}..{spec.maximum}")
    raw = round(value / spec.scale)
    return raw & 0xFFFF if spec.signed else raw


def decode_temperature(raw: int) -> float:
    """Temperature registers are in tenths of a degree."""
    return round(raw / 10, 1)


def encode_temperature(value: float) -> int:
    """Degrees Celsius to a register value."""
    return round(value * 10)


def apply_bits(register: int, *, set_mask: int = 0, clear_mask: int = 0) -> int:
    """Return `register` with bits cleared then set, leaving every other bit alone."""
    return (register & ~clear_mask | set_mask) & 0xFFFF


@dataclass(frozen=True, slots=True)
class WallControlState:
    """Everything read from one wall control."""

    air_temperature: float  # T1
    active_setpoint: float  # register 8
    status: int  # STAT
    alarms: int  # ALR_STAT
    program_register: int  # PRG
    setpoint: float  # register 231
    season_register: int  # register 233
    web_flags: int  # register 247
    water_temperature: float | None = None  # T2, INN-FR-B32 only
    outputs: int | None = None  # OUT, INN-FR-B32 only
    motor_speed: int | None = None  # MOT_SET, INN-FR-B32 only
    config: Mapping[str, float] = field(default_factory=dict)

    # -- operation ------------------------------------------------------------
    @property
    def power(self) -> bool:
        return not self.program_register & PRG_STANDBY

    @property
    def keyboard_locked(self) -> bool:
        return bool(self.program_register & PRG_KEYBOARD_LOCK)

    @property
    def program(self) -> FanProgram | None:
        return _PROGRAM_BY_VALUE.get(self.program_register & PRG_MASK)

    @property
    def season(self) -> Season | None:
        return _SEASON_BY_VALUE.get(self.season_register)

    @property
    def cooling_active(self) -> bool:
        return bool(self.status & STAT_COOLING)

    @property
    def heating_active(self) -> bool:
        return bool(self.status & STAT_HEATING)

    @property
    def valve_open(self) -> bool | None:
        """True if a water valve output is on (None without the INN-FR-B32 outputs register)."""
        if self.outputs is None:
            return None
        return bool(self.outputs & (OUT_EV1 | OUT_EV2))

    # -- measurements ---------------------------------------------------------
    @property
    def air_probe_fault(self) -> bool:
        return bool(self.alarms & ALARM_AIR_PROBE)

    @property
    def current_temperature(self) -> float | None:
        return None if self.air_probe_fault else self.air_temperature

    @property
    def alarm_active(self) -> bool:
        return bool(self.status & STAT_ALARM) or bool(self.alarms & ALARM_MASK)

    # -- limits ---------------------------------------------------------------
    @property
    def min_setpoint(self) -> float:
        key = "web_min_setpoint" if self.web_flags & WEB_SETPOINT_RESTRICTION else "min_setpoint"
        return self.config.get(key, DEFAULT_MIN_SETPOINT)

    @property
    def max_setpoint(self) -> float:
        key = "web_max_setpoint" if self.web_flags & WEB_SETPOINT_RESTRICTION else "max_setpoint"
        return self.config.get(key, DEFAULT_MAX_SETPOINT)


def parse_state(values: Mapping[int, int | None], config: Mapping[str, float] | None = None) -> WallControlState:
    """Build the state from register values keyed by address.

    Required registers must be present; optional ones may be missing or None.
    """
    return WallControlState(
        air_temperature=decode_temperature(values[REG_AIR_TEMP]),
        active_setpoint=decode_temperature(values[REG_ACTIVE_SETPOINT]),
        status=values[REG_STATUS],
        alarms=values[REG_ALARMS],
        program_register=values[REG_PROGRAM],
        setpoint=decode_temperature(values[REG_SETPOINT]),
        season_register=values[REG_SEASON],
        web_flags=values[REG_WEB],
        water_temperature=None if values.get(REG_WATER_TEMP) is None else decode_temperature(values[REG_WATER_TEMP]),
        outputs=values.get(REG_OUTPUTS),
        motor_speed=values.get(REG_MOTOR_SPEED),
        config=dict(config or {}),
    )
