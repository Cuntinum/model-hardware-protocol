# Safety Configuration

KHP implements a three-layer safety model that prevents AI agents from causing physical damage.

## The Three Layers

### Layer 1: Hard Limits (BLOCKED)

Values outside hard limits are **rejected entirely**. The operation does not execute.

```python
@safety(max=120.0, min=4.0, reason="Hardware rated 4-120°C", hard=True)
@writable(type="float", unit="celsius")
def temperature(self, value: float):
    self.device.set_temp(value)
```

If an agent writes `temperature = 200.0`:
- Operation is **refused**
- `SafetyBlockedError` raised with code `SAFETY_BLOCKED`
- Audit log records the attempt

### Layer 2: Soft Limits (CLAMPED)

Values outside soft limits are **clamped** to the nearest boundary. The operation proceeds with the clamped value.

```python
@safety(max=80.0, min=20.0, reason="Recommended operating range", hard=False)
@writable(type="float", unit="celsius")
def temperature(self, value: float):
    self.device.set_temp(value)
```

If an agent writes `temperature = 100.0`:
- Value is clamped to `80.0`
- Operation proceeds with `80.0`
- Response includes `safety_check: "clamped"`

### Layer 3: Confirmation Gates (HUMAN-IN-THE-LOOP)

Operations marked `requires_confirmation=True` always pause for human approval.

```python
@procedure(description="Dispense hazardous reagent", requires_confirmation=True)
def dispense_acid(self, volume_ml: float):
    ...
```

If an agent calls this procedure:
- `ConfirmationRequiredError` raised with a unique `confirmation_id`
- Agent must present the operation to the human
- Human approves or denies
- If approved, agent retries with the confirmation token

## Safety in the Manifest

Every driver's manifest includes its safety configuration:

```json
{
  "safety": {
    "hard_limits": {
      "temperature_limit": {
        "property": "temperature",
        "max": 120.0,
        "min": 4.0,
        "reason": "Hardware rated 4-120°C"
      }
    },
    "soft_limits": {
      "temperature_soft": {
        "property": "temperature",
        "recommended_max": 80.0,
        "recommended_min": 20.0,
        "reason": "Recommended operating range"
      }
    },
    "emergency_stop": {
      "supported": true
    }
  }
}
```

AI agents can read the manifest BEFORE attempting operations to understand what's safe.

## Emergency Stop

Every driver supports `emergency_stop()`:

```python
await device.emergency_stop()
```

This:
1. Aborts all running jobs
2. Sets device status to ERROR
3. Emits an `emergency_stop` event
4. Calls the driver's custom shutdown logic

Override in your driver for hardware-specific behavior:

```python
async def emergency_stop(self):
    self._serial.write(b"!ESTOP\n")   # Hardware-specific
    self._close_valves()               # Custom safety action
    await super().emergency_stop()     # Base class cleanup
```

## Monitoring & Alerts

The `@monitor` decorator enables continuous safety monitoring:

```python
@monitor(interval_ms=500, alert_above=95.0, action="emergency_stop")
@readable(type="float", unit="celsius")
def temperature(self) -> float:
    return self._read_thermocouple()
```

When `temperature > 95.0`:
- If `action="emergency_stop"`: triggers full e-stop
- If `action="emit_event"`: fires an alert event (default)

## Best Practices

1. **Always define hard limits** for anything that could cause physical damage
2. **Use soft limits** for operational recommendations (wear, efficiency)
3. **Require confirmation** for irreversible or hazardous operations
4. **Override emergency_stop()** with hardware-specific shutdown
5. **Document reasons** — agents use these to explain why an operation was blocked
