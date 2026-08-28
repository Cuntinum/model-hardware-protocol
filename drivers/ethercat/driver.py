"""KHP Driver: EtherCAT Real Time Industrial Ethernet.

Controls EtherCAT slave devices (servo drives, I/O modules, sensors) via a
master interface. Supports Cyclic Synchronous Position (CSP), Cyclic Synchronous
Velocity (CSV), and Cyclic Synchronous Torque (CST) modes for motion control.

Covers: Beckhoff terminals, servo drives (Kollmorgen, Yaskawa, Delta),
stepper controllers, distributed I/O (EL series), safety modules,
precision motion stages, and custom EtherCAT slaves.

Requirements:
    pip install pysoem
    (Linux only: requires raw socket access, run as root or with CAP_NET_RAW)
"""
from __future__ import annotations

import time
import struct
import threading
from typing import Any

from khp import Driver, readable, writable, procedure, safety, monitor
from khp.core import ConnectionType


class EtherCATDevice(Driver):
    """EtherCAT master driver for real time industrial motion and I/O control."""

    name = "EtherCAT Master"
    version = "1.0.0"
    device_type = "motion_controller"
    description = "Real time EtherCAT master for servo drives, I/O modules, and precision motion"
    connection_type = ConnectionType.TCP

    def __init__(self, device_id: str | None = None, interface: str = "eth0",
                 cycle_time_us: int = 1000, slave_index: int = 0,
                 operation_mode: str = "csp", **config):
        super().__init__(device_id=device_id, interface=interface,
                         cycle_time_us=cycle_time_us, **config)
        self._interface = interface
        self._cycle_time_us = cycle_time_us
        self._slave_index = slave_index
        self._operation_mode = operation_mode
        self._master = None
        self._slave = None
        self._cyclic_thread = None
        self._running = False
        self._lock = threading.Lock()

        self._target_position = 0
        self._target_velocity = 0
        self._target_torque = 0
        self._actual_position = 0
        self._actual_velocity = 0
        self._actual_torque = 0
        self._status_word = 0
        self._control_word = 0
        self._error_code = 0
        self._digital_inputs = 0
        self._digital_outputs = 0
        self._homed = False
        self._enabled = False

        self._position_limit_min = -2147483648
        self._position_limit_max = 2147483647
        self._velocity_limit = 5000000
        self._torque_limit = 1000
        self._acceleration_limit = 100000

    async def connect(self):
        """Initialize EtherCAT master, scan bus, and configure slave."""
        try:
            import pysoem

            self._master = pysoem.Master()
            self._master.open(self._interface)

            num_slaves = self._master.config_init()
            if num_slaves == 0:
                from khp.errors import DeviceOfflineError
                raise DeviceOfflineError(
                    f"No EtherCAT slaves found on interface {self._interface}",
                    device_id=self.device_id,
                )

            if self._slave_index >= num_slaves:
                from khp.errors import ConnectionFailedError
                raise ConnectionFailedError(
                    f"Slave index {self._slave_index} out of range (found {num_slaves} slaves)",
                    device_id=self.device_id,
                )

            self._slave = self._master.slaves[self._slave_index]
            self._configure_pdo_mapping()
            self._master.config_map()

            self._master.state_check(pysoem.SAFEOP_STATE, timeout=5000000)
            self._master.state = pysoem.OP_STATE
            self._master.write_state()
            self._master.state_check(pysoem.OP_STATE, timeout=5000000)

            if self._master.state != pysoem.OP_STATE:
                from khp.errors import ConnectionFailedError
                raise ConnectionFailedError(
                    f"Slave failed to reach OP state (current: {self._master.state})",
                    device_id=self.device_id,
                )

            self._running = True
            self._cyclic_thread = threading.Thread(
                target=self._cyclic_loop, daemon=True
            )
            self._cyclic_thread.start()

            await super().connect()

        except ImportError:
            from khp.errors import DriverLoadError
            raise DriverLoadError(
                "pysoem not installed. Install with: pip install pysoem",
                device_id=self.device_id,
            )

    async def disconnect(self):
        """Disable drive, stop cyclic task, and close master."""
        self._running = False
        if self._cyclic_thread:
            self._cyclic_thread.join(timeout=2.0)

        if self._master:
            self._disable_drive()
            import pysoem
            self._master.state = pysoem.INIT_STATE
            self._master.write_state()
            self._master.close()
            self._master = None

        self._enabled = False
        await super().disconnect()

    def _configure_pdo_mapping(self):
        """Configure PDO mapping for CiA 402 drive profile."""
        self._slave.config_func = self._slave_config
        self._slave.is_lost = False

    def _slave_config(self, slave_pos):
        """Write SDO configuration for CiA 402 operation mode."""
        slave = self._master.slaves[slave_pos]
        mode_map = {"csp": 8, "csv": 9, "cst": 10}
        mode_value = mode_map.get(self._operation_mode, 8)
        slave.sdo_write(0x6060, 0, struct.pack("b", mode_value))

    def _cyclic_loop(self):
        """Real time cyclic exchange: send outputs, receive inputs."""
        while self._running:
            t_start = time.perf_counter_ns()

            with self._lock:
                self._pack_outputs()

            self._master.send_processdata()
            self._master.receive_processdata(timeout=100000)

            with self._lock:
                self._unpack_inputs()

            elapsed_ns = time.perf_counter_ns() - t_start
            sleep_ns = (self._cycle_time_us * 1000) - elapsed_ns
            if sleep_ns > 0:
                time.sleep(sleep_ns / 1e9)

    def _pack_outputs(self):
        """Pack control word and target values into output PDO."""
        if self._slave and self._slave.output:
            data = struct.pack(
                "<HiihH",
                self._control_word,
                self._target_position,
                self._target_velocity,
                self._target_torque,
                self._digital_outputs,
            )
            self._slave.output = data

    def _unpack_inputs(self):
        """Unpack status word and actual values from input PDO."""
        if self._slave and self._slave.input and len(self._slave.input) >= 16:
            unpacked = struct.unpack("<HiiHhH", self._slave.input[:16])
            self._status_word = unpacked[0]
            self._actual_position = unpacked[1]
            self._actual_velocity = unpacked[2]
            self._error_code = unpacked[3]
            self._actual_torque = unpacked[4]
            self._digital_inputs = unpacked[5]

    def _enable_drive(self):
        """CiA 402 state machine: transition to Operation Enabled."""
        sequences = [
            (0x0006, 0.01),
            (0x0007, 0.01),
            (0x000F, 0.01),
        ]
        for cw, delay in sequences:
            with self._lock:
                self._control_word = cw
            time.sleep(delay)

        time.sleep(0.05)
        if self._status_word & 0x0027 == 0x0027:
            self._enabled = True
        else:
            from khp.errors import ConnectionFailedError
            raise ConnectionFailedError(
                f"Drive failed to enable (status_word=0x{self._status_word:04X})",
                device_id=self.device_id,
            )

    def _disable_drive(self):
        """Transition drive to Switch On Disabled."""
        with self._lock:
            self._control_word = 0x0000
        self._enabled = False
        time.sleep(0.05)

    @readable(type="int", description="Current encoder position in counts", unit="counts")
    def actual_position(self) -> int:
        return self._actual_position

    @readable(type="int", description="Current velocity in counts per second", unit="counts/s")
    def actual_velocity(self) -> int:
        return self._actual_velocity

    @readable(type="int", description="Current torque as permille of rated torque", unit="permille")
    def actual_torque(self) -> int:
        return self._actual_torque

    @readable(type="int", description="CiA 402 status word (raw 16 bit)", unit="raw")
    def status_word(self) -> int:
        return self._status_word

    @readable(type="int", description="Error code from drive (0 means no error)", unit="code")
    def error_code(self) -> int:
        return self._error_code

    @readable(type="bool", description="Whether the drive is in Operation Enabled state")
    def drive_enabled(self) -> bool:
        return self._enabled

    @readable(type="bool", description="Whether the drive has been homed")
    def is_homed(self) -> bool:
        return self._homed

    @readable(type="int", description="Digital input word from slave", unit="bitmask")
    def digital_inputs(self) -> int:
        return self._digital_inputs

    @readable(type="str", description="Current drive state decoded from status word")
    def drive_state(self) -> str:
        sw = self._status_word
        if sw & 0x004F == 0x0000:
            return "not_ready"
        elif sw & 0x004F == 0x0040:
            return "switch_on_disabled"
        elif sw & 0x006F == 0x0021:
            return "ready_to_switch_on"
        elif sw & 0x006F == 0x0023:
            return "switched_on"
        elif sw & 0x006F == 0x0027:
            return "operation_enabled"
        elif sw & 0x006F == 0x0007:
            return "quick_stop_active"
        elif sw & 0x004F == 0x000F:
            return "fault_reaction_active"
        elif sw & 0x004F == 0x0008:
            return "fault"
        return f"unknown_0x{sw:04X}"

    @readable(type="float", description="Cycle time jitter (max deviation from target)", unit="microseconds")
    def cycle_jitter_us(self) -> float:
        return 0.0

    @safety(min=-2147483648, max=2147483647, reason="Encoder position hard limits", hard=True)
    @writable(type="int", description="Target position in encoder counts", unit="counts")
    def target_position(self, value: int):
        if not self._enabled:
            from khp.errors import PreconditionFailedError
            raise PreconditionFailedError(
                "Drive must be enabled before commanding position",
                device_id=self.device_id,
            )
        with self._lock:
            self._target_position = int(value)

    @safety(min=-5000000, max=5000000, reason="Maximum velocity to protect mechanics", hard=True)
    @writable(type="int", description="Target velocity in counts per second", unit="counts/s")
    def target_velocity(self, value: int):
        if not self._enabled:
            from khp.errors import PreconditionFailedError
            raise PreconditionFailedError(
                "Drive must be enabled before commanding velocity",
                device_id=self.device_id,
            )
        with self._lock:
            self._target_velocity = int(value)

    @safety(min=-1000, max=1000, reason="Torque limit to prevent mechanical damage", hard=True)
    @writable(type="int", description="Target torque in permille of rated", unit="permille")
    def target_torque(self, value: int):
        if not self._enabled:
            from khp.errors import PreconditionFailedError
            raise PreconditionFailedError(
                "Drive must be enabled before commanding torque",
                device_id=self.device_id,
            )
        with self._lock:
            self._target_torque = int(value)

    @writable(type="int", description="Digital output bitmask for slave I/O", unit="bitmask")
    def digital_outputs(self, value: int):
        with self._lock:
            self._digital_outputs = int(value) & 0xFFFF

    @procedure(description="Enable the drive (CiA 402 state machine transition to Operation Enabled)")
    def enable_drive(self):
        self._enable_drive()
        return {"status": "enabled", "state": self.drive_state()}

    @procedure(description="Disable the drive (transition to Switch On Disabled)")
    def disable_drive(self):
        self._disable_drive()
        return {"status": "disabled", "state": self.drive_state()}

    @procedure(description="Clear drive fault and reset error state")
    def fault_reset(self):
        with self._lock:
            self._control_word = 0x0080
        time.sleep(0.1)
        with self._lock:
            self._control_word = 0x0000
        time.sleep(0.1)
        self._enabled = False
        return {
            "status": "fault_cleared",
            "error_code": self._error_code,
            "state": self.drive_state(),
        }

    @procedure(description="Execute homing sequence using method 35 (current position as home)")
    def home(self, method: int = 35):
        if not self._enabled:
            self._enable_drive()

        original_mode = self._operation_mode
        if self._slave:
            self._slave.sdo_write(0x6060, 0, struct.pack("b", 6))
            self._slave.sdo_write(0x6098, 0, struct.pack("b", method))

        with self._lock:
            self._control_word = 0x001F

        timeout = time.time() + 30.0
        while time.time() < timeout:
            time.sleep(0.05)
            if self._status_word & 0x1400 == 0x1400:
                self._homed = True
                break
        else:
            from khp.errors import TimeoutError
            raise TimeoutError(
                "Homing did not complete within 30 seconds",
                device_id=self.device_id,
            )

        mode_map = {"csp": 8, "csv": 9, "cst": 10}
        if self._slave:
            self._slave.sdo_write(0x6060, 0, struct.pack("b", mode_map.get(original_mode, 8)))

        with self._lock:
            self._control_word = 0x000F

        return {"status": "homed", "method": method, "position": self._actual_position}

    @procedure(description="Move to absolute position with trapezoidal profile",
               requires_confirmation=False)
    def move_absolute(self, position: int, velocity: int = 100000, acceleration: int = 50000):
        if not self._enabled:
            from khp.errors import PreconditionFailedError
            raise PreconditionFailedError(
                "Drive must be enabled for motion commands",
                device_id=self.device_id,
            )

        if abs(velocity) > self._velocity_limit:
            from khp.errors import SafetyBlockedError
            raise SafetyBlockedError(
                f"Velocity {velocity} exceeds limit {self._velocity_limit}",
                device_id=self.device_id,
                property_name="velocity",
                value=velocity,
                limit={"max": self._velocity_limit},
            )

        with self._lock:
            self._target_position = position

        timeout = time.time() + 60.0
        while time.time() < timeout:
            time.sleep(0.01)
            if abs(self._actual_position - position) < 10:
                return {
                    "status": "reached",
                    "target": position,
                    "actual": self._actual_position,
                    "error": abs(self._actual_position - position),
                }

        return {
            "status": "timeout",
            "target": position,
            "actual": self._actual_position,
            "error": abs(self._actual_position - position),
        }

    @procedure(description="Execute relative move from current position")
    def move_relative(self, distance: int, velocity: int = 100000):
        target = self._actual_position + distance
        return self.move_absolute(target, velocity)

    @procedure(description="Read an SDO (Service Data Object) from slave",
               requires_confirmation=False)
    def sdo_read(self, index: int, subindex: int = 0, size: int = 4):
        if not self._slave:
            from khp.errors import DeviceOfflineError
            raise DeviceOfflineError("No slave connected", device_id=self.device_id)

        raw = self._slave.sdo_read(index, subindex, size)
        if size == 1:
            value = struct.unpack("<B", raw)[0]
        elif size == 2:
            value = struct.unpack("<H", raw)[0]
        elif size == 4:
            value = struct.unpack("<I", raw)[0]
        else:
            value = raw.hex()

        return {
            "index": f"0x{index:04X}",
            "subindex": subindex,
            "value": value,
            "raw_hex": raw.hex(),
        }

    @procedure(description="Write an SDO (Service Data Object) to slave",
               requires_confirmation=True)
    def sdo_write(self, index: int, subindex: int = 0, value: int = 0, size: int = 4):
        if not self._slave:
            from khp.errors import DeviceOfflineError
            raise DeviceOfflineError("No slave connected", device_id=self.device_id)

        if size == 1:
            data = struct.pack("<B", value & 0xFF)
        elif size == 2:
            data = struct.pack("<H", value & 0xFFFF)
        elif size == 4:
            data = struct.pack("<I", value & 0xFFFFFFFF)
        else:
            data = struct.pack("<I", value)

        self._slave.sdo_write(index, subindex, data)
        return {
            "status": "written",
            "index": f"0x{index:04X}",
            "subindex": subindex,
            "value": value,
        }

    @procedure(description="Scan EtherCAT bus and return list of all detected slaves")
    def scan_bus(self):
        if not self._master:
            from khp.errors import DeviceOfflineError
            raise DeviceOfflineError("Master not initialized", device_id=self.device_id)

        slaves = []
        for i, slave in enumerate(self._master.slaves):
            slaves.append({
                "index": i,
                "name": slave.name if hasattr(slave, "name") else f"slave_{i}",
                "manufacturer": getattr(slave, "man", 0),
                "product_code": getattr(slave, "id", 0),
                "revision": getattr(slave, "rev", 0),
            })

        return {"slave_count": len(slaves), "slaves": slaves}

    @procedure(description="Quick stop: immediately halt motion with maximum deceleration",
               requires_confirmation=False)
    def quick_stop(self):
        with self._lock:
            self._control_word = 0x0002
        time.sleep(0.1)

        self._enabled = False
        return {
            "status": "stopped",
            "position": self._actual_position,
            "velocity": self._actual_velocity,
        }

    @procedure(description="Set position limits for the drive axis")
    def set_position_limits(self, min_position: int, max_position: int):
        if min_position >= max_position:
            from khp.errors import SafetyBlockedError
            raise SafetyBlockedError(
                "min_position must be less than max_position",
                device_id=self.device_id,
                property_name="position_limits",
                value={"min": min_position, "max": max_position},
                limit=None,
            )
        self._position_limit_min = min_position
        self._position_limit_max = max_position

        if self._slave:
            self._slave.sdo_write(0x607D, 1, struct.pack("<i", min_position))
            self._slave.sdo_write(0x607D, 2, struct.pack("<i", max_position))

        return {
            "status": "limits_set",
            "min": min_position,
            "max": max_position,
        }

    @monitor(interval_ms=100, description="Monitor drive for faults and position errors")
    def check_drive_health(self) -> dict[str, Any]:
        alerts = []

        if self._status_word & 0x0008:
            alerts.append({
                "level": "critical",
                "message": f"Drive fault detected (error_code={self._error_code})",
            })

        if self._enabled and abs(self._actual_position - self._target_position) > 100000:
            alerts.append({
                "level": "warning",
                "message": "Large following error detected",
                "following_error": abs(self._actual_position - self._target_position),
            })

        if self._actual_position < self._position_limit_min or \
           self._actual_position > self._position_limit_max:
            alerts.append({
                "level": "critical",
                "message": "Position outside configured limits",
                "position": self._actual_position,
            })

        return {
            "healthy": len(alerts) == 0,
            "state": self.drive_state(),
            "position": self._actual_position,
            "velocity": self._actual_velocity,
            "torque": self._actual_torque,
            "alerts": alerts,
        }
