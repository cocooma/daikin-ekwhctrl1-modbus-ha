"""Config flow: a gateway is a config entry, a wall control is a subentry."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    SubentryFlowResult,
)
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PORT
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .client import Framing, ModbusConnectionError, ModbusError, ModbusGateway
from .const import (
    CONF_FRAMING,
    CONF_SCAN_INTERVAL,
    CONF_SLAVE_ID,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SLAVE_ID,
    MIN_SCAN_INTERVAL,
    SUBENTRY_WALL_CONTROL,
)
from .protocol import REG_AIR_TEMP


def _gateway_schema(defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_NAME, default=defaults.get(CONF_NAME, "Modbus gateway")): str,
            vol.Required(CONF_HOST, default=defaults.get(CONF_HOST, vol.UNDEFINED)): str,
            vol.Required(CONF_PORT, default=defaults.get(CONF_PORT, DEFAULT_PORT)): vol.All(
                vol.Coerce(int), vol.Range(min=1, max=65535)
            ),
            vol.Required(CONF_FRAMING, default=defaults.get(CONF_FRAMING, Framing.RTU_OVER_TCP.value)): SelectSelector(
                SelectSelectorConfig(
                    options=[f.value for f in Framing],
                    mode=SelectSelectorMode.DROPDOWN,
                    translation_key="framing",
                )
            ),
            vol.Required(
                CONF_SCAN_INTERVAL, default=defaults.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
            ): vol.All(vol.Coerce(int), vol.Range(min=MIN_SCAN_INTERVAL, max=3600)),
        }
    )


def _unique_id(host: str, port: int) -> str:
    return f"{host.strip().lower()}:{port}"


async def _test_connection(data: dict[str, Any]) -> str | None:
    """Open (and close) a TCP connection; return an error key or None."""
    gateway = ModbusGateway(data[CONF_HOST], data[CONF_PORT], Framing(data[CONF_FRAMING]))
    try:
        await gateway.connect()
    except ModbusConnectionError:
        return "cannot_connect"
    finally:
        await gateway.close()
    return None


class WallControlConfigFlow(ConfigFlow, domain=DOMAIN):
    """Add or reconfigure a gateway."""

    VERSION = 1

    @classmethod
    @callback
    def async_get_supported_subentry_types(cls, config_entry: ConfigEntry) -> dict[str, type[ConfigSubentryFlow]]:
        """Wall controls are added as subentries of a gateway."""
        return {SUBENTRY_WALL_CONTROL: WallControlSubentryFlow}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Add a gateway."""
        errors: dict[str, str] = {}
        if user_input is not None:
            await self.async_set_unique_id(_unique_id(user_input[CONF_HOST], user_input[CONF_PORT]))
            self._abort_if_unique_id_configured()
            if error := await _test_connection(user_input):
                errors["base"] = error
            else:
                return self.async_create_entry(title=user_input[CONF_NAME], data=user_input)
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(_gateway_schema({}), user_input),
            errors=errors,
        )

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Change host, port, framing, name or poll interval of a gateway."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            unique_id = _unique_id(user_input[CONF_HOST], user_input[CONF_PORT])
            if any(
                other.unique_id == unique_id and other.entry_id != entry.entry_id
                for other in self._async_current_entries(include_ignore=False)
            ):
                return self.async_abort(reason="already_configured")
            if error := await _test_connection(user_input):
                errors["base"] = error
            else:
                # The entry's update listener reloads it with the new settings.
                self.hass.config_entries.async_update_entry(
                    entry, title=user_input[CONF_NAME], data=user_input, unique_id=unique_id
                )
                return self.async_abort(reason="reconfigure_successful")
        defaults = {**entry.data, CONF_NAME: entry.title} if user_input is None else user_input
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_gateway_schema(defaults),
            errors=errors,
        )


class WallControlSubentryFlow(ConfigSubentryFlow):
    """Add or reconfigure one wall control of a gateway."""

    def __init__(self) -> None:
        """Initialize the flow."""
        self._unanswered_slave: int | None = None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Add a wall control."""
        return await self._async_step_unit("user", user_input)

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Change the name or slave address of a wall control."""
        return await self._async_step_unit("reconfigure", user_input)

    async def _async_step_unit(self, step_id: str, user_input: dict[str, Any] | None) -> SubentryFlowResult:
        entry = self._get_entry()
        subentry = self._get_reconfigure_subentry() if step_id == "reconfigure" else None
        errors: dict[str, str] = {}

        if user_input is not None:
            slave = int(user_input[CONF_SLAVE_ID])
            data = {CONF_NAME: user_input[CONF_NAME], CONF_SLAVE_ID: slave}
            if any(
                other.data.get(CONF_SLAVE_ID) == slave
                and (subentry is None or other.subentry_id != subentry.subentry_id)
                for other in entry.subentries.values()
            ):
                errors[CONF_SLAVE_ID] = "slave_in_use"
            elif slave != self._unanswered_slave and (error := await self._probe(entry, slave)):
                # Offer the same address again: submitting it a second time is accepted,
                # so a unit that is simply switched off or not wired yet can still be added.
                self._unanswered_slave = slave
                errors["base"] = error
            else:
                if subentry is None:
                    return self.async_create_entry(title=data[CONF_NAME], data=data)
                return self.async_update_and_abort(
                    entry, subentry, title=data[CONF_NAME], data=data
                )

        defaults = user_input or (dict(subentry.data) if subentry else {})
        schema = vol.Schema(
            {
                vol.Required(CONF_NAME, default=defaults.get(CONF_NAME, vol.UNDEFINED)): str,
                vol.Required(CONF_SLAVE_ID, default=defaults.get(CONF_SLAVE_ID, vol.UNDEFINED)): NumberSelector(
                    NumberSelectorConfig(min=1, max=MAX_SLAVE_ID, step=1, mode=NumberSelectorMode.BOX)
                ),
            }
        )
        return self.async_show_form(step_id=step_id, data_schema=schema, errors=errors)

    @staticmethod
    async def _probe(entry: ConfigEntry, slave: int) -> str | None:
        """Read the unit once if the gateway is connected; return an error key or None."""
        if entry.state is not ConfigEntryState.LOADED:
            return None  # cannot test now; accept the address
        try:
            await entry.runtime_data.gateway.read_holding_registers(slave, REG_AIR_TEMP, 1)
        except ModbusError:
            return "no_response"
        return None
