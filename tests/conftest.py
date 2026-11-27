"""Test setup.

``custom_components/daikin_ekwhctrl1_modbus/__init__.py`` imports Home Assistant,
which the protocol and client tests do not need. A stub package object lets the
pure modules be imported as ``daikin_ekwhctrl1_modbus.protocol`` / ``.client``
without executing that ``__init__``.

Tests run their own event loop with ``asyncio.run`` so no pytest plugin is needed.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
import sys
import types

import pytest

ROOT = Path(__file__).resolve().parent.parent
PACKAGE_DIR = ROOT / "custom_components" / "daikin_ekwhctrl1_modbus"

if "daikin_ekwhctrl1_modbus" not in sys.modules:
    stub = types.ModuleType("daikin_ekwhctrl1_modbus")
    stub.__path__ = [str(PACKAGE_DIR)]
    sys.modules["daikin_ekwhctrl1_modbus"] = stub

sys.path.insert(0, str(Path(__file__).resolve().parent))

from daikin_ekwhctrl1_modbus.client import Framing, ModbusGateway  # noqa: E402
from simulator import WallControlSim, GatewaySim  # noqa: E402


@pytest.fixture(params=[Framing.RTU_OVER_TCP, Framing.MODBUS_TCP], ids=["rtu_over_tcp", "modbus_tcp"])
def framing(request: pytest.FixtureRequest) -> Framing:
    """Run every gateway test with both framings."""
    return request.param


@asynccontextmanager
async def open_gateway(
    framing: Framing, units: dict[int, WallControlSim] | None = None, **client_kwargs
) -> AsyncIterator[tuple[GatewaySim, ModbusGateway]]:
    """Start a simulator (units 1 and 2 by default, 3 absent; unit 2 lacks the INN-FR-B32-only registers) and a connected client."""
    sim = GatewaySim(
        framing=framing.value,
        units=units if units is not None else {1: WallControlSim(), 2: WallControlSim(board=False)},
    )
    await sim.start()
    kwargs = {"timeout": 0.4, "retries": 1, "message_delay": 0.0} | client_kwargs
    client = ModbusGateway("127.0.0.1", sim.port, framing, **kwargs)
    try:
        yield sim, client
    finally:
        await client.close()
        await sim.stop()
