# KHP Capabilities Manifest Schema

## Overview

Every KHP-connected device must provide a capabilities manifest — a JSON document describing everything the device can do, its safety constraints, and connection details. This is the single source of truth that agents use to understand a device.

## Schema Version

Current: `https://khp.dev/schema/manifest/v1`

## Full Schema

```json
{
  "$schema": "https://khp.dev/schema/manifest/v1",
  "device_id": "string (unique, kebab-case)",
  "name": "string (human-readable)",
  "type": "string (from standard taxonomy)",
  "driver": "string (driver package name)",
  "version": "string (semver)",
  "description": "string (what this device does, 1-2 sentences)",

  "readable": {
    "<property_name>": {
      "type": "string (json-schema type or custom: timeseries, image, waveform)",
      "description": "string",
      "unit": "string|null (SI preferred)",
      "range": { "min": "number|null", "max": "number|null" },
      "poll_interval_ms": "number|null (suggested polling rate)",
      "example": "any (example value)"
    }
  },

  "writable": {
    "<property_name>": {
      "type": "string",
      "description": "string",
      "unit": "string|null",
      "min": "number|null",
      "max": "number|null",
      "step": "number|null (minimum increment)",
      "enum": ["array of allowed values|null"],
      "pattern": "string|null (regex for string types)",
      "default": "any|null",
      "requires_confirmation": "bool (default false)"
    }
  },

  "procedures": {
    "<procedure_name>": {
      "description": "string",
      "params": {
        "<param_name>": {
          "type": "string",
          "required": "bool",
          "default": "any|null",
          "description": "string"
        }
      },
      "preconditions": ["array of human-readable conditions"],
      "postconditions": ["array of expected outcomes"],
      "estimated_duration_s": "number|null",
      "requires_confirmation": "bool (default false)",
      "idempotent": "bool (safe to retry?)",
      "reversible": "bool (can be undone?)"
    }
  },

  "safety": {
    "hard_limits": {
      "<constraint_name>": {
        "property": "string (which writable)",
        "max": "number|null",
        "min": "number|null",
        "reason": "string (why this limit exists)"
      }
    },
    "soft_limits": {
      "<constraint_name>": {
        "property": "string",
        "recommended_max": "number|null",
        "recommended_min": "number|null",
        "reason": "string"
      }
    },
    "collision_zones": [
      { "zone_id": "string", "description": "string", "devices_affected": ["array of device_ids"] }
    ],
    "emergency_stop": {
      "supported": "bool",
      "method": "string (how e-stop is executed)",
      "recovery": "string (how to resume after e-stop)"
    },
    "confirmation_required": ["array of procedure names requiring human approval"]
  },

  "metadata": {
    "manufacturer": "string|null",
    "model": "string|null",
    "serial_number": "string|null",
    "firmware_version": "string|null",
    "purchase_date": "string|null (ISO date)",
    "last_calibration": "string|null (ISO date)",
    "next_calibration_due": "string|null (ISO date)",
    "connection": {
      "type": "string (REST|serial|USB|TCP|file_drop|COM|SDK|GUI)",
      "endpoint": "string (connection-type specific)",
      "baud_rate": "number|null (serial only)",
      "timeout_ms": "number (default 5000)"
    },
    "tags": {
      "<key>": "string (free-form, natural language OK)"
    },
    "documentation_url": "string|null",
    "support_contact": "string|null"
  },

  "events": {
    "<event_name>": {
      "description": "string",
      "payload": { "<field>": "type" },
      "severity": "info|warning|error|critical"
    }
  },

  "state_bus": {
    "publishes": ["array of slot names this device writes to"],
    "subscribes": ["array of slot names this device reads from"]
  }
}
```

## Minimal Valid Manifest

The smallest valid manifest for a simple sensor:

```json
{
  "$schema": "https://khp.dev/schema/manifest/v1",
  "device_id": "temp-sensor-1",
  "name": "Lab Temperature Sensor",
  "type": "sensor",
  "driver": "khp-driver-generic-serial",
  "version": "1.0.0",
  "description": "DS18B20 temperature sensor on Raspberry Pi GPIO",
  "readable": {
    "temperature": {
      "type": "float",
      "description": "Current ambient temperature",
      "unit": "celsius",
      "range": { "min": -55, "max": 125 }
    }
  },
  "writable": {},
  "procedures": {},
  "safety": {
    "hard_limits": {},
    "soft_limits": {},
    "emergency_stop": { "supported": false }
  },
  "metadata": {
    "connection": { "type": "serial", "endpoint": "/dev/ttyUSB0", "baud_rate": 9600 }
  }
}
```

## Auto-Generation

Drivers SHOULD auto-generate manifests from device introspection where possible:

```bash
khp manifest generate --driver biorad-cfx96 --endpoint http://localhost:8080
# Queries the device, builds manifest, writes to stdout
```

For devices that can't be introspected, manifests are hand-written by the driver developer.

## Validation

```bash
khp manifest validate ./my_manifest.json
# Checks against schema, reports errors/warnings
```

## Natural Language Tags

The `metadata.tags` field accepts free-form natural language. This is specifically designed for AI agents to understand context:

```json
{
  "tags": {
    "location": "Building A, 2nd floor, Lab 204, left bench near the window",
    "quirks": "Channels 7-8 only work with wide-bore tips. Channel 3 sometimes sticks.",
    "protocols": "Used primarily for cell culture media preparation and ELISA plate setup",
    "maintenance": "Service contract with Tecan, next visit scheduled December 2026",
    "safety_notes": "Keep deck clear of paper/gloves, had a jam incident in March"
  }
}
```

This replaces the "tacit knowledge" problem — instead of living in someone's head, it lives in the manifest where any agent can read it.
