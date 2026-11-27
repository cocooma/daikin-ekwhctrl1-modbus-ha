"""Climate entity for an EKWHCTRL1 / EKRTCTRL1 wall control."""

from __future__ import annotations

from typing import Any

from homeassistant.components.climate import (
    ATTR_HVAC_MODE,
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, PRECISION_TENTHS, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import WallControlConfigEntry
from .coordinator import WallControlCoordinator
from .entity import WallControlEntity
from .protocol import (
    PRG_MASK,
    PRG_STANDBY,
    PROGRAM_VALUE,
    REG_PROGRAM,
    REG_SEASON,
    REG_SETPOINT,
    SEASON_VALUE,
    FanProgram,
    Season,
    encode_temperature,
)

PARALLEL_UPDATES = 1

_HVAC_TO_SEASON = {HVACMode.AUTO: Season.AUTO, HVACMode.HEAT: Season.HEAT, HVACMode.COOL: Season.COOL}
_SEASON_TO_HVAC = {season: hvac for hvac, season in _HVAC_TO_SEASON.items()}
_FAN_TO_PROGRAM = {program.value: program for program in FanProgram}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WallControlConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add a climate entity for every wall control of this gateway."""
    for subentry_id, coordinator in entry.runtime_data.coordinators.items():
        async_add_entities([WallControlClimate(coordinator)], config_subentry_id=subentry_id)


class WallControlClimate(WallControlEntity, ClimateEntity):
    """Thermostat for one wall control."""

    _attr_name = None  # the entity is the device
    _attr_translation_key = "wall_control"
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_precision = PRECISION_TENTHS
    _attr_target_temperature_step = 0.5
    _attr_hvac_modes = [HVACMode.OFF, HVACMode.AUTO, HVACMode.HEAT, HVACMode.COOL]
    _attr_fan_modes = list(_FAN_TO_PROGRAM)
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.FAN_MODE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )

    def __init__(self, coordinator: WallControlCoordinator) -> None:
        """Initialize the climate entity."""
        super().__init__(coordinator, "climate")

    # ------------------------------------------------------------------ state

    @property
    def min_temp(self) -> float:
        """Lower setpoint limit as configured in the unit (SPL, or SPL_W when restricted)."""
        return self.coordinator.data.min_setpoint

    @property
    def max_temp(self) -> float:
        """Upper setpoint limit as configured in the unit (SPH, or SPH_W when restricted)."""
        return self.coordinator.data.max_setpoint

    @property
    def hvac_mode(self) -> HVACMode | None:
        """Off while in stand-by, otherwise the seasonal mode."""
        data = self.coordinator.data
        if not data.power:
            return HVACMode.OFF
        return _SEASON_TO_HVAC.get(data.season)

    @property
    def hvac_action(self) -> HVACAction | None:
        """Cooling/heating when a water valve is open; unknown without the INN-FR-B32 outputs."""
        data = self.coordinator.data
        if not data.power:
            return HVACAction.OFF
        if data.valve_open is None:
            return None
        if data.valve_open:
            if data.cooling_active:
                return HVACAction.COOLING
            if data.heating_active:
                return HVACAction.HEATING
        return HVACAction.IDLE

    @property
    def current_temperature(self) -> float | None:
        """Air temperature T1 (unknown while the air probe alarm is set)."""
        return self.coordinator.data.current_temperature

    @property
    def target_temperature(self) -> float | None:
        """Absolute setpoint (register 231)."""
        return self.coordinator.data.setpoint

    @property
    def fan_mode(self) -> str | None:
        """Fan programme: automatic, silent, night or maximum."""
        program = self.coordinator.data.program
        return program.value if program is not None else None

    # ---------------------------------------------------------------- commands

    async def _async_set_standby(self, standby: bool) -> None:
        await self.coordinator.async_update_bits(
            REG_PROGRAM,
            "program_register",
            set_mask=PRG_STANDBY if standby else 0,
            clear_mask=0 if standby else PRG_STANDBY,
        )

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set the seasonal mode and switch on, or go to stand-by."""
        if hvac_mode == HVACMode.OFF:
            await self._async_set_standby(True)
            return
        season = _HVAC_TO_SEASON[hvac_mode]
        await self.coordinator.async_write(REG_SEASON, SEASON_VALUE[season], season_register=SEASON_VALUE[season])
        await self._async_set_standby(False)

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set the target temperature (and the mode, if one is passed along)."""
        if (temperature := kwargs.get(ATTR_TEMPERATURE)) is not None:
            temperature = round(float(temperature), 1)
            if not self.min_temp <= temperature <= self.max_temp:
                raise ServiceValidationError(
                    f"Temperature {temperature} is outside the unit's limits {self.min_temp}-{self.max_temp}"
                )
        if (hvac_mode := kwargs.get(ATTR_HVAC_MODE)) is not None:
            await self.async_set_hvac_mode(hvac_mode)
        if temperature is not None:
            await self.coordinator.async_write(REG_SETPOINT, encode_temperature(temperature), setpoint=temperature)

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        """Select the fan programme, keeping the other bits of register 201."""
        await self.coordinator.async_update_bits(
            REG_PROGRAM,
            "program_register",
            set_mask=PROGRAM_VALUE[_FAN_TO_PROGRAM[fan_mode]],
            clear_mask=PRG_MASK,
        )

    async def async_turn_on(self) -> None:
        """Leave stand-by, keeping the seasonal mode."""
        await self._async_set_standby(False)

    async def async_turn_off(self) -> None:
        """Go to stand-by."""
        await self._async_set_standby(True)
