<p align="center">
  <h1 align="center">@cuntinum/khp</h1>
  <p align="center">
    <strong>Model Hardware Protocol: TypeScript SDK for AI agents controlling physical devices.</strong>
  </p>
  <p align="center">
    <a href="https://www.npmjs.com/package/@cuntinum/khp"><img src="https://img.shields.io/npm/v/@cuntinum/khp?style=flat-square" alt="npm version"></a>
    <a href="https://github.com/Cuntinum/model-hardware-protocol"><img src="https://img.shields.io/github/stars/Cuntinum/model-hardware-protocol?style=flat-square" alt="GitHub stars"></a>
    <a href="https://github.com/Cuntinum/model-hardware-protocol/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-Apache%202.0-blue?style=flat-square" alt="License"></a>
  </p>
</p>

<br>

## What is MHP?

**Model Hardware Protocol** is a production grade, open source specification that lets any AI model (Claude, GPT, Gemini, Llama, or your own) discover, communicate with, and orchestrate physical hardware through a universal driver interface.

This package is the official TypeScript SDK. It provides everything you need to build hardware drivers, run an MCP server, and coordinate devices through the State Bus.

<br>

## Installation

```bash
npm install @cuntinum/khp
```

<br>

## Core Modules

| Module | Purpose |
|--------|---------|
| `Driver` | Abstract base class for all hardware drivers |
| `StateBus` | Shared memory with typed slots, transforms, and pub/sub events |
| `DeviceRegistry` | File based device discovery and registration |
| `Manifest` | Capabilities validation and export |
| `KHPMCPServer` | Full Model Context Protocol server (8 tools) |
| `Errors` | Typed error hierarchy with protocol error codes |
| `Decorators` | `@readable`, `@writable`, `@procedure`, `@safety`, `@monitor` |

<br>

## Quick Start: Write a Driver

```typescript
import { Driver, readable, writable, procedure, safety, ConnectionType } from '@cuntinum/khp';

class TemperatureSensor extends Driver {
  name = 'Lab Thermocouple';
  deviceType = 'sensor';
  version = '1.0.0';
  connectionType = ConnectionType.SERIAL;

  private _currentTemp = 22.5;
  private _threshold = 80.0;

  constructor() {
    super('sensor_001');

    // Register readable properties
    this._readableProps.set('temperature', {
      type: 'float',
      description: 'Current temperature',
      unit: 'celsius',
      minValue: -40,
      maxValue: 200,
    });

    // Register writable properties with safety
    this._writableProps.set('alert_threshold', {
      type: 'float',
      description: 'Alert threshold',
      unit: 'celsius',
    });

    // Register hard safety limit
    this._safetyLimits.push({
      propertyName: 'alert_threshold',
      max: 200.0,
      min: -40.0,
      reason: 'Sensor rated from negative 40 to 200 celsius',
      hard: true,
    });
  }

  temperature(): number {
    return this._currentTemp;
  }

  set_alert_threshold(value: number): void {
    this._threshold = value;
  }
}
```

<br>

## MCP Server (AI Agent Integration)

Expose your hardware to any MCP compatible AI agent:

```typescript
import { KHPMCPServer } from '@cuntinum/khp';

const server = new KHPMCPServer();
server.registerDriver(new TemperatureSensor());
server.serve(7400);
```

This exposes 8 MCP tools:

| Tool | Description |
|------|-------------|
| `khp_discover` | Find available devices |
| `khp_read` | Read a device property |
| `khp_write` | Write a property (safety enforced) |
| `khp_execute` | Run a procedure |
| `khp_manifest` | Get full device capabilities |
| `khp_bus_read` | Read from shared State Bus |
| `khp_bus_write` | Write to shared State Bus |
| `khp_emergency_stop` | Emergency halt |

<br>

## State Bus (Multi Device Coordination)

```typescript
import { StateBus, Transform } from '@cuntinum/khp';

const bus = new StateBus();

// Create typed data channels
bus.createSlot('reactor_temp', { type: 'float', unit: 'celsius' });

// Automatic transforms
bus.addTransform(new Transform({
  transformId: 'overheat',
  inputSlot: 'reactor_temp',
  operation: 'threshold',
  params: { above: 95.0 },
  outputEvent: 'overheat_alarm',
}));

// React to events
bus.on('overheat_alarm', (event) => {
  console.log('ALERT: Temperature exceeded threshold!');
});

// Devices write, agents read
bus.writeSlot('reactor_temp', 97.5);
// >>> fires overheat_alarm event
```

