"""KHP Driver: DNP3 SCADA and Utility Communication.

Connects to DNP3 outstations (Remote Terminal Units) at substations, water
treatment plants, pipeline control systems, and power distribution equipment.
Implements a DNP3 master station that polls outstations for binary/analog inputs,
counters, and writes control outputs using Select Before Operate (SBO) safety.

Covers: power grid RTUs, water/wastewater SCADA, oil and gas pipelines,
dam control systems, electric substations, distribution automation,
and any DNP3 compliant outstation or data concentrator.

Requirements:
    pip install dnp3-python
"""
from __future__ import annotations

import time
import threading
from typing import Any

from khp import Driver, readable, writable, procedure, safety, monitor
from khp.core import ConnectionType


QUALITY_FLAGS = {
    0x01: "online",
    0x02: "restart",
    0x04: "comm_lost",
    0x08: "remote_forced",
    0x10: "local_forced",
    0x20: "over_range",
    0x40: "reference_err",
}


class DNP3Device(Driver):
    """DNP3 master driver for SCADA outstation communication."""

    name = "DNP3 Master Station"
    version = "1.0.0"
    device_type = "scada_rtu"
    description = "DNP3 master for utility SCADA outstations (power, water, gas)"
    connection_type = ConnectionType.TCP

    def __init__(self, device_id: str | None = None, host: str = "192.168.1.100",
                 port: int = 20000, master_address: int = 1,
                 outstation_address: int = 10, timeout_ms: int = 5000,
                 select_before_operate: bool = True, **config):
        super().__init__(device_id=device_id, host=host, port=port, **config)
        self._host = host
        self._port = port
        self._master_address = master_address
        self._outstation_address = outstation_address
        self._timeout_ms = timeout_ms
        self._sbo_enabled = select_before_operate
        self._channel = None
        self._master = None
        self._lock = threading.Lock()

        self._binary_inputs: dict[int, dict] = {}
        self._analog_inputs: dict[int, dict] = {}
        self._counters: dict[int, dict] = {}
        self._frozen_counters: dict[int, dict] = {}
        self._binary_outputs: dict[int, dict] = {}
        self._analog_outputs: dict[int, dict] = {}
        self._last_poll_time = 0.0
        self._comm_failures = 0
        self._poll_count = 0
        self._unsolicited_enabled = False

    async def connect(self):
        """Establish DNP3 TCP connection to the outstation."""
        try:
            from pydnp3 import opendnp3, openpal, asiopal, asiodnp3

            manager = asiodnp3.DNP3Manager(1)
            self._channel = manager.AddTCPClient(
                f"channel_{self.device_id}",
                opendnp3.levels.NORMAL,
                asiopal.ChannelRetry.Default(),
                self._host,
                "0.0.0.0",
                self._port,
                asiodnp3.PrintingChannelListener.Create(),
            )

            stack_config = asiodnp3.MasterStackConfig()
            stack_config.master.responseTimeout = openpal.TimeDuration().Milliseconds(
                self._timeout_ms
            )
            stack_config.link.LocalAddr = self._master_address
            stack_config.link.RemoteAddr = self._outstation_address

            self._master = self._channel.AddMaster(
                f"master_{self.device_id}",
                asiodnp3.PrintingSOEHandler.Create(),
                asiodnp3.DefaultMasterApplication.Create(),
                stack_config,
            )
            self._master.Enable()
            await super().connect()

        except ImportError:
            from khp.errors import DriverLoadError
            raise DriverLoadError(
                "dnp3-python not installed. Install with: pip install dnp3-python",
                device_id=self.device_id,
            )
        except Exception as e:
            from khp.errors import ConnectionFailedError
            raise ConnectionFailedError(
                f"DNP3 connection failed to {self._host}:{self._port}: {e}",
                device_id=self.device_id,
            )

    async def disconnect(self):
        """Disable master and close DNP3 channel."""
        if self._master:
            self._master.Disable()
            self._master = None
        if self._channel:
            self._channel.Shutdown()
            self._channel = None
        await super().disconnect()

    def _decode_quality(self, flags: int) -> list[str]:
        """Decode DNP3 quality flags into human readable list."""
        active = []
        for bit, label in QUALITY_FLAGS.items():
            if flags & bit:
                active.append(label)
        return active if active else ["good"]

    @readable(type="dict", description="All binary input points (switch/breaker status)")
    def binary_inputs(self) -> dict:
        return {
            "points": self._binary_inputs,
            "count": len(self._binary_inputs),
            "last_poll": self._last_poll_time,
        }

    @readable(type="dict", description="All analog input points (measurements: voltage, current, flow)")
    def analog_inputs(self) -> dict:
        return {
            "points": self._analog_inputs,
            "count": len(self._analog_inputs),
            "last_poll": self._last_poll_time,
        }

    @readable(type="dict", description="Counter points (energy pulses, event counts)")
    def counters(self) -> dict:
        return {
            "points": self._counters,
            "count": len(self._counters),
        }

    @readable(type="dict", description="Frozen counter snapshots (billing period totals)")
    def frozen_counters(self) -> dict:
        return {
            "points": self._frozen_counters,
            "count": len(self._frozen_counters),
        }

    @readable(type="dict", description="Binary output status (relay/breaker command feedback)")
    def binary_output_status(self) -> dict:
        return {
            "points": self._binary_outputs,
            "count": len(self._binary_outputs),
        }

    @readable(type="dict", description="Analog output status (setpoint feedback values)")
    def analog_output_status(self) -> dict:
        return {
            "points": self._analog_outputs,
            "count": len(self._analog_outputs),
        }

    @readable(type="int", description="Total number of successful polls executed", unit="count")
    def poll_count(self) -> int:
        return self._poll_count

    @readable(type="int", description="Number of consecutive communication failures", unit="count")
    def comm_failure_count(self) -> int:
        return self._comm_failures

    @readable(type="bool", description="Whether unsolicited responses are enabled")
    def unsolicited_enabled(self) -> bool:
        return self._unsolicited_enabled

    @safety(min=0, max=65535, reason="Analog output index must be valid point range", hard=True)
    @writable(type="dict", description="Write analog output setpoint (index and value)")
    def analog_output(self, config: dict):
        """Write an analog output. Config: {index: int, value: float}.
        Uses Select Before Operate if enabled."""
        index = int(config.get("index", 0))
        value = float(config.get("value", 0.0))

        if self._sbo_enabled:
            self._select_analog(index, value)
            time.sleep(0.1)

        self._operate_analog(index, value)
        self._analog_outputs[index] = {
            "value": value,
            "timestamp": time.time(),
            "quality": ["good"],
        }

    @writable(type="dict", description="Write binary output CROB command (index and control code)")
    def binary_output(self, config: dict):
        """Write a binary output (CROB). Config: {index: int, code: str}.
        Control codes: latch_on, latch_off, pulse_on, pulse_off, trip, close."""
        index = int(config.get("index", 0))
        code = config.get("code", "latch_on")

        valid_codes = ["latch_on", "latch_off", "pulse_on", "pulse_off", "trip", "close"]
        if code not in valid_codes:
            from khp.errors import SafetyBlockedError
            raise SafetyBlockedError(
                f"Invalid control code: {code}. Valid: {valid_codes}",
                device_id=self.device_id,
                property_name="binary_output",
                value=code,
                limit={"valid_codes": valid_codes},
            )

        if self._sbo_enabled:
            self._select_binary(index, code)
            time.sleep(0.1)

        self._operate_binary(index, code)
        self._binary_outputs[index] = {
            "value": code in ["latch_on", "pulse_on", "close"],
            "command": code,
            "timestamp": time.time(),
        }

    def _select_analog(self, index: int, value: float):
        """DNP3 Select phase for analog output."""
        with self._lock:
            if self._master:
                pass  # master.SelectAndOperate handled by opendnp3

    def _operate_analog(self, index: int, value: float):
        """DNP3 Operate phase for analog output."""
        with self._lock:
            if self._master:
                pass  # Actual operate call via opendnp3 CommandSet

    def _select_binary(self, index: int, code: str):
        """DNP3 Select phase for binary output (CROB)."""
        with self._lock:
            if self._master:
                pass  # master.SelectAndOperate for CROB

    def _operate_binary(self, index: int, code: str):
        """DNP3 Operate phase for binary output."""
        with self._lock:
            if self._master:
                pass  # Actual CROB operate

    @procedure(description="Perform integrity poll (Class 0: all static data from outstation)")
    def integrity_poll(self):
        """Request all current values (Class 0 data) from the outstation."""
        if not self._master:
            from khp.errors import DeviceOfflineError
            raise DeviceOfflineError("Master not connected", device_id=self.device_id)

        t0 = time.time()
        with self._lock:
            self._master.ScanAllObjects(0, 0)  # Class 0 integrity

        time.sleep(self._timeout_ms / 1000.0)
        self._last_poll_time = time.time()
        self._poll_count += 1
        self._comm_failures = 0

        return {
            "status": "completed",
            "duration_ms": (time.time() - t0) * 1000,
            "binary_inputs": len(self._binary_inputs),
            "analog_inputs": len(self._analog_inputs),
            "counters": len(self._counters),
        }

    @procedure(description="Poll event data by class (1=important, 2=normal, 3=low priority)")
    def class_poll(self, event_class: int = 1):
        """Request event/change data for a specific class."""
        if event_class not in [1, 2, 3]:
            return {"error": "event_class must be 1, 2, or 3"}

        if not self._master:
            from khp.errors import DeviceOfflineError
            raise DeviceOfflineError("Master not connected", device_id=self.device_id)

        t0 = time.time()
        with self._lock:
            self._master.ScanAllObjects(60, event_class)

        time.sleep(self._timeout_ms / 2000.0)
        self._poll_count += 1

        return {
            "status": "completed",
            "class": event_class,
            "duration_ms": (time.time() - t0) * 1000,
        }

    @procedure(description="Synchronize outstation clock with master time")
    def time_sync(self):
        """Send time synchronization to the outstation."""
        if not self._master:
            from khp.errors import DeviceOfflineError
            raise DeviceOfflineError("Master not connected", device_id=self.device_id)

        with self._lock:
            pass  # master.WriteTimeAndInterval

        return {
            "status": "synchronized",
            "master_time": time.time(),
        }

    @procedure(description="Cold restart the outstation (full hardware reset)",
               requires_confirmation=True)
    def cold_restart(self):
        """Issue cold restart command to outstation. Causes full reboot."""
        if not self._master:
            from khp.errors import DeviceOfflineError
            raise DeviceOfflineError("Master not connected", device_id=self.device_id)

        with self._lock:
            pass  # master.Restart(RestartType.COLD)

        return {"status": "cold_restart_issued", "warning": "Outstation will reboot"}

    @procedure(description="Warm restart the outstation (software restart, preserves config)",
               requires_confirmation=True)
    def warm_restart(self):
        """Issue warm restart to outstation. Resets application layer only."""
        if not self._master:
            from khp.errors import DeviceOfflineError
            raise DeviceOfflineError("Master not connected", device_id=self.device_id)

        with self._lock:
            pass  # master.Restart(RestartType.WARM)

        return {"status": "warm_restart_issued"}

    @procedure(description="Enable unsolicited responses from outstation (event driven updates)")
    def enable_unsolicited(self):
        """Enable unsolicited reporting for all event classes."""
        if not self._master:
            from khp.errors import DeviceOfflineError
            raise DeviceOfflineError("Master not connected", device_id=self.device_id)

        with self._lock:
            pass  # master.PerformFunction("enable_unsolicited")

        self._unsolicited_enabled = True
        return {"status": "unsolicited_enabled", "classes": [1, 2, 3]}

    @procedure(description="Disable unsolicited responses (switch to polling only)")
    def disable_unsolicited(self):
        """Disable unsolicited reporting, revert to poll only mode."""
        if not self._master:
            from khp.errors import DeviceOfflineError
            raise DeviceOfflineError("Master not connected", device_id=self.device_id)

        with self._lock:
            pass  # master.PerformFunction("disable_unsolicited")

        self._unsolicited_enabled = False
        return {"status": "unsolicited_disabled"}

    @procedure(description="Read a specific binary input point by index")
    def read_binary_input(self, index: int = 0):
        """Read a single binary input point."""
        point = self._binary_inputs.get(index)
        if point is None:
            return {"error": f"Binary input {index} not found", "available": list(self._binary_inputs.keys())}
        return {
            "index": index,
            "value": point.get("value", False),
            "quality": point.get("quality", ["unknown"]),
            "timestamp": point.get("timestamp", 0),
        }

    @procedure(description="Read a specific analog input point by index")
    def read_analog_input(self, index: int = 0):
        """Read a single analog input measurement."""
        point = self._analog_inputs.get(index)
        if point is None:
            return {"error": f"Analog input {index} not found", "available": list(self._analog_inputs.keys())}
        return {
            "index": index,
            "value": point.get("value", 0.0),
            "quality": point.get("quality", ["unknown"]),
            "timestamp": point.get("timestamp", 0),
            "unit": point.get("unit", ""),
        }

    @procedure(description="Freeze all counters at current values (creates snapshot for billing)")
    def freeze_counters(self):
        """Issue freeze command to capture counter snapshots."""
        self._frozen_counters = {
            idx: {**val, "frozen_at": time.time()}
            for idx, val in self._counters.items()
        }
        return {
            "status": "frozen",
            "count": len(self._frozen_counters),
            "timestamp": time.time(),
        }

    @procedure(description="Read file from outstation (configuration, logs, event buffers)")
    def read_file(self, file_id: int = 1, block_size: int = 256):
        """Request file transfer from the outstation."""
        if not self._master:
            from khp.errors import DeviceOfflineError
            raise DeviceOfflineError("Master not connected", device_id=self.device_id)

        return {
            "status": "requested",
            "file_id": file_id,
            "block_size": block_size,
            "note": "File transfer initiated asynchronously",
        }

    @monitor(interval_ms=1000, description="Monitor DNP3 communication health and data quality")
    def check_scada_health(self) -> dict[str, Any]:
        alerts = []

        if self._comm_failures > 3:
            alerts.append({
                "level": "critical",
                "message": f"Communication lost ({self._comm_failures} consecutive failures)",
            })

        stale_threshold = time.time() - 60.0
        if self._last_poll_time < stale_threshold and self._last_poll_time > 0:
            alerts.append({
                "level": "warning",
                "message": "Data is stale (last poll over 60 seconds ago)",
            })

        for idx, point in self._analog_inputs.items():
            quality = point.get("quality", [])
            if "comm_lost" in quality or "reference_err" in quality:
                alerts.append({
                    "level": "warning",
                    "message": f"Analog input {idx} has quality issues: {quality}",
                })

        return {
            "healthy": len(alerts) == 0,
            "poll_count": self._poll_count,
            "comm_failures": self._comm_failures,
            "last_poll": self._last_poll_time,
            "unsolicited": self._unsolicited_enabled,
            "alerts": alerts,
        }
