# Getting Started with KHP

## Installation

### Python SDK

```bash
pip install khp
```

Install with driver-specific extras:

```bash
pip install khp[serial]      # Serial/RS-232 devices
pip install khp[gpio]        # Raspberry Pi GPIO
pip install khp[mqtt]        # MQTT/IoT devices
pip install khp[modbus]      # Modbus TCP/RTU
pip install khp[visa]        # SCPI/VISA instruments
pip install khp[camera]      # USB/IP cameras
pip install khp[all]         # Everything
```

### TypeScript SDK

```bash
npm install @cuntinum/khp
```

## Quick Start (Python)

```python
from khp import Driver, readable, writable, procedure, safety
from khp.core import ConnectionType

class MyTemperatureSensor(Driver):
    name = "My Sensor"
    version = "1.0.0"
    device_type = "temperature_sensor"
    connection_type = ConnectionType.REST

    @readable(type="float", description="Current temperature", unit="celsius")
    def temperature(self) -> float:
        # Read from your actual hardware here
        return 22.5

    @safety(max=100.0, reason="Prevent overheating", hard=True)
    @writable(type="float", description="Alert threshold", unit="celsius")
    def alert_threshold(self, value: float):
        self._threshold = value

    @procedure(description="Reset sensor to factory defaults")
    def factory_reset(self):
        self._threshold = 30.0
        return {"reset": True}
```

## Quick Start (TypeScript)

```typescript
import { Driver, readable, writable, procedure } from '@cuntinum/khp';

class MyTemperatureSensor extends Driver {
  name = 'My Sensor';
  version = '1.0.0';
  deviceType = 'temperature_sensor';

  @readable({ type: 'float', description: 'Current temperature', unit: 'celsius' })
  temperature(): number {
    return 22.5;
  }
}
```

## Core Concepts

### The Four Primitives

KHP exposes hardware through exactly four operations:

| Primitive | Decorator | Purpose |
|-----------|-----------|---------|
| **READ** | `@readable` | Get current sensor value |
| **WRITE** | `@writable` | Set a parameter (safety-checked) |
| **EXECUTE** | `@procedure` | Run a multi-step operation |
| **DISCOVER** | automatic | Find devices on network |

### Safety Model

Three layers protect against dangerous operations:

1. **Hard Limits** — Values outside these ranges are blocked entirely
2. **Soft Limits** — Values are clamped to the recommended range
3. **Confirmation Gates** — Human must approve before execution

```python
@safety(max=200.0, reason="Laser rated for 200mW max", hard=True)
@writable(type="float", unit="mW")
def laser_power(self, value: float):
    self.device.set_power(value)
```

### State Bus

The State Bus provides shared memory between devices:

```python
from khp.state_bus import StateBus, Transform

bus = StateBus()
bus.create_slot("temperature", type="float", unit="celsius")
bus.write_slot("temperature", 85.0)

# Set up alerts
bus.add_transform(Transform(
    transform_id="overheat_alert",
    input_slot="temperature",
    operation="threshold",
    params={"above": 95.0},
    output_event="overheat_alarm",
))
bus.on("overheat_alarm", lambda e: print(f"ALARM: {e}"))
```

### MCP Integration

Expose devices to AI agents via Model Context Protocol:

```python
from mcp.server import KHPMCPServer

server = KHPMCPServer()
server.register_driver(my_sensor)
server.serve(port=7400)
```

## Next Steps

- [Writing a Driver](./writing-a-driver.md)
- [Safety Configuration](./safety.md)
- [MCP Server Setup](./mcp-setup.md)
- [Available Drivers](./drivers.md)
