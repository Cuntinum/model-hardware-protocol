# KHP Protocol Specification v0.1 (Draft)

## 1. Overview

The Kinetic Hardware Protocol (KHP) defines a standardized interface between AI agents and physical hardware devices. It enables any AI model to discover, query, control, and orchestrate equipment through a uniform set of primitives.

## 2. Design Principles

1. **Minimal surface area** — Four primitives cover all interactions
2. **Safety-first** — Limits are enforced at the driver level, not the agent level
3. **Stateless requests** — Each command is self-contained (state lives in the State Bus)
4. **Typed data** — All values carry type information for validation
5. **Human-in-the-loop** — Destructive or irreversible operations require confirmation

## 3. Primitives

### 3.1 READ

Retrieve the current value of a device property.

```
READ <device_id>.<property>
→ { value: T, type: string, unit: string, timestamp: ISO8601 }
```

**Examples:**
```
READ thermocycler_1.temperature → { value: 72.3, type: "float", unit: "celsius", timestamp: "2026-08-28T14:30:00Z" }
READ pipette_1.tip_attached → { value: true, type: "bool", unit: null, timestamp: "..." }
READ camera_1.frame → { value: <base64>, type: "image/png", unit: null, timestamp: "..." }
```

### 3.2 WRITE

Set a device property to a new value. Subject to safety envelope validation.

```
WRITE <device_id>.<property> <value>
→ { success: bool, actual_value: T, safety_check: "passed"|"clamped"|"blocked" }
```

**Safety responses:**
- `passed` — value within limits, applied as-is
- `clamped` — value exceeded soft limit, clamped to maximum/minimum
- `blocked` — value exceeded hard limit, operation rejected entirely

**Examples:**
```
WRITE thermocycler_1.temperature 95.0 → { success: true, actual_value: 95.0, safety_check: "passed" }
WRITE laser_1.power 500.0 → { success: false, actual_value: 200.0, safety_check: "blocked", reason: "exceeds max_power=200mW" }
```

### 3.3 EXECUTE

Run a named procedure (multi-step operation) on a device.

```
EXECUTE <device_id>.<procedure> { params }
→ { job_id: string, status: "running"|"completed"|"failed"|"awaiting_confirmation" }
```

**Examples:**
```
EXECUTE pipette_1.aspirate { volume_ul: 100, speed: "normal" } → { job_id: "j_abc123", status: "running" }
EXECUTE robot_arm.move_to { position: [100, 200, 50], speed: 0.5 } → { job_id: "j_def456", status: "awaiting_confirmation" }
```

### 3.4 DISCOVER

Find available devices on the network or local system.

```
DISCOVER [filter]
→ { devices: [{ id, name, type, driver, capabilities_url, status }] }
```

**Examples:**
```
DISCOVER → { devices: [{ id: "thermo_1", name: "BioRad CFX96", type: "thermocycler", ... }, ...] }
DISCOVER type=liquid_handler → { devices: [{ id: "tecan_1", name: "Tecan Fluent 1080", ... }] }
```

## 4. State Bus

The State Bus is a shared data layer accessible by all devices and agents.

### 4.1 Slots

A slot is a named, typed data channel.

```json
{
  "slot_id": "thermo_1.temperature.history",
  "type": "timeseries<float>",
  "unit": "celsius",
  "retention": "1h",
  "subscribers": ["agent_1", "dashboard"]
}
```

### 4.2 Transforms

Composable operations on slot data:

```json
{
  "transform_id": "temp_alert",
  "input_slot": "thermo_1.temperature.current",
  "operation": "threshold",
  "params": { "above": 100.0, "action": "emit_event" },
  "output": "event:temperature_exceeded"
}
```

### 4.3 Events

Pub/sub notification system:

```json
{
  "event": "safety_triggered",
  "device_id": "laser_1",
  "property": "power",
  "detail": "Attempted write of 500mW blocked (max: 200mW)",
  "timestamp": "2026-08-28T14:30:00Z"
}
```

## 5. Capabilities Manifest

Every device exposes a manifest describing its full interface:

