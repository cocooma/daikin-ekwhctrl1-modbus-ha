"""Constants for the Daikin EKWHCTRL1 Modbus integration."""

from __future__ import annotations

DOMAIN = "daikin_ekwhctrl1_modbus"

# Gateway (config entry) data
CONF_FRAMING = "framing"
CONF_SCAN_INTERVAL = "scan_interval"

# Wall control (subentry) data
SUBENTRY_WALL_CONTROL = "wall_control"
CONF_SLAVE_ID = "slave_id"

DEFAULT_PORT = 502
DEFAULT_SCAN_INTERVAL = 30
MIN_SCAN_INTERVAL = 10

# Register 200 (ADR) accepts 1..255. The manual recommends leaving 1 free.
MAX_SLAVE_ID = 255

# Configuration registers change rarely; read them on every Nth poll only.
CONFIG_POLL_EVERY = 10