<br>

## Device Discovery

```typescript
import { DeviceRegistry } from '@cuntinum/khp';

const registry = new DeviceRegistry({ configDir: './devices' });

// Register devices
registry.register(new TemperatureSensor());

// Discover by type
const sensors = registry.listDevices({ deviceType: 'sensor' });
```

<br>

## Safety Model

MHP enforces three layers of safety that the AI agent cannot bypass:

| Layer | Behavior | Example |
|-------|----------|---------|
| **Hard Limits** | Operation refused entirely | Write 300 celsius to a sensor rated to 200 → `SafetyBlockedError` |
| **Soft Limits** | Value silently clamped | Write 100 to a max 80 → actual value set to 80 |
| **Confirmation Gates** | Requires human approval | Dangerous procedure → `ConfirmationRequiredError` with approval ID |

Emergency stop is universal: `driver.emergencyStop()` immediately halts all operations.

<br>

## Error Types

```typescript
import {
  KHPError,
  SafetyBlockedError,
  ConfirmationRequiredError,
  PreconditionFailedError,
  PropertyNotFoundError,
  DeviceOfflineError,
  ConnectionFailedError,
  TimeoutError,
  ManifestValidationError,
  DriverLoadError,
} from '@cuntinum/khp';
```

All errors include `deviceId`, `code`, and `toJSON()` for serialization.

<br>

## Manifest Generation

Every driver auto generates a JSON manifest describing its full capabilities:

```typescript
const sensor = new TemperatureSensor();
const manifest = sensor.getManifest();

console.log(manifest);
// {
//   "$schema": "https://khp.dev/schema/manifest/v1",
//   "device_id": "sensor_001",
//   "name": "Lab Thermocouple",
//   "type": "sensor",
//   "readable": { "temperature": { type: "float", unit: "celsius" } },
//   "writable": { "alert_threshold": { type: "float", unit: "celsius" } },
//   "safety": { "hard_limits": { ... } },
//   ...
// }
```

<br>

## Full API Reference

### Driver Base Class

| Method | Description |
|--------|-------------|
| `connect()` | Establish connection to hardware |
| `disconnect()` | Close connection gracefully |
| `read(property)` | Read a property value |
| `write(property, value)` | Write a value (safety enforced) |
| `execute(procedure, params)` | Run a named procedure |
| `emergencyStop()` | Immediately halt all operations |
| `getManifest()` | Export capabilities as JSON |
| `healthCheck()` | Test connection status |
| `onEvent(type, handler)` | Subscribe to device events |
| `setTags(tags)` | Add metadata tags |

### StateBus

| Method | Description |
|--------|-------------|
| `createSlot(name, options)` | Create a typed data channel |
| `writeSlot(name, value)` | Update a slot value |
| `readSlot(name)` | Read current slot value |
| `addTransform(transform)` | Add automatic data transform |
| `on(event, handler)` | Subscribe to bus events |

### DeviceRegistry

| Method | Description |
|--------|-------------|
| `register(driver)` | Register a device |
| `deregister(deviceId)` | Remove a device |
| `listDevices(filter?)` | List registered devices |
| `getDevice(deviceId)` | Get a specific device |

<br>

## Requirements

- Node.js 18 or higher
- TypeScript 5.5+ (for decorators)
- Optional: `@modelcontextprotocol/sdk` for MCP server functionality

<br>

## Python SDK

The Python equivalent is available on PyPI:

```bash
pip install khp
```

Both SDKs share identical APIs and full interoperability through the MHP protocol.

<br>

## Links

- [GitHub Repository](https://github.com/Cuntinum/model-hardware-protocol)
- [Full Documentation](https://github.com/Cuntinum/model-hardware-protocol/tree/main/docs)
- [Protocol Specification](https://github.com/Cuntinum/model-hardware-protocol/tree/main/spec)
- [10 Reference Drivers](https://github.com/Cuntinum/model-hardware-protocol/tree/main/drivers)
- [Contributing Guide](https://github.com/Cuntinum/model-hardware-protocol/blob/main/CONTRIBUTING.md)

<br>

## License

Apache 2.0: Use commercially. Fork freely. Contribute back.

<br>

<p align="center">
  <strong>Built by <a href="https://cuntinum.com">Cuntinum</a></strong><br>
  Making AI work in the physical world.
</p>
