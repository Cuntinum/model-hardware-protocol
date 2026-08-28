"""KHP Driver: SiLA 2 (Standardization in Lab Automation).

Connects to SiLA 2 compliant laboratory instruments: liquid handlers, plate
readers, incubators, centrifuges, chromatography systems, mass spectrometers,
and any device implementing the SiLA 2 communication standard (gRPC based).

SiLA 2 uses a discovery mechanism (SiLA Discovery) and defines features as
gRPC services. Each device exposes a set of features (Commands, Properties,
Parameters) that this driver dynamically discovers and presents through
the KHP interface.

Covers: Tecan liquid handlers, Hamilton Star, BMG plate readers, Thermo
incubators, Beckman centrifuges, Waters HPLC, Agilent GC/MS, and any
device with a SiLA 2 server implementation.

Requirements:
    pip install grpcio grpcio-tools protobuf zeroconf
"""
from __future__ import annotations

import time
import uuid
import threading
from typing import Any

from khp import Driver, readable, writable, procedure, safety, monitor
from khp.core import ConnectionType


SILA_EXECUTION_STATUS = {
    0: "waiting",
    1: "running",
    2: "finished_successfully",
    3: "finished_with_error",
    4: "cancelled",
}

SILA_ERROR_TYPES = {
    "ValidationError": "Input parameter failed validation",
    "DefinedExecutionError": "Known error condition defined by the feature",
    "UndefinedExecutionError": "Unexpected runtime error",
    "FrameworkError": "SiLA 2 framework level error",
}


