# KHP Device Discovery Specification

## Overview

Device discovery allows AI agents to find and connect to hardware without manual configuration. Three discovery mechanisms are supported for different deployment scenarios.

## 1. mDNS Discovery (Local Network)

For devices on the same LAN.

### Service Type

```
_khp._tcp.local.
```

### TXT Records

| Key | Value | Required |
|-----|-------|----------|
| `manifest` | URL path to manifest endpoint | Yes |
| `type` | Device type (e.g., `liquid_handler`) | Yes |
| `name` | Human-readable device name | Yes |
| `version` | KHP spec version supported | Yes |
| `status` | `online` / `busy` / `maintenance` | Yes |

### Example Announcement

```
tecan-fluent-1._khp._tcp.local. 
  SRV 0 0 8080 tecan-fluent-1.local.
  TXT "manifest=/khp/manifest" "type=liquid_handler" "name=Tecan Fluent 1080" "version=1" "status=online"
```

### Agent Discovery Flow

```
1. Agent sends mDNS query for _khp._tcp.local.
2. Devices respond with SRV + TXT records
3. Agent fetches manifest from each device's manifest URL
4. Agent builds local device registry
5. Agent can now issue READ/WRITE/EXECUTE commands
```

## 2. Central Registry (Enterprise)

For managed deployments with multiple labs/floors/buildings.

### Registry API

```
POST   /v1/devices              Register a device
GET    /v1/devices              List all devices (with filters)
GET    /v1/devices/{id}         Get device details
PUT    /v1/devices/{id}/status  Update device status
DELETE /v1/devices/{id}         Deregister a device
GET    /v1/devices/{id}/manifest  Get full capabilities manifest
```

### Registration Payload

```json
{
  "device_id": "tecan_fluent_lab204",
  "name": "Tecan Fluent 1080",
  "type": "liquid_handler",
  "location": {
    "building": "Research Center A",
    "floor": 2,
    "room": "Lab 204",
    "bench": "3"
  },
  "connection": {
    "host": "10.0.2.45",
    "port": 8080,
    "protocol": "http"
  },
  "driver": "khp-driver-tecan-fluent",
  "driver_version": "1.2.0",
  "status": "online",
  "tags": ["liquid-handling", "high-throughput", "96-well"]
}
```

### Query Filters

```
GET /v1/devices?type=liquid_handler
GET /v1/devices?location.room=Lab+204
GET /v1/devices?status=online
GET /v1/devices?tags=high-throughput
GET /v1/devices?capability=aspirate  (searches manifest procedures)
```

### Health Check

Registry pings devices every 30s (configurable):
- Responsive → `online`
- No response for 60s → `offline`
- Device reports `busy` → busy (still healthy)
- Device reports `maintenance` → excluded from auto-assignment

## 3. Manual Registration (Air-gapped)

For secure environments without network discovery.

### File-Based

Place manifest files in:
```
~/.khp/devices/<device_id>.json
```

### CLI Registration

```bash
khp device register --id thermo_1 --host 192.168.1.50 --port 8080 --driver biorad-cfx96
khp device register --manifest ./my_device_manifest.json
khp device list
khp device remove thermo_1
```

## 4. Device Types (Standard Taxonomy)

| Type | Description | Examples |
|------|-------------|----------|
| `liquid_handler` | Pipetting/dispensing | Tecan, Hamilton, CyBio |
| `thermocycler` | Temperature cycling | BioRad CFX96, ABI |
| `plate_reader` | Absorbance/fluorescence | Varioskan, BMG |
| `robotic_arm` | Pick-and-place | Universal Robots, Doosan |
| `microscope` | Imaging | Zeiss, Nikon, Leica |
| `centrifuge` | Separation | Eppendorf, Beckman |
| `incubator` | Cell culture | Thermo, Liconic |
| `laser` | Optical systems | Any |
| `camera` | Visual monitoring | Any |
| `sensor` | Temperature, humidity, etc. | Any |
| `gpio` | General purpose I/O | Raspberry Pi, Arduino |
| `serial_device` | RS-232/RS-485 equipment | Lab instruments |
| `custom` | Anything else | User-defined |

## 5. Capability-Based Discovery

Agents can search for devices by what they CAN DO, not just what they ARE:

```
DISCOVER capability=measure_temperature
DISCOVER capability=aspirate AND capability=dispense
DISCOVER procedure=run_qpcr
```

This searches all device manifests for matching readable/writable/procedure entries.

## 6. Multi-Agent Coordination

When multiple agents share devices:

### Device Locking

```
EXECUTE device.acquire_lock { agent_id: "agent_1", timeout_s: 300 }
EXECUTE device.release_lock { agent_id: "agent_1" }
```

### Queue-Based Access

```
EXECUTE device.enqueue_procedure { procedure: "aspirate", params: {...}, priority: 5 }
→ { queue_position: 3, estimated_start_s: 45 }
```

### Status Broadcasting

Device emits events when ownership changes:
```json
{ "event": "device_locked", "device_id": "pipette_1", "by": "agent_2", "until": "2026-08-28T15:00:00Z" }
```
