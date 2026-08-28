<p align="center">
  <h1 align="center">Model Hardware Protocol</h1>
  <p align="center">
    <strong>The open standard for AI models to safely control physical hardware.</strong>
  </p>
  <p align="center">
    <a href="https://github.com/Cuntinum/model-hardware-protocol/actions"><img src="https://img.shields.io/github/actions/workflow/status/Cuntinum/model-hardware-protocol/ci.yml?branch=main&style=flat-square&label=CI" alt="CI Status"></a>
    <a href="https://pypi.org/project/khp/"><img src="https://img.shields.io/pypi/v/khp?style=flat-square&label=PyPI" alt="PyPI"></a>
    <a href="https://www.npmjs.com/package/@cuntinum/khp"><img src="https://img.shields.io/npm/v/@cuntinum/khp?style=flat-square&label=npm" alt="npm"></a>
    <a href="https://github.com/Cuntinum/model-hardware-protocol/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-Apache%202.0-blue?style=flat-square" alt="License"></a>
    <a href="https://pypi.org/project/khp/"><img src="https://img.shields.io/pypi/pyversions/khp?style=flat-square" alt="Python"></a>
  </p>
</p>

<br>

**Model Hardware Protocol (MHP)** is a production grade, open source specification that enables any AI model (Claude, GPT, Gemini, Llama, or your own) to discover, communicate with, and orchestrate physical hardware through a universal driver interface.

One protocol. Any model. Any device. Safe by default.

```
AI Agent  ►►►  MHP Driver  ►►►  Physical Device
   │              │                    │
   │         Safety Envelope           │
   │         (hard limits,             │
   │          confirmations)           │
   │              │                    │
   └══════ State Bus (shared memory) ══┘
```

> **Why this exists:** AI agents are moving from chatbots to physical systems: labs, factories, homes, vehicles. There is no standard way for a model to safely operate hardware. MHP fills that gap with a simple, safe, model agnostic protocol backed by real driver implementations.

<br>

## Table of Contents

