# Writing a KHP Driver

This guide walks through creating a driver for physical hardware.

## Driver Structure

Every KHP driver inherits from `Driver` and uses decorators to declare capabilities:

```python
from khp import Driver, readable, writable, procedure, safety
from khp.core import ConnectionType

class MyDevice(Driver):
    # Required class attributes
    name = "Human-Readable Name"
    version = "1.0.0"
    device_type = "category"  # e.g., "thermocycler", "pump", "sensor"
    description = "What this device does"
    connection_type = ConnectionType.SERIAL  # How we talk to it

    def __init__(self, device_id=None, **config):
        super().__init__(device_id=device_id, **config)
        # Initialize your hardware connection state
        self._serial = None
```

## Connection Lifecycle

Override `connect()` and `disconnect()` for hardware setup/teardown:

```python
async def connect(self):
    import serial
    self._serial = serial.Serial(
        self.config.get("port", "/dev/ttyUSB0"),
        baudrate=self.config.get("baud_rate", 9600),
    )
    await super().connect()  # Sets status to ONLINE

async def disconnect(self):
    if self._serial:
        self._serial.close()
    await super().disconnect()  # Sets status to OFFLINE
```

## Readable Properties

Mark methods that return sensor values:

```python
@readable(type="float", description="Current pressure", unit="bar", poll_interval_ms=500)
def pressure(self) -> float:
    raw = self._serial.readline()
    return float(raw.strip()) / 1000.0
```

Parameters:
- `type`: "float", "int", "bool", "string", "json"
- `unit`: Physical unit (for display and validation)
- `poll_interval_ms`: Suggested polling rate for monitoring
- `min_value`/`max_value`: Expected value range (informational)

## Writable Properties

Mark methods that accept a value to set on hardware:

```python
@writable(type="float", description="Flow rate setpoint", unit="mL/min",
          min_value=0.0, max_value=50.0, step=0.1)
def flow_rate(self, value: float):
    self._serial.write(f"FLOW {value:.1f}\n".encode())
```

Parameters:
- `requires_confirmation`: If True, raises ConfirmationRequiredError before executing
- `step`: Minimum increment (for UI sliders)
- `enum_values`: List of allowed string values

## Procedures

Mark methods for multi-step operations:

```python
@procedure(
    description="Prime the pump with fluid",
    preconditions=["valve_open"],
    estimated_duration_s=30,
    idempotent=True,
)
def prime(self, volume_ml: float = 5.0, speed: str = "slow"):
    self._serial.write(f"PRIME {volume_ml} {speed}\n".encode())
    # Wait for completion
    response = self._serial.readline()
    return {"primed": True, "volume": volume_ml}
```

Parameters are auto-detected from the method signature (type annotations map to JSON Schema types).

## Safety Limits

Stack `@safety` on writable properties:

```python
@safety(max=50.0, min=0.0, reason="Pump rated 0-50 mL/min", hard=True)
@writable(type="float", unit="mL/min")
def flow_rate(self, value: float):
    ...
```

- `hard=True`: Values outside range → SafetyBlockedError (operation refused)
- `hard=False`: Values outside range → clamped to nearest limit (operation proceeds)

## Monitoring

Add continuous monitoring to readable properties:

```python
from khp.decorators import monitor

@monitor(interval_ms=1000, alert_above=95.0, action="emergency_stop")
@readable(type="float", unit="celsius")
def temperature(self) -> float:
    ...
```

## Emergency Stop

Override for hardware-specific shutdown:

```python
async def emergency_stop(self):
    self._serial.write(b"STOP\n")
    self._serial.write(b"VALVE CLOSE\n")
    await super().emergency_stop()  # Marks jobs as aborted
```

## Testing Your Driver

```python
import pytest
from my_driver import MyDevice

@pytest.fixture
def device():
    return MyDevice(device_id="test_1", port="/dev/null")

def test_manifest_valid(device):
    m = device.get_manifest()
    assert m["readable"]
    assert m["type"] == "pump"

@pytest.mark.asyncio
async def test_connect_disconnect(device):
    await device.connect()
    assert device.status.value == "online"
    await device.disconnect()
```

## Publishing

1. Place your driver in `drivers/your-device/driver.py`
2. Add a `drivers/your-device/README.md` with wiring diagram and requirements
3. Submit a PR — CI validates the manifest and runs safety checks
