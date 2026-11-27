# Daikin EKWHCTRL1 Modbus Home Assistant Integration

[![HACS custom repository](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://www.hacs.xyz/)
[![Release](https://img.shields.io/github/v/release/cocooma/daikin-ekwhctrl1-modbus-ha)](https://github.com/cocooma/daikin-ekwhctrl1-modbus-ha/releases)
[![Validate](https://github.com/cocooma/daikin-ekwhctrl1-modbus-ha/actions/workflows/validate.yml/badge.svg)](https://github.com/cocooma/daikin-ekwhctrl1-modbus-ha/actions/workflows/validate.yml)

Home Assistant custom integration for Daikin **EKWHCTRL1 / EKRTCTRL1** wall controls (INN-FR-B32 board, EKWHCTRL0 kit) on a **Modbus RTU** network, reached through a Modbus TCP gateway. It supports **several gateways**, each with **several wall controls**, and gives every wall control a **climate entity** plus sensors, binary sensors, numbers and switches.

> **Authorship:** this integration, its tests and this README were created by [Claude](https://www.anthropic.com/claude), an AI assistant made by Anthropic, from the Daikin manual *Installation, connection and settings wall mounted control EKWHCTRL1, EKRTCTRL1 for use in a Modbus RTU serial network* (N420384A). Review it before using it on real equipment.

> **Status:** the register map and client are tested against a simulator written from the manual. The Home Assistant glue (config flow, entities, setup) has **not been run in a real Home Assistant**; it was checked only against stand-ins for the HA APIs. Expect to report or fix small issues on first install, and check the [caveats](#caveats) on your hardware.

## How it is organised

| Home Assistant concept | Here |
|---|---|
| Integration entry | one **gateway** (host, port, protocol, poll interval) |
| Subentry ("Add wall control") | one **wall control** (name, Modbus slave address) |
| Device | one per wall control, linked to its gateway device |

All wall controls on a gateway share one TCP connection and requests are sent one at a time (they share one RS485 bus). A wall control that does not answer only makes its own entities unavailable.

## Requirements

- Home Assistant **2025.7.0** or newer (the integration icon is shown from 2026.3).
- Wall controls wired to the RS485 bus (terminal `+ A B -`; bipolar shielded cable, 120 Ω termination at the end of the line, no star wiring).
- A gateway with the serial side set to **9600 baud, 8 data bits, no parity, 1 stop bit**, in either mode:
  - **Modbus RTU over TCP** (transparent serial server: Waveshare, USR, Elfin in transparent mode), or
  - **Modbus TCP** (the gateway converts the protocol).
- A unique **slave address 1-255** per wall control (register 200). Set it on the keypad: with the panel in stand-by hold **AUTO** for 5 s, set the value with the arrow keys, confirm with the stand-by key. The manual recommends leaving **address 1 free**.
- No extra Python packages: the Modbus client is built in.

## Install

### With HACS (recommended)

[![Open this repository in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=cocooma&repository=daikin-ekwhctrl1-modbus-ha&category=integration)

Use the button above, or add the repository by hand:

1. In HACS, open the **⋮** menu → **Custom repositories**.
2. Add `https://github.com/cocooma/daikin-ekwhctrl1-modbus-ha` with type **Integration**.
3. Download **Daikin EKWHCTRL1 Modbus Home Assistant Integration** and restart Home Assistant.

### Manually

Copy `custom_components/daikin_ekwhctrl1_modbus` from the [latest release](https://github.com/cocooma/daikin-ekwhctrl1-modbus-ha/releases/latest) into your Home Assistant `config/custom_components/` folder and restart.

## Set up

1. **Settings → Devices & services → Add integration → Daikin EKWHCTRL1 Modbus Home Assistant Integration**, enter the gateway.
2. On the gateway entry choose **Add wall control** for each unit (name and slave address). If the gateway is connected the address is probed; if the unit does not answer you can submit again to add it anyway.

Gateway and wall control settings can be changed later with **Reconfigure**; the entry reloads automatically.

## Entities (per wall control)

**Climate**

| Feature | Mapping |
|---|---|
| HVAC modes | Off = stand-by (register 201 bit 7); Auto / Heat / Cool = seasonal register 233 (0 / 3 / 5) |
| Fan mode | fan programme, register 201 bits 2:0: automatic / silent / night / maximum |
| Target temperature | absolute setpoint, register 231 (0.1 °C resolution) |
| Current temperature | air temperature T1, register 0 (unknown while the air-probe alarm is set) |
| Limits | read from the unit: SPL/SPH (202/203), or SPL_W/SPH_W (245/246) while the "setpoint restriction" flag is on |
| Action | cooling / heating when a water valve output is on and the active-mode flag says which; idle otherwise. Needs the INN-FR-B32 outputs register (9); unknown without it |

There is no swing, humidity or fan-only mode on this device, so none is offered.

**Sensors:** air temperature, water temperature H2 (reg 1), active setpoint (reg 8), fan speed set (reg 15), active mode cooling/heating.

**Binary sensors** (alarms are diagnostic; the less common ones are disabled by default): alarm (any), filter maintenance, motor fault, air probe, grid contact open, H2/H4 probe, harmful water, H2/H4 water not suitable, high temperature resistance, communication error, remote air probe (M4); status flags antifreeze, test mode, fan stopped for unsuitable water, stopped for unsuitable water, master timeout, probe absent at start-up; relay outputs valve 1, valve 2, chiller request, boiler request.

**Numbers** (configuration category): SPL, SPH, presence/stand-by offset, maintenance interval, fan running hours (write 0 to restart the count), air probe offset, restricted setpoint limits SPL_W/SPH_W; and, disabled by default, the INN-FR-B32 fan speed limits (MVV1-5, MVVP1-3), water limits (LLO, LHI) and water probe offsets. Values are range-checked against the manual before writing.

**Switches** (configuration category): local keyboard lock (register 201 bit 4); web flags (register 247, disabled by default): LED off, forced off, lock programme selection, lock stand-by key, inhibit setpoint extremes, restrict setpoint range, lock all keys, lock season key. The "bypass for one hour" flag is set by the user at the keypad and is not written.

The device address register (200) is deliberately not exposed: changing it takes the unit off the bus.

## How polling works

The device implements only function 03 (read **one** register) and 06 (write one register), so each register is a request of its own. A poll reads 8 required and 3 optional registers per wall control; the 20 configuration registers are read on the first poll and then every 10th poll. With the default 30 s interval and a handful of units this is a small fraction of the 9600 baud bus; raise the interval if you have many units on one bus.

Registers marked "INN-FR-B32 only" in the manual may not exist on the wall controls themselves. If the unit answers one of them with a Modbus exception, it is treated as not supported, is not asked for again, and its entity shows as unavailable.

Flag registers (201, 247) are changed by reading the register just before writing it, then changing only the intended bit, so reserved bits and changes made at the keypad in the meantime are preserved. Writes show in Home Assistant immediately and are confirmed or corrected by the next poll.

## Caveats

- **Not verified on hardware.** Behaviour the manual does not state is an assumption: that every register can be read with function 03 one at a time (this is how the manual describes it), that missing optional registers answer with an exception rather than zero (if yours returns zeros the entities simply show those values), and that 231 reads back what was written.
- Writing the setpoint, season or stand-by bit while the "forced off" or key-lock web flags are on may be ignored by the unit.
- **Setpoint vs active setpoint:** the target temperature is register 231; the "active setpoint" sensor is register 8, which is what the controller actually regulates to.
- Slave addresses above 247 are outside the Modbus standard; the manual allows up to 255, and some gateways may refuse them.

## Development

```
pip install pytest
pytest
```

`tests/simulator.py` simulates a gateway (both framings) with wall controls behind it, implementing only functions 03 and 06. `tests/ha_stubs.py` provides minimal Home Assistant stand-ins so the tests run without Home Assistant; they therefore cannot prove compatibility with a specific HA release.

## License

[MIT](LICENSE)

This is an unofficial project. It is not affiliated with, endorsed by or supported by Daikin. Daikin and the product names mentioned here are trademarks of their respective owners.
