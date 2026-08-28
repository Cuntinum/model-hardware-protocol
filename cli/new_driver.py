"""KHP Driver Template Generator.

Creates a complete, production ready driver scaffold from a protocol template.
Generated drivers include proper decorator usage, safety limits, error handling,
connection lifecycle, monitor function, and conformance test stubs.

Usage:
    python -m cli.new_driver --name my_protocol --type sensor --transport tcp
    python -m cli.new_driver --name laser_cutter --type actuator --transport serial
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from datetime import datetime, timezone


TRANSPORT_MAP = {
    "tcp": "ConnectionType.TCP",
    "udp": "ConnectionType.UDP",
    "serial": "ConnectionType.SERIAL",
    "usb": "ConnectionType.USB",
    "bluetooth": "ConnectionType.BLUETOOTH",
    "wireless": "ConnectionType.WIRELESS",
    "can": "ConnectionType.CAN",
}

DEVICE_TYPES = [
    "sensor", "actuator", "controller", "gateway", "instrument",
    "robot", "imaging", "lighting", "energy", "medical",
    "building_automation", "industrial", "laboratory", "vehicle",
]


def generate_driver(name: str, device_type: str = "sensor",
                    transport: str = "tcp", output_dir: str | None = None) -> str:
    """Generate a complete KHP driver from template."""
    class_name = "".join(word.capitalize() for word in name.replace("-", "_").split("_")) + "Device"
    module_name = name.lower().replace("-", "_").replace(" ", "_")

    if output_dir:
        driver_dir = Path(output_dir) / module_name
    else:
        driver_dir = Path("drivers") / module_name

    driver_dir.mkdir(parents=True, exist_ok=True)

    init_content = f'''from .driver import {class_name}

__all__ = ["{class_name}"]
'''

    driver_content = f'''"""KHP Driver: {name.replace("_", " ").title()} Integration.

[DESCRIPTION: Replace with a detailed description of what this driver controls,
what protocol it uses, and what hardware it connects to.]

Requirements:
    pip install [REQUIRED_PACKAGES]
