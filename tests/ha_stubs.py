"""Minimal stand-ins for the Home Assistant APIs the integration touches.

Real Home Assistant cannot be installed in the test sandbox, so these stubs let
the glue code (coordinator, platforms, setup, config flow) run against the
gateway simulator. They model only the behaviour the integration relies on;
they do not prove compatibility with a particular HA release.
"""

from __future__ import annotations

import dataclasses
import enum
import sys
import types
from typing import Any
from unittest.mock import MagicMock


class _AutoMeta(type):
    def __getattr__(cls, name: str) -> str:
        if name.startswith("__"):
            raise AttributeError(name)
        return name


class Auto(metaclass=_AutoMeta):
    """Accepts any constructor arguments and any attribute (used for selectors, registries)."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.args, self.kwargs = args, kwargs

    def __getattr__(self, name: str) -> Any:
        if name.startswith("__"):
            raise AttributeError(name)
        return MagicMock()


class _Module(types.ModuleType):
    def __getattr__(self, name: str) -> Any:
        if name.startswith("__"):
            raise AttributeError(name)
        cls = type(name, (Auto,), {})
        setattr(self, name, cls)
        return cls


def _mod(name: str, **attrs: Any) -> _Module:
    module = _Module(name)
    module.__path__ = []  # type: ignore[attr-defined]
    for key, value in attrs.items():
        setattr(module, key, value)
    sys.modules[name] = module
    parent, _, child = name.rpartition(".")
    if parent:
        setattr(sys.modules[parent], child, module)
    return module


class _Generic:
    def __class_getitem__(cls, _item: Any) -> type:
        return cls


class HomeAssistantError(Exception):
    """HA base error."""


class ConfigEntryNotReady(HomeAssistantError):
    """Setup should be retried."""


class ServiceValidationError(HomeAssistantError):
    """Bad service input."""


class UpdateFailed(Exception):
    """Coordinator update failed."""


class DataUpdateCoordinator(_Generic):
    def __init__(self, hass, logger, *, config_entry=None, name="", update_interval=None) -> None:
        self.hass, self.config_entry, self.name, self.update_interval = hass, config_entry, name, update_interval
        self.data = None
        self.last_update_success = True
        self.updates: list[Any] = []

    async def async_refresh(self) -> None:
        try:
            self.data = await self._async_update_data()
            self.last_update_success = True
        except UpdateFailed:
            self.last_update_success = False

    def async_set_updated_data(self, data: Any) -> None:
        self.data = data
        self.updates.append(data)


class CoordinatorEntity(_Generic):
    def __init__(self, coordinator: Any) -> None:
        self.coordinator = coordinator

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success


class _StrEnum(enum.StrEnum):
    pass


class HVACMode(_StrEnum):
    OFF = "off"
    AUTO = "auto"
    COOL = "cool"
    HEAT = "heat"
    FAN_ONLY = "fan_only"


class HVACAction(_StrEnum):
    OFF = "off"
    COOLING = "cooling"
    HEATING = "heating"
    FAN = "fan"
    IDLE = "idle"


class ClimateEntityFeature(enum.IntFlag):
    TARGET_TEMPERATURE = 1
    FAN_MODE = 8
    SWING_MODE = 32
    TURN_OFF = 128
    TURN_ON = 256


class _Entity:
    pass


@dataclasses.dataclass(frozen=True, kw_only=True)
class _Description:
    key: str
    translation_key: str | None = None
    device_class: Any = None
    state_class: Any = None
    native_unit_of_measurement: Any = None
    options: list[str] | None = None
    entity_category: Any = None
    entity_registry_enabled_default: bool = True
    native_min_value: float | None = None
    native_max_value: float | None = None
    native_step: float | None = None


class AbortFlow(Exception):
    """Raised by _abort_if_unique_id_configured."""


class _ConfigFlowBase:
    def __init_subclass__(cls, domain: str | None = None, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)

    def async_show_form(self, **kwargs: Any) -> dict:
        return {"type": "form", **kwargs}

    def async_create_entry(self, **kwargs: Any) -> dict:
        return {"type": "create_entry", **kwargs}

    def async_abort(self, **kwargs: Any) -> dict:
        return {"type": "abort", **kwargs}


class ConfigFlow(_ConfigFlowBase):
    hass: Any = None
    entries: list = []
    reconfigure_entry: Any = None
    unique_id: str | None = None

    async def async_set_unique_id(self, unique_id: str) -> None:
        self.unique_id = unique_id

    def _abort_if_unique_id_configured(self) -> None:
        if any(e.unique_id == self.unique_id for e in self.entries):
            raise AbortFlow("already_configured")

    def _async_current_entries(self, include_ignore: bool = True) -> list:
        return self.entries

    def _get_reconfigure_entry(self) -> Any:
        return self.reconfigure_entry

    def add_suggested_values_to_schema(self, schema: Any, suggested: Any) -> Any:
        return schema


class ConfigSubentryFlow(_ConfigFlowBase):
    entry: Any = None
    subentry: Any = None

    def _get_entry(self) -> Any:
        return self.entry

    def _get_reconfigure_subentry(self) -> Any:
        return self.subentry

    def async_update_and_abort(self, entry: Any, subentry: Any, **kwargs: Any) -> dict:
        return {"type": "abort", "reason": "reconfigure_successful", "subentry": subentry, **kwargs}


class ConfigEntryState(enum.Enum):
    LOADED = "loaded"
    NOT_LOADED = "not_loaded"


class _Platform(_StrEnum):
    CLIMATE = "climate"
    SENSOR = "sensor"
    BINARY_SENSOR = "binary_sensor"
    NUMBER = "number"
    SWITCH = "switch"


class _Voluptuous:
    UNDEFINED = object()

    class Schema:
        def __init__(self, schema: dict) -> None:
            self.schema = schema

    @staticmethod
    def Required(key: str, default: Any = None) -> str:
        return key

    @staticmethod
    def All(*validators: Any) -> tuple:
        return validators

    @staticmethod
    def Coerce(kind: type) -> type:
        return kind

    @staticmethod
    def Range(**kwargs: Any) -> dict:
        return kwargs


def install() -> None:
    """Register the stub modules (idempotent)."""
    if "homeassistant" in sys.modules and getattr(sys.modules["homeassistant"], "_is_stub", False):
        return
    root = _mod("homeassistant")
    root._is_stub = True  # type: ignore[attr-defined]
    _mod("homeassistant.components")
    _mod("homeassistant.helpers")
    _mod("homeassistant.exceptions", HomeAssistantError=HomeAssistantError,
         ConfigEntryNotReady=ConfigEntryNotReady, ServiceValidationError=ServiceValidationError)
    _mod("homeassistant.core", callback=lambda f: f, HomeAssistant=Auto)
    _mod("homeassistant.const", Platform=_Platform, CONF_HOST="host", CONF_PORT="port", CONF_NAME="name",
         ATTR_TEMPERATURE="temperature", PRECISION_TENTHS=0.1, PERCENTAGE="%",
         UnitOfTemperature=types.SimpleNamespace(CELSIUS="°C"),
         EntityCategory=types.SimpleNamespace(DIAGNOSTIC="diagnostic", CONFIG="config"))
    _mod("homeassistant.config_entries", ConfigEntry=_Generic, ConfigEntryState=ConfigEntryState,
         ConfigFlow=ConfigFlow, ConfigSubentryFlow=ConfigSubentryFlow, ConfigSubentry=Auto,
         ConfigFlowResult=dict, SubentryFlowResult=dict)
    _mod("homeassistant.helpers.update_coordinator", DataUpdateCoordinator=DataUpdateCoordinator,
         CoordinatorEntity=CoordinatorEntity, UpdateFailed=UpdateFailed)
    _mod("homeassistant.helpers.device_registry")
    _mod("homeassistant.helpers.entity_platform", AddConfigEntryEntitiesCallback=Any)
    _mod("homeassistant.helpers.selector")
    _mod("homeassistant.components.climate", HVACMode=HVACMode, HVACAction=HVACAction,
         ClimateEntityFeature=ClimateEntityFeature, ClimateEntity=_Entity, ATTR_HVAC_MODE="hvac_mode",
         FAN_AUTO="auto", FAN_LOW="low", FAN_MEDIUM="medium", FAN_HIGH="high", SWING_ON="on", SWING_OFF="off")
    for name, entity, desc in (
        ("sensor", "SensorEntity", "SensorEntityDescription"),
        ("binary_sensor", "BinarySensorEntity", "BinarySensorEntityDescription"),
        ("number", "NumberEntity", "NumberEntityDescription"),
        ("switch", "SwitchEntity", "SwitchEntityDescription"),
    ):
        _mod(f"homeassistant.components.{name}", **{entity: _Entity, desc: _Description})
    sys.modules["voluptuous"] = _Voluptuous  # type: ignore[assignment]