```json
{
  "$schema": "https://khp.dev/schema/manifest/v1",
  "device_id": "tecan_fluent_1",
  "name": "Tecan Fluent 1080",
  "type": "liquid_handler",
  "driver": "khp-driver-tecan-fluent",
  "version": "1.0.0",
  "description": "8-channel liquid handling platform with 1080mm deck",
  "readable": {
    "tip_status": { "type": "array<bool>", "description": "Which channels have tips attached" },
    "current_position": { "type": "object", "description": "Current arm XYZ position" },
    "deck_layout": { "type": "object", "description": "What's on each deck position" }
  },
  "writable": {
    "target_well": { "type": "string", "description": "Target well for next operation", "pattern": "[A-H][0-9]{1,2}" },
    "aspirate_volume_ul": { "type": "float", "min": 0.5, "max": 1000, "unit": "uL" },
    "dispense_speed_ul_s": { "type": "float", "min": 1, "max": 500, "unit": "uL/s" }
  },
  "procedures": {
    "aspirate": {
      "params": { "volume_ul": "float", "speed": "enum(slow,normal,fast)" },
      "preconditions": ["tip_attached"],
      "estimated_duration_s": 5
    },
    "dispense": {
      "params": { "volume_ul": "float", "speed": "enum(slow,normal,fast)", "blowout": "bool" },
      "preconditions": ["tip_attached", "volume_loaded"],
      "estimated_duration_s": 5
    },
    "wash_tips": {
      "params": { "cycles": "int", "volume_ul": "float" },
      "estimated_duration_s": 30
    }
  },
  "safety": {
    "hard_limits": {
      "max_aspirate_ul": 1000,
      "max_speed_ul_s": 500,
      "deck_collision_zones": ["pos_1_3", "pos_7_8"]
    },
    "soft_limits": {
      "recommended_max_speed_ul_s": 200
    },
    "preconditions": {
      "aspirate": "tip_status must include at least one true",
      "move": "no collision zone conflict"
    },
    "confirmation_required": ["run_protocol", "home_all_axes"]
  },
  "metadata": {
    "manufacturer": "Tecan",
    "model": "Fluent 1080",
    "serial": null,
    "firmware_version": null,
    "connection": { "type": "REST", "endpoint": "http://localhost:8080/api/v1" },
    "tags": {
      "location": "Lab 204, Bench 3",
      "responsible": "Dr. Smith",
      "notes": "Channels 7-8 have wide-bore tips only"
    }
  }
}
```

## 6. Discovery Protocol

### 6.1 mDNS (Local Network)

Devices broadcast via mDNS service type `_khp._tcp`:
```
_khp._tcp.local.
  tecan-fluent-1._khp._tcp.local. 8080 "manifest=/api/manifest" "type=liquid_handler"
```

### 6.2 Registry (Enterprise)

Central registry for managed deployments:
```
POST /registry/devices { manifest }
GET  /registry/devices?type=thermocycler
GET  /registry/devices/{id}/manifest
```

### 6.3 Manual (Air-gapped)

File-based registration:
```
~/.khp/devices/tecan_fluent_1.json  ← manifest file
```

## 7. Transport Bindings

### 7.1 MCP (Model Context Protocol)

KHP primitives map directly to MCP tools:
```json
{
  "name": "khp_read",
  "description": "Read a property from a connected device",
  "parameters": { "device_id": "string", "property": "string" }
}
```

### 7.2 REST API

```
GET    /devices                          → DISCOVER
GET    /devices/{id}/{property}          → READ
PUT    /devices/{id}/{property}          → WRITE
POST   /devices/{id}/procedures/{name}   → EXECUTE
GET    /devices/{id}/manifest            → capabilities
GET    /bus/slots/{slot_id}              → read state bus
WS     /bus/events                       → subscribe to events
```

### 7.3 CLI

```bash
khp discover [--type TYPE] [--filter EXPR]
khp read DEVICE.PROPERTY
khp write DEVICE.PROPERTY VALUE
khp execute DEVICE.PROCEDURE [--params JSON]
khp manifest DEVICE
khp monitor DEVICE [--properties PROP1,PROP2]
```

## 8. Error Handling

### Error Codes

| Code | Meaning |
|------|---------|
| `DEVICE_NOT_FOUND` | Device ID not in registry |
| `PROPERTY_NOT_FOUND` | Property not in manifest |
| `SAFETY_BLOCKED` | Hard limit exceeded |
| `SAFETY_CLAMPED` | Soft limit applied |
| `PRECONDITION_FAILED` | Required state not met |
| `CONFIRMATION_REQUIRED` | Human must approve |
| `DEVICE_BUSY` | Device executing another procedure |
| `DEVICE_OFFLINE` | Device not responding |
| `TIMEOUT` | Operation exceeded time limit |
| `HARDWARE_ERROR` | Physical device reported fault |

### Error Response Format

```json
{
  "success": false,
  "error": {
    "code": "SAFETY_BLOCKED",
    "message": "Power 500mW exceeds hard limit of 200mW for laser_1",
    "device_id": "laser_1",
    "property": "power",
    "requested_value": 500.0,
    "limit_value": 200.0,
    "recovery_hint": "Use a value between 0 and 200 mW"
  }
}
```

## 9. Security

### 9.1 Authentication

- Drivers authenticate to devices using device-specific credentials (stored in KHP credential vault)
- Agents authenticate to KHP via API key or OAuth token
- No credentials exposed in manifests or state bus

### 9.2 Authorization

Three permission levels:
1. **Observer** — READ + DISCOVER only
2. **Operator** — READ + WRITE + EXECUTE (within safety envelope)
3. **Administrator** — All + modify safety envelope + register/deregister devices

### 9.3 Audit Log

All operations logged with:
- Timestamp, agent identity, device, operation, parameters, result
- Safety events highlighted
- Retention: configurable, default 90 days

## 10. Versioning

- Spec version: semantic versioning (MAJOR.MINOR.PATCH)
- Driver compatibility: drivers declare minimum spec version
- Backward compatible within MAJOR version
- Manifest schema versioned independently

---

*This is a draft specification. Subject to revision during development.*