"""
from __future__ import annotations

import time
import asyncio
from typing import Any
from datetime import datetime, timezone

from khp import Driver, readable, writable, procedure, safety, monitor
from khp.core import {TRANSPORT_MAP.get(transport, "ConnectionType.TCP")}


class {class_name}(Driver):
    """{name.replace("_", " ").title()} driver for [DESCRIBE HARDWARE]."""

    name = "{name.replace("_", " ").title()}"
    version = "1.0.0"
    device_type = "{device_type}"
    description = "[DESCRIBE what this driver controls and its key capabilities]"
    connection_type = {TRANSPORT_MAP.get(transport, "ConnectionType.TCP")}

    def __init__(self, device_id: str | None = None, host: str = "192.168.1.1",
                 port: int = 502, **config):
        super().__init__(device_id=device_id, host=host, port=port, **config)
        self._host = host
        self._port = port
        self._connected = False
        self._client = None
        # TODO: Add device specific state variables

    async def connect(self):
        """Establish connection to the device."""
        try:
            # TODO: Implement actual connection logic
            # Example:
            #   self._client = SomeProtocolClient(self._host, self._port)
            #   self._client.connect()
            self._connected = True
            await super().connect()

        except ImportError:
            from khp.errors import DriverLoadError
            raise DriverLoadError(
                "[PACKAGE] not installed. Install with: pip install [PACKAGE]",
                device_id=self.device_id,
            )
        except Exception as e:
            from khp.errors import ConnectionFailedError
            raise ConnectionFailedError(
                f"Cannot connect to device at {{self._host}}:{{self._port}}: {{e}}",
                device_id=self.device_id,
            )

    async def disconnect(self):
        """Close connection and release resources."""
        if self._client:
            # TODO: Close the actual connection
            self._client = None
        self._connected = False
        await super().disconnect()

    def _ensure_connected(self):
        """Guard for operations that require an active connection."""
        if not self._connected:
            from khp.errors import DeviceOfflineError
            raise DeviceOfflineError(
                "Device not connected",
                device_id=self.device_id,
            )

    # ─── READABLE PROPERTIES ─────────────────────────────────────────

    @readable(type="bool", description="Whether the device is currently connected")
    def is_connected(self) -> bool:
        return self._connected

    @readable(type="str", description="Current device status")
    def status(self) -> str:
        if not self._connected:
            return "offline"
        return "online"

    # TODO: Add more @readable properties for sensor values, state, etc.
    # Example:
    # @readable(type="float", description="Current temperature reading", unit="celsius")
    # def temperature(self) -> float:
    #     self._ensure_connected()
    #     return self._client.read_temperature()

    # ─── WRITABLE PROPERTIES ─────────────────────────────────────────

    # TODO: Add @writable properties with @safety decorators
    # Example:
    # @safety(min=0, max=100, reason="Power output cannot exceed rated maximum", hard=True)
    # @writable(type="float", description="Set output power level", unit="percent")
    # def power_level(self, value: float):
    #     self._ensure_connected()
    #     self._client.set_power(value)

    # ─── PROCEDURES ──────────────────────────────────────────────────

    @procedure(description="Get device identification and firmware info")
    def get_device_info(self):
        """Query device for identification data."""
        self._ensure_connected()
        # TODO: Implement actual device query
        return {{
            "manufacturer": "Unknown",
            "model": "Unknown",
            "serial_number": "Unknown",
            "firmware_version": "Unknown",
        }}

    # TODO: Add more @procedure methods for device operations
    # Example:
    # @procedure(description="Calibrate the sensor against a known reference")
    # def calibrate(self, reference_value: float = 0.0):
    #     """Run calibration routine with optional reference."""
    #     self._ensure_connected()
    #     result = self._client.run_calibration(reference_value)
    #     return {{"status": "calibrated", "offset": result.offset}}

    # ─── HEALTH MONITOR ──────────────────────────────────────────────

    @monitor(interval_ms=10000, description="Monitor device connectivity and health")
    def check_health(self) -> dict[str, Any]:
        """Periodic health check for alerting and diagnostics."""
        alerts = []

        if not self._connected:
            alerts.append({{"level": "critical", "message": "Device not connected"}})

        # TODO: Add device specific health checks
        # Example:
        # if self._temperature > 85:
        #     alerts.append({{"level": "warning", "message": f"Temperature high: {{self._temperature}}")}}

        return {{
            "healthy": len(alerts) == 0,
            "connected": self._connected,
            "host": self._host,
            "port": self._port,
            "alerts": alerts,
        }}
'''

    test_content = f'''"""Tests for {class_name}."""
import pytest
from drivers.{module_name} import {class_name}


class Test{class_name}:
    """Conformance and unit tests for the {name.replace("_", " ")} driver."""

    def test_instantiation(self):
        """Driver can be instantiated with default parameters."""
        device = {class_name}()
        assert device.name == "{name.replace("_", " ").title()}"
        assert device.device_type == "{device_type}"
        assert device.version == "1.0.0"

    def test_device_id_parameter(self):
        """Driver accepts custom device_id."""
        device = {class_name}(device_id="test_001")
        assert device.device_id == "test_001"

    def test_readable_properties_exist(self):
        """Driver has readable properties."""
        device = {class_name}()
        assert hasattr(device.is_connected, "_khp_readable")
        assert hasattr(device.status, "_khp_readable")

    def test_offline_guard(self):
        """Operations fail gracefully when not connected."""
        device = {class_name}()
        # Readable properties that check connection should raise
        # or return safe default values

    @pytest.mark.asyncio
    async def test_connect_disconnect_lifecycle(self):
        """Connection lifecycle works correctly."""
        device = {class_name}(host="localhost")
        # NOTE: This test requires a simulator running
        # await device.connect()
        # assert device.is_connected()
        # await device.disconnect()
        # assert not device.is_connected()
'''

    (driver_dir / "__init__.py").write_text(init_content)
    (driver_dir / "driver.py").write_text(driver_content)

    test_dir = Path("tests") / f"test_{module_name}"
    test_dir.mkdir(parents=True, exist_ok=True)
    (test_dir / "__init__.py").write_text("")
    (test_dir / f"test_{module_name}.py").write_text(test_content)

    return str(driver_dir)


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="KHP Driver Template Generator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
Device types: {', '.join(DEVICE_TYPES)}
Transports: {', '.join(TRANSPORT_MAP.keys())}

Examples:
    khp new-driver --name laser_cutter --type actuator --transport serial
    khp new-driver --name weather_station --type sensor --transport tcp
    khp new-driver --name plc_gateway --type controller --transport tcp
        """,
    )
    parser.add_argument("--name", required=True, help="Driver name (snake_case)")
    parser.add_argument("--type", default="sensor", choices=DEVICE_TYPES, help="Device type category")
    parser.add_argument("--transport", default="tcp", choices=list(TRANSPORT_MAP.keys()), help="Physical transport")
    parser.add_argument("--output", default=None, help="Output directory (default: drivers/)")

    args = parser.parse_args()
    output_path = generate_driver(args.name, args.type, args.transport, args.output)
    print(f"Driver generated at: {output_path}")
    print(f"Next steps:")
    print(f"  1. Edit {output_path}/driver.py and replace TODO sections")
    print(f"  2. Run conformance: python -m tests.test_conformance drivers.{args.name}")
    print(f"  3. Test with simulator: python -m simulators.{args.name}")


if __name__ == "__main__":
    main()