- [Features](#features)
- [Quick Start](#quick-start)
- [Architecture](#architecture)
- [The Four Primitives](#the-four-primitives)
- [Safety Model](#safety-model)
- [Available Drivers](#available-drivers)
- [MCP Integration](#mcp-integration)
- [State Bus](#state-bus)
- [SDKs](#sdks)
- [CLI](#cli)
- [Project Structure](#project-structure)
- [Comparison](#comparison)
- [Roadmap](#roadmap)
- [Contributing](#contributing)
- [License](#license)

<br>

## Features

| Category | Capability |
|----------|-----------|
| **Universal** | Model agnostic: works with any LLM, agent framework, or automation system |
| **Safe** | Three layer safety: hard limits (blocked), soft limits (clamped), human gates (confirmed) |
| **Simple** | Four primitives: `READ`, `WRITE`, `EXECUTE`, `DISCOVER` covering 95% of hardware interactions |
| **MCP Native** | First class [Model Context Protocol](https://modelcontextprotocol.io) integration: plug into Claude, Cursor, or any MCP client |
| **Multi Language** | Python SDK + TypeScript SDK, drivers in any language |
| **Production Ready** | Audit logging, emergency stop, precondition checks, job tracking, connection lifecycle |
| **Discoverable** | Devices self announce via mDNS, central registry, or manual registration |
| **Composable** | State Bus enables inter device coordination with typed slots, transforms, and pub/sub events |
| **Extensible** | 16 reference drivers ship out of the box; community drivers slot in with zero framework code |
| **Open** | Apache 2.0: use commercially, fork freely, contribute back |

<br>

## Quick Start

### Install

```bash
pip install khp
```

### Write a Driver (5 lines of real code)

```python
from khp import Driver, readable, writable, procedure, safety
from khp.core import ConnectionType

class TemperatureSensor(Driver):
    name = "Lab Thermocouple"
    version = "1.0.0"
    device_type = "sensor"
    connection_type = ConnectionType.SERIAL

    @readable(type="float", description="Current temperature", unit="celsius")
    def temperature(self) -> float:
        return self._read_hardware()

    @safety(max=200.0, min=-40.0, reason="Sensor rated from negative 40 to 200 celsius", hard=True)
    @writable(type="float", description="Alert threshold", unit="celsius")
    def alert_threshold(self, value: float):
        self._threshold = value

    @procedure(description="Zero point calibration", requires_confirmation=True)
    def calibrate(self, reference_temp: float = 0.0):
        self._offset = reference_temp - self._read_raw()
        return {"calibrated": True, "offset": self._offset}
```

### Use via CLI

```bash
khp discover                          # Find devices on network
khp read sensor_1 temperature        # Read a value
khp write sensor_1 alert_threshold 85.0  # Write with safety check
khp execute sensor_1 calibrate       # Run a procedure
khp manifest sensor_1                # View full capabilities
khp serve --port 7400                # Start MCP server
```

### Use via MCP (AI Agent)

```
Agent: What devices are available?
>>> khp_discover() >>> [{id: "sensor_1", type: "sensor", status: "online"}]

Agent: What is the current temperature?
>>> khp_read(device_id="sensor_1", property="temperature")
>>> {value: 23.4, unit: "celsius", timestamp: "2024-08-28T14:30:00Z"}

Agent: Set alert threshold to 300 celsius
>>> khp_write(device_id="sensor_1", property="alert_threshold", value=300.0)
>>> ERROR: SAFETY_BLOCKED: Value 300.0 exceeds hard limit (max: 200.0)
```

<br>

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        AI AGENT LAYER                            │
│  Claude / GPT / Gemini / Llama / Custom Agent / Automation       │
└────────────────────────────┬────────────────────────────────────┘
                             │
              ┌──────────────┼──────────────┐
              │    TRANSPORT LAYER           │
              │  MCP │ REST │ CLI │ WebSocket │
              └──────────────┬──────────────┘
                             │
┌────────────────────────────┼────────────────────────────────────┐
│                    MHP PROTOCOL LAYER                            │
│                                                                  │
│  ┌──────────────┐  ┌──────────────┐  ┌───────────────────────┐ │
│  │   DISCOVERY   │  │  STATE BUS   │  │    SAFETY ENVELOPE     │ │
│  │ mDNS/Registry │  │ Slots+Events │  │ Hard │ Soft │ Confirm  │ │
│  └──────────────┘  └──────────────┘  └───────────────────────┘ │
│                                                                  │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │                    DRIVER INTERFACE                          │ │
│  │  @readable  │  @writable  │  @procedure  │  @safety         │ │
│  │  Auto manifest  │  Audit log  │  Job tracking  │  E Stop    │ │
│  └────────────────────────────────────────────────────────────┘ │
└────────────────────────────┬────────────────────────────────────┘
                             │
┌────────────────────────────┼────────────────────────────────────┐
│                    PHYSICAL HARDWARE                             │
│  Lab Instruments │ Robotics │ IoT │ Industrial │ Cameras │ GPIO  │
└─────────────────────────────────────────────────────────────────┘
```

<br>

## The Four Primitives

Every hardware interaction maps to one of four operations:

| Primitive | Decorator | Purpose | Example |
|-----------|-----------|---------|---------|
| **READ** | `@readable` | Get current sensor/state value | `temperature`, `position`, `status` |
| **WRITE** | `@writable` | Set a parameter (safety enforced) | `set_temperature(95.0)`, `set_speed(100)` |
| **EXECUTE** | `@procedure` | Run a multi step operation | `aspirate(volume=100)`, `home_axis()` |
| **DISCOVER** | automatic | Find devices on the network | `khp discover` with type filter |

This covers 95% of hardware interactions. Complex workflows are built by composing these primitives: the agent decides the sequence, the driver enforces safety at each step.

<br>

## Safety Model

MHP implements defense in depth with three layers that **cannot be bypassed by the AI agent**:

### Layer 1: Hard Limits (BLOCKED)

Operations outside absolute hardware ratings are refused entirely.

```python
@safety(max=120.0, min=4.0, reason="Hardware rated 4 to 120 celsius", hard=True)
@writable(type="float", unit="celsius")
def temperature(self, value: float):
    self.device.set_temp(value)
```

Agent writes `200.0` → **SafetyBlockedError**: operation never executes.

### Layer 2: Soft Limits (CLAMPED)

Values outside recommended operating range are silently clamped.

```python
@safety(max=80.0, min=20.0, reason="Optimal range for this assay", hard=False)
```

Agent writes `100.0` → Actual value set to `80.0`, response notes `safety_check: "clamped"`.

### Layer 3: Confirmation Gates (HUMAN IN THE LOOP)

Dangerous or irreversible operations require explicit human approval.

```python
@procedure(description="Dispense concentrated acid", requires_confirmation=True)
def dispense_acid(self, volume_ml: float):
    ...
```

Agent calls procedure → **ConfirmationRequiredError** with unique ID → Human approves or denies.

### Emergency Stop

Every driver supports `emergency_stop()` which immediately halts all operations, aborts running jobs, and sets the device to a safe state.

```bash
khp emergency_stop           # Stop ALL devices
khp emergency_stop pump_1    # Stop specific device
```

<br>

## Available Drivers

MHP ships with **16 production ready drivers** covering the most common hardware categories:

### Simulated (No Hardware Required)

| Driver | Description | Use Case |
|--------|-------------|----------|
| `SimulatedThermocycler` | Models thermal dynamics with realistic ramp rates | Testing, demos, CI/CD |
| `SimulatedLiquidHandler` | Pipetting simulation with volume tracking | Protocol development |
| `SimulatedRoboticArm` | 6 axis motion with collision detection | Motion planning |

### Physical Hardware

| Driver | Connection | Install | Devices |
|--------|-----------|---------|---------|
| `RaspberryPiGPIO` | GPIO/I2C/SPI | `pip install khp[gpio]` | Any Pi connected sensor/actuator |
| `ArduinoDevice` | Serial USB | `pip install khp[serial]` | Arduino Uno/Mega/Nano |
| `SerialDevice` | RS232/485 | `pip install khp[serial]` | Lab scales, PLCs, legacy equipment |
| `Camera` | USB/IP/RTSP | `pip install khp[camera]` | Webcams, IP cameras, microscopes |

### Network Protocols

| Driver | Connection | Install | Devices |
|--------|-----------|---------|---------|
| `RESTDevice` | HTTP API | `pip install khp[rest]` | Smart plugs, cloud devices, APIs |
| `MQTTDevice` | MQTT broker | `pip install khp[mqtt]` | Zigbee, Z Wave, Home Assistant |
| `ModbusDevice` | TCP/RTU | `pip install khp[modbus]` | Industrial PLCs, HVAC, meters |
| `SCPIDevice` | GPIB/USB/LAN | `pip install khp[visa]` | Oscilloscopes, power supplies, DMMs |

### Robotics and Automotive

| Driver | Connection | Install | Devices |
|--------|-----------|---------|---------|
| `ROS2BridgeDevice` | ROS 2 DDS | `pip install rclpy` | Mobile robots, manipulators, drones, autonomous vehicles |
| `CANBusDevice` | SocketCAN/PCAN | `pip install python-can cantools` | ECUs, electric vehicles, battery management, motor controllers |

### Building and Facilities

| Driver | Connection | Install | Devices |
|--------|-----------|---------|---------|
| `BACnetDevice` | BACnet/IP | `pip install BAC0` | HVAC, lighting, access control, fire alarm, elevators |

### Wireless and IoT

| Driver | Connection | Install | Devices |
|--------|-----------|---------|---------|
| `BLEDevice` | BLE GATT | `pip install bleak` | IoT sensors, wearables, health devices, environmental monitors |

### Enterprise and Lab

| Driver | Connection | Install | Devices |
|--------|-----------|---------|---------|
| `OPCUADevice` | OPC UA TCP | `pip install asyncua` | Siemens, ABB, Rockwell, Beckhoff PLCs, SCADA systems |
| `LabVIEWDevice` | REST/TCP | `pip install httpx` | NI instruments, DAQ, custom test equipment |

### Specialized

| Driver | Connection | Devices |
|--------|-----------|---------|
| `FileDropDevice` | Filesystem | G code printers, batch processors |
| `SmartPlug` | REST | Tasmota, Shelly, TP Link |
| `Zigbee2MQTTDevice` | MQTT | Any Zigbee device via zigbee2mqtt |
| `HomeAssistantDevice` | MQTT | Any HA connected entity |

### Install Everything

```bash
pip install khp[all]
```

<br>

## MCP Integration

MHP is designed as a **first class MCP server**. Any MCP compatible AI agent can control hardware immediately:

### Exposed Tools

| MCP Tool | Description |
|----------|-------------|
| `khp_discover` | Find available devices (filter by type, capability) |
| `khp_read` | Read a device property |
| `khp_write` | Write a device property (safety enforced) |
| `khp_execute` | Run a procedure |
| `khp_manifest` | Get full device capabilities |
| `khp_bus_read` | Read from shared State Bus |
| `khp_bus_write` | Write to shared State Bus |
| `khp_emergency_stop` | Emergency halt (one device or all) |

### Connect to Claude Desktop

Add to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "hardware": {
      "command": "khp",
      "args": ["serve", "--port", "7400"]
    }
  }
}
```

### Connect to Any MCP Client

```python
from mcp.server import KHPMCPServer
from drivers.docker_sim.driver import SimulatedThermocycler

server = KHPMCPServer()
server.register_driver(SimulatedThermocycler())
server.serve(port=7400)
```

<br>

## State Bus

The State Bus provides shared memory for multi device coordination:

```python
from khp.state_bus import StateBus, Transform

bus = StateBus()

# Create typed data channels
bus.create_slot("reactor_temp", type="float", unit="celsius")
bus.create_slot("coolant_flow", type="float", unit="L/min")

# Set up automatic alerts
bus.add_transform(Transform(
    transform_id="overheat_alert",
    input_slot="reactor_temp",
    operation="threshold",
    params={"above": 95.0},
    output_event="overheat_alarm",
))

# React to events
bus.on("overheat_alarm", lambda e: emergency_shutdown())

# Devices write, agents read, or vice versa
bus.write_slot("reactor_temp", 87.5)
```

**Transform operations:** `threshold`, `scale`, `clamp`, `moving_average`, `delta`

<br>

## SDKs

### Python (primary)

```bash
pip install khp
```

```python
from khp import Driver, readable, writable, procedure, safety
from khp.state_bus import StateBus
from khp.discovery import discover, DeviceRegistry
```

### TypeScript

```bash
npm install @cuntinum/khp
```

```typescript
import { Driver, readable, writable, procedure, safety } from '@cuntinum/khp';
import { StateBus } from '@cuntinum/khp';
import { discover, DeviceRegistry } from '@cuntinum/khp';
```

Both SDKs provide identical APIs:

| Module | Purpose |
|--------|---------|
| `Driver` | Base class with decorator driven capability declaration |
| `StateBus` | Inter device communication with typed slots and events |
| `DeviceRegistry` | Discovery and registration of hardware devices |
| `Manifest` | Utilities for validation and export |
| `MCP Server` | Full Model Context Protocol server implementation |
| `Errors` | Complete error types with protocol error codes |

<br>

## CLI

```bash
khp discover                        # Find all devices
khp discover --type thermocycler    # Filter by type
khp list                            # Show registered devices
khp status device_1                 # Device health check
khp read device_1 temperature      # Read property
khp write device_1 setpoint 95.0   # Write with safety
khp execute device_1 calibrate     # Run procedure
khp manifest device_1              # Show capabilities
khp monitor device_1 temperature   # Live monitoring
khp serve --port 7400              # Start MCP server
khp validate ./my_driver/          # Validate driver
khp emergency_stop                 # STOP ALL
```

<br>

## Project Structure

```
model-hardware-protocol/
├── spec/                          Protocol specification
│   ├── PROTOCOL.md                 Full protocol spec (primitives, errors, lifecycle)
│   ├── SAFETY.md                   Three layer safety model
│   ├── DISCOVERY.md                Device discovery (mDNS, registry, manual)
│   └── MANIFEST.md                 Capabilities manifest JSON schema
├── sdk/
│   ├── python/                    Python SDK
│   │   ├── khp/                    Core library (Driver, decorators, state bus, discovery)
│   │   ├── setup.py               PyPI package
│   │   └── pyproject.toml         Build configuration
│   └── typescript/                TypeScript SDK
│       └── src/                    Full mirror of Python SDK
├── drivers/                       16 reference driver implementations
│   ├── docker_sim/                 Simulated devices (no hardware needed)
│   ├── raspberry_pi/               GPIO, I2C, camera
│   ├── arduino/                    Serial text protocol
│   ├── serial_generic/             RS232/485
│   ├── camera/                     USB/IP/RTSP with motion detection
│   ├── rest_generic/               HTTP API + smart plugs
│   ├── mqtt_iot/                   MQTT, Zigbee2MQTT, Home Assistant
│   ├── modbus/                     Industrial Modbus TCP/RTU
│   ├── scpi_visa/                  Lab instruments (GPIB/USB/LAN)
│   ├── file_drop/                  File based interfaces, G code
│   ├── ros2/                       ROS 2 bridge (robots, drones, vehicles)
│   ├── opcua/                      OPC UA industrial (PLCs, SCADA)
│   ├── labview/                    LabVIEW bridge (NI instruments, DAQ)
│   ├── canbus/                     CAN bus (automotive, EVs, ECUs)
│   ├── ble/                        Bluetooth Low Energy (IoT, wearables)
│   └── bacnet/                     BACnet building automation (HVAC)
├── mcp/                           MCP server implementation
├── cli/                           Command line interface
├── certification/                 Driver certification program (3 tiers)
├── dashboard/                     Real time web monitoring UI
├── tests/                         Comprehensive test suite (pytest)
├── docs/                          Documentation
│   ├── getting_started.md          Installation and first driver
│   ├── writing_a_driver.md         Full driver authoring guide
│   ├── safety.md                   Safety configuration reference
│   ├── mcp_setup.md               MCP server setup
│   └── drivers.md                  Available driver catalog
├── examples/                      Usage examples
├── .github/workflows/             CI/CD (test matrix + publish)
├── CONTRIBUTING.md                Contribution guidelines
└── LICENSE                        Apache 2.0
```

<br>

## Comparison

| Feature | Model Hardware Protocol | Anthropic MHS | Custom Integration |
|---------|------------------------|---------------|-------------------|
| Open Source | Apache 2.0 | Research preview (closed) | N/A |
| Model Support | Any model | Claude only | Single model |
| Language SDKs | Python + TypeScript | Python (reference) | Custom |
| Ready Drivers | 16 categories | 3 partners | 0 |
| Safety Model | 3 layer (hard/soft/confirm) | Reference files | Custom per device |
| State Bus | Built in (slots + transforms) | Not specified | Custom |
| MCP Native | Yes (8 tools) | Planned | Custom |
| Discovery | mDNS + registry + manual | Not specified | Custom |
| Emergency Stop | Universal | Per device | Custom |
| Production Ready | Yes (audit, jobs, lifecycle) | Research only | Varies |
| Community Drivers | Accepting PRs now | Not yet | N/A |

<br>

## Design Principles

1. **Safety is non negotiable.** No agent, no prompt, no override can bypass hardware safety limits. The driver defines the envelope; the protocol enforces it.

2. **Four primitives cover everything.** Read, Write, Execute, Discover. Complex workflows emerge from composing simple operations. The agent decides strategy; the driver ensures safety.

3. **Model agnostic by design.** Zero coupling to any specific AI model, framework, or vendor. If it can call a function, it can control hardware.

4. **Drivers are thin.** A driver is a 50 to 200 line adapter, not a framework. Decorators declare capabilities; the protocol handles safety, audit, jobs, and lifecycle.

5. **Production first.** Audit logs, connection lifecycle, job tracking, emergency stop, health checks. Not a demo: designed for real labs and factories.

<br>

## Roadmap

- [x] Protocol specification (4 documents)
- [x] Python SDK (core, decorators, state bus, discovery, manifest, errors)
- [x] TypeScript SDK (full mirror)
- [x] 16 reference drivers (10 original + 6 new)
- [x] MCP server (8 tools)
- [x] CLI (12 commands)
- [x] State Bus (slots, transforms, events)
- [x] Safety framework (3 layer)
- [x] Test suite (91 test cases)
- [x] CI/CD (GitHub Actions)
- [x] Documentation site
- [x] npm publication (v0.1.1)
- [x] Certification program (Bronze/Silver/Gold tiers)
- [x] Web dashboard (real time device monitoring)
- [x] ROS 2 bridge driver
- [x] OPC UA industrial driver
- [x] LabVIEW bridge driver
- [x] CAN bus automotive driver
- [x] BLE (Bluetooth Low Energy) driver
- [x] BACnet building automation driver
- [ ] PyPI publication (v0.1.0)
- [ ] Hardware validation lab results
- [ ] EtherCAT real time industrial driver
- [ ] SiLA 2 lab automation bridge
- [ ] gRPC microservices driver
- [ ] PROFINET industrial driver

<br>

## Contributing

We welcome contributions! See [CONTRIBUTING.md](./CONTRIBUTING.md) for guidelines.

**Ways to contribute:**

- Write a driver for your hardware
- Improve documentation
- Report bugs or suggest features
- Add test cases
- Translate docs

```bash
# Development setup
git clone https://github.com/Cuntinum/model-hardware-protocol.git
cd model-hardware-protocol
pip install -e sdk/python/[dev]
pytest tests/ -v
```

<br>

## License

[Apache 2.0](./LICENSE): Use commercially. Fork freely. Contribute back.

<br>

<p align="center">
  <strong>Built by <a href="https://cuntinum.com">Cuntinum</a></strong><br>
  Making AI work in the physical world.
</p>