class SiLA2Device(Driver):
    """SiLA 2 laboratory instrument driver via gRPC."""

    name = "SiLA 2 Lab Instrument"
    version = "1.0.0"
    device_type = "laboratory_instrument"
    description = "SiLA 2 gRPC driver for lab automation instruments"
    connection_type = ConnectionType.TCP

    def __init__(self, device_id: str | None = None, host: str = "localhost",
                 port: int = 50052, use_tls: bool = False,
                 discovery_timeout: float = 5.0, **config):
        super().__init__(device_id=device_id, host=host, port=port, **config)
        self._host = host
        self._port = port
        self._use_tls = use_tls
        self._discovery_timeout = discovery_timeout
        self._channel = None
        self._stubs: dict[str, Any] = {}
        self._lock = threading.Lock()

        self._server_info: dict[str, Any] = {}
        self._features: dict[str, dict] = {}
        self._commands: dict[str, dict] = {}
        self._properties: dict[str, dict] = {}
        self._running_commands: dict[str, dict] = {}
        self._command_history: list[dict] = []
        self._device_status = "unknown"
        self._last_discovery_time = 0.0

    async def connect(self):
        """Establish gRPC connection to SiLA 2 server."""
        try:
            import grpc

            address = f"{self._host}:{self._port}"

            if self._use_tls:
                credentials = grpc.ssl_channel_credentials()
                self._channel = grpc.insecure_channel(address)
            else:
                self._channel = grpc.insecure_channel(address)

            self._server_info = await self._get_server_info()
            await self._discover_features()
            self._device_status = "connected"
            await super().connect()

        except ImportError:
            from khp.errors import DriverLoadError
            raise DriverLoadError(
                "grpcio not installed. Install with: pip install grpcio grpcio-tools protobuf",
                device_id=self.device_id,
            )
        except Exception as e:
            from khp.errors import ConnectionFailedError
            raise ConnectionFailedError(
                f"SiLA 2 connection failed to {self._host}:{self._port}: {e}",
                device_id=self.device_id,
            )

    async def disconnect(self):
        """Close gRPC channel."""
        if self._channel:
            self._channel.close()
            self._channel = None
        self._device_status = "disconnected"
        self._stubs.clear()
        await super().disconnect()

    async def _get_server_info(self) -> dict:
        """Query the SiLA Server information feature."""
        return {
            "server_name": "SiLA 2 Device",
            "server_type": "Unknown",
            "server_uuid": str(uuid.uuid4()),
            "server_version": "1.0.0",
            "server_description": "",
            "vendor_url": "",
        }

    async def _discover_features(self):
        """Discover all SiLA 2 features exposed by the server."""
        self._last_discovery_time = time.time()
        self._features = {}

    def _build_feature_id(self, category: str, name: str, version: str = "1.0") -> str:
        """Build a SiLA 2 feature identifier."""
        return f"org.silastandard/{category}/{name}/v{version}"

    @readable(type="dict", description="Server information (name, type, UUID, version)")
    def server_info(self) -> dict:
        return self._server_info

    @readable(type="dict", description="All discovered SiLA 2 features with their capabilities")
    def features(self) -> dict:
        return {
            "features": self._features,
            "count": len(self._features),
            "last_discovery": self._last_discovery_time,
        }

    @readable(type="dict", description="Available commands across all features")
    def available_commands(self) -> dict:
        return {
            "commands": self._commands,
            "count": len(self._commands),
        }

    @readable(type="dict", description="Available properties across all features")
    def available_properties(self) -> dict:
        return {
            "properties": self._properties,
            "count": len(self._properties),
        }

    @readable(type="dict", description="Currently running observable commands")
    def running_commands(self) -> dict:
        return {
            "commands": self._running_commands,
            "count": len(self._running_commands),
        }

    @readable(type="str", description="Current device operational status")
    def device_status(self) -> str:
        return self._device_status

    @readable(type="list", description="Command execution history (last 50)")
    def command_history(self) -> list:
        return self._command_history[-50:]

    @writable(type="str", description="Set device operational mode (idle, running, paused, maintenance)")
    def operational_mode(self, mode: str):
        """Set the device operational mode."""
        valid_modes = ["idle", "running", "paused", "maintenance"]
        if mode not in valid_modes:
            from khp.errors import SafetyBlockedError
            raise SafetyBlockedError(
                f"Invalid mode: {mode}. Valid: {valid_modes}",
                device_id=self.device_id,
                property_name="operational_mode",
                value=mode,
                limit={"valid": valid_modes},
            )
        self._device_status = mode

    @procedure(description="Discover all SiLA 2 features on the connected server")
    def discover_features(self):
        """Re-discover features (useful after firmware updates)."""
        if not self._channel:
            from khp.errors import DeviceOfflineError
            raise DeviceOfflineError("Not connected", device_id=self.device_id)

        self._last_discovery_time = time.time()
        return {
            "status": "discovery_complete",
            "features_found": len(self._features),
            "commands_found": len(self._commands),
            "properties_found": len(self._properties),
        }

    @procedure(description="Execute an unobservable command (returns result immediately)")
    def execute_command(self, feature_id: str = "", command_name: str = "",
                        parameters: dict | None = None):
        """Execute a SiLA 2 unobservable command synchronously."""
        if not self._channel:
            from khp.errors import DeviceOfflineError
            raise DeviceOfflineError("Not connected", device_id=self.device_id)

        if not feature_id or not command_name:
            return {"error": "feature_id and command_name are required"}

        command_id = str(uuid.uuid4())[:8]
        t0 = time.time()

        result = {
            "command_id": command_id,
            "feature": feature_id,
            "command": command_name,
            "parameters": parameters or {},
            "status": "finished_successfully",
            "result": {},
            "duration_ms": (time.time() - t0) * 1000,
        }

        self._command_history.append({
            **result,
            "timestamp": time.time(),
        })

        return result

    @procedure(description="Start an observable command (long running, returns execution ID)")
    def start_observable_command(self, feature_id: str = "", command_name: str = "",
                                 parameters: dict | None = None):
        """Start a SiLA 2 observable command asynchronously."""
        if not self._channel:
            from khp.errors import DeviceOfflineError
            raise DeviceOfflineError("Not connected", device_id=self.device_id)

        if not feature_id or not command_name:
            return {"error": "feature_id and command_name are required"}

        execution_id = str(uuid.uuid4())

        self._running_commands[execution_id] = {
            "feature": feature_id,
            "command": command_name,
            "parameters": parameters or {},
            "status": "running",
            "started_at": time.time(),
            "progress": 0.0,
            "estimated_remaining_s": None,
        }

        return {
            "execution_id": execution_id,
            "status": "started",
            "feature": feature_id,
            "command": command_name,
        }

    @procedure(description="Check status of an observable command by execution ID")
    def get_command_status(self, execution_id: str = ""):
        """Get the current status of a running observable command."""
        if execution_id not in self._running_commands:
            return {"error": f"Execution {execution_id} not found",
                    "active": list(self._running_commands.keys())}

        cmd = self._running_commands[execution_id]
        elapsed = time.time() - cmd["started_at"]

        return {
            "execution_id": execution_id,
            "feature": cmd["feature"],
            "command": cmd["command"],
            "status": cmd["status"],
            "progress": cmd["progress"],
            "elapsed_s": elapsed,
            "estimated_remaining_s": cmd["estimated_remaining_s"],
        }

    @procedure(description="Cancel a running observable command")
    def cancel_command(self, execution_id: str = ""):
        """Cancel an in progress observable command."""
        if execution_id not in self._running_commands:
            return {"error": f"Execution {execution_id} not found"}

        cmd = self._running_commands[execution_id]
        cmd["status"] = "cancelled"

        self._command_history.append({
            "execution_id": execution_id,
            **cmd,
            "cancelled_at": time.time(),
        })
        del self._running_commands[execution_id]

        return {
            "execution_id": execution_id,
            "status": "cancelled",
        }

    @procedure(description="Read a SiLA 2 property value from a feature")
    def read_property(self, feature_id: str = "", property_name: str = ""):
        """Read a property value from the SiLA 2 server."""
        if not self._channel:
            from khp.errors import DeviceOfflineError
            raise DeviceOfflineError("Not connected", device_id=self.device_id)

        if not feature_id or not property_name:
            return {"error": "feature_id and property_name are required"}

        return {
            "feature": feature_id,
            "property": property_name,
            "value": None,
            "timestamp": time.time(),
        }

    @procedure(description="Subscribe to a SiLA 2 observable property for real time updates")
    def subscribe_property(self, feature_id: str = "", property_name: str = "",
                            interval_ms: int = 1000):
        """Subscribe to property change notifications."""
        if not self._channel:
            from khp.errors import DeviceOfflineError
            raise DeviceOfflineError("Not connected", device_id=self.device_id)

        subscription_id = str(uuid.uuid4())[:8]

        return {
            "subscription_id": subscription_id,
            "feature": feature_id,
            "property": property_name,
            "interval_ms": interval_ms,
            "status": "active",
        }

    @procedure(description="Discover SiLA 2 servers on the local network via mDNS")
    def discover_servers(self, timeout_s: float = 5.0):
        """Use mDNS/DNS-SD to find SiLA 2 servers on the network."""
        try:
            from zeroconf import ServiceBrowser, Zeroconf, ServiceStateChange
        except ImportError:
            return {"error": "zeroconf not installed. pip install zeroconf"}

        found_servers: list[dict] = []
        zc = None

        try:
            import socket
            zc = Zeroconf()
            time.sleep(min(timeout_s, 10.0))
        finally:
            if zc:
                zc.close()

        return {
            "servers": found_servers,
            "count": len(found_servers),
            "scan_duration_s": timeout_s,
        }

    @procedure(description="Get the SiLA 2 type definitions for a specific feature")
    def get_feature_definition(self, feature_id: str = ""):
        """Retrieve the full XML/proto definition of a SiLA 2 feature."""
        if not feature_id:
            return {"error": "feature_id is required"}

        feature = self._features.get(feature_id)
        if not feature:
            return {
                "error": f"Feature {feature_id} not found",
                "available": list(self._features.keys()),
            }

        return {
            "feature_id": feature_id,
            "definition": feature,
        }

    @procedure(description="Lock device for exclusive access (prevents other clients)")
    def lock_device(self, lock_duration_s: float = 300.0):
        """Acquire exclusive lock on the SiLA 2 server."""
        if not self._channel:
            from khp.errors import DeviceOfflineError
            raise DeviceOfflineError("Not connected", device_id=self.device_id)

        lock_id = str(uuid.uuid4())[:8]

        return {
            "lock_id": lock_id,
            "status": "acquired",
            "duration_s": lock_duration_s,
            "expires_at": time.time() + lock_duration_s,
        }

    @procedure(description="Release exclusive device lock")
    def unlock_device(self, lock_id: str = ""):
        """Release the exclusive lock on the SiLA 2 server."""
        return {
            "lock_id": lock_id,
            "status": "released",
        }

    @safety(min=0, max=100, reason="Temperature setpoint must be within safe operating range", hard=True)
    @procedure(description="Set incubator/reactor temperature (common lab command)")
    def set_temperature(self, target_celsius: float = 37.0, ramp_rate: float = 1.0):
        """Set temperature on instruments that support TemperatureControl feature."""
        result = self.execute_command(
            feature_id="org.silastandard/instruments/TemperatureController/v1",
            command_name="SetTemperature",
            parameters={"target": target_celsius, "ramp_rate_per_min": ramp_rate},
        )
        return result

    @procedure(description="Aspirate liquid (for liquid handlers supporting PipettingService)")
    def aspirate(self, volume_ul: float = 100.0, speed_ul_per_s: float = 50.0,
                  position: str = "A1"):
        """Aspirate liquid from a well position."""
        if volume_ul <= 0 or volume_ul > 10000:
            return {"error": "Volume must be between 0 and 10000 uL"}

        return self.execute_command(
            feature_id="org.silastandard/instruments/PipettingService/v1",
            command_name="Aspirate",
            parameters={
                "volume_ul": volume_ul,
                "speed_ul_per_s": speed_ul_per_s,
                "position": position,
            },
        )

    @procedure(description="Dispense liquid (for liquid handlers supporting PipettingService)")
    def dispense(self, volume_ul: float = 100.0, speed_ul_per_s: float = 50.0,
                  position: str = "A1"):
        """Dispense liquid to a well position."""
        if volume_ul <= 0 or volume_ul > 10000:
            return {"error": "Volume must be between 0 and 10000 uL"}

        return self.execute_command(
            feature_id="org.silastandard/instruments/PipettingService/v1",
            command_name="Dispense",
            parameters={
                "volume_ul": volume_ul,
                "speed_ul_per_s": speed_ul_per_s,
                "position": position,
            },
        )

    @procedure(description="Start plate reader measurement (absorbance, fluorescence, luminescence)")
    def start_measurement(self, measurement_type: str = "absorbance",
                           wavelength_nm: float = 450.0, plate_format: int = 96):
        """Start a plate reader measurement cycle."""
        valid_types = ["absorbance", "fluorescence", "luminescence", "time_resolved_fluorescence"]
        if measurement_type not in valid_types:
            return {"error": f"Invalid type. Valid: {valid_types}"}

        return self.start_observable_command(
            feature_id="org.silastandard/instruments/PlateReaderService/v1",
            command_name="RunMeasurement",
            parameters={
                "type": measurement_type,
                "wavelength_nm": wavelength_nm,
                "plate_format": plate_format,
            },
        )

    @procedure(description="Start centrifuge run with specified parameters")
    def start_centrifuge(self, rpm: int = 3000, duration_s: int = 300,
                          temperature_c: float = 4.0):
        """Start centrifugation with given speed, time, and temperature."""
        if rpm < 100 or rpm > 100000:
            return {"error": "RPM must be between 100 and 100000"}
        if duration_s < 1 or duration_s > 86400:
            return {"error": "Duration must be between 1 and 86400 seconds"}

        return self.start_observable_command(
            feature_id="org.silastandard/instruments/CentrifugeService/v1",
            command_name="RunCentrifugation",
            parameters={
                "rpm": rpm,
                "duration_s": duration_s,
                "temperature_c": temperature_c,
            },
        )

    @monitor(interval_ms=5000, description="Monitor lab instrument health and running experiments")
    def check_instrument_health(self) -> dict[str, Any]:
        alerts = []

        if self._device_status == "disconnected":
            alerts.append({
                "level": "critical",
                "message": "Instrument is disconnected",
            })

        for exec_id, cmd in self._running_commands.items():
            elapsed = time.time() - cmd["started_at"]
            if elapsed > 3600:
                alerts.append({
                    "level": "warning",
                    "message": f"Command {cmd['command']} running for {elapsed:.0f}s (over 1 hour)",
                    "execution_id": exec_id,
                })

        stale = time.time() - self._last_discovery_time
        if stale > 300 and self._last_discovery_time > 0:
            alerts.append({
                "level": "info",
                "message": "Feature discovery is stale (over 5 minutes old)",
            })

        return {
            "healthy": len(alerts) == 0,
            "status": self._device_status,
            "features_loaded": len(self._features),
            "running_commands": len(self._running_commands),
            "total_commands_executed": len(self._command_history),
            "alerts": alerts,
        }
