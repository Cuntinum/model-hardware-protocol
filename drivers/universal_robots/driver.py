"""KHP Driver: Universal Robots Collaborative Robot Arm.

Controls UR3/UR5/UR10/UR16/UR20/UR30 series cobots via the Real Time Data
Exchange (RTDE) protocol on port 30004. Provides full 6 DOF joint control,
Cartesian positioning, I/O access, force/torque sensing, and program management.

The RTDE protocol allows 500Hz data exchange for joint positions, velocities,
currents, TCP pose, force/torque, safety status, and digital/analog I/O,
while simultaneously sending motion commands via the primary interface.

Requirements:
    pip install ur-rtde
    (UR controller firmware >= 3.3 for RTDE, >= 5.0 recommended)
"""
from __future__ import annotations

import struct
import socket
import time
import threading
import math
from typing import Any

from khp import Driver, readable, writable, procedure, safety, monitor
from khp.core import ConnectionType


JOINT_COUNT = 6
RTDE_PORT = 30004
DASHBOARD_PORT = 29999
PRIMARY_PORT = 30001

ROBOT_MODE_RUNNING = 7
SAFETY_MODE_NORMAL = 1
SAFETY_MODE_REDUCED = 2
SAFETY_MODE_PROTECTIVE_STOP = 3
SAFETY_MODE_RECOVERY = 4
SAFETY_MODE_SAFEGUARD_STOP = 5
SAFETY_MODE_SYSTEM_EMERGENCY = 6
SAFETY_MODE_ROBOT_EMERGENCY = 7
SAFETY_MODE_VIOLATION = 8
SAFETY_MODE_FAULT = 9


class UniversalRobotDevice(Driver):
    """Universal Robots cobot driver via RTDE and Dashboard Server protocols."""

    name = "Universal Robots Cobot"
    version = "1.0.0"
    device_type = "robot_arm"
    description = "UR3/UR5/UR10/UR16/UR20/UR30 cobot control via RTDE (500Hz realtime)"
    connection_type = ConnectionType.TCP

    def __init__(self, device_id: str | None = None, host: str = "192.168.1.2",
                 rtde_frequency: float = 125.0, max_joint_speed: float = 1.05,
                 max_joint_accel: float = 1.4, max_linear_speed: float = 0.25,
                 max_linear_accel: float = 1.2, payload_kg: float = 0.0,
                 payload_cog: tuple = (0.0, 0.0, 0.0), **config):
        super().__init__(device_id=device_id, host=host, **config)
        self._host = host
        self._rtde_frequency = rtde_frequency
        self._max_joint_speed = max_joint_speed
        self._max_joint_accel = max_joint_accel
        self._max_linear_speed = max_linear_speed
        self._max_linear_accel = max_linear_accel
        self._payload_kg = payload_kg
        self._payload_cog = payload_cog

        self._rtde_conn = None
        self._rtde_recv = None
        self._dashboard_socket = None
        self._lock = threading.Lock()
        self._monitoring = False

        self._joint_positions = [0.0] * JOINT_COUNT
        self._joint_velocities = [0.0] * JOINT_COUNT
        self._joint_temperatures = [0.0] * JOINT_COUNT
        self._joint_currents = [0.0] * JOINT_COUNT
        self._tcp_pose = [0.0] * 6
        self._tcp_force = [0.0] * 6
        self._tcp_speed = [0.0] * 6
        self._digital_inputs = 0
        self._digital_outputs = 0
        self._analog_inputs = [0.0, 0.0]
        self._analog_outputs = [0.0, 0.0]
        self._robot_mode = 0
        self._safety_mode = 0
        self._program_running = False
        self._speed_scaling = 1.0
        self._robot_voltage = 0.0
        self._robot_current = 0.0
        self._model_name = ""
        self._serial_number = ""
        self._software_version = ""

    async def connect(self):
        """Connect to UR robot via RTDE and Dashboard Server."""
        try:
            import rtde_control
            import rtde_receive

            self._rtde_conn = rtde_control.RTDEControlInterface(self._host)
            self._rtde_recv = rtde_receive.RTDEReceiveInterface(
                self._host, self._rtde_frequency
            )

            self._dashboard_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._dashboard_socket.settimeout(5.0)
            self._dashboard_socket.connect((self._host, DASHBOARD_PORT))
            welcome = self._dashboard_socket.recv(1024).decode("utf-8", errors="replace")

            self._model_name = self._dashboard_command("get robot model")
            self._serial_number = self._dashboard_command("get serial number")
            self._software_version = self._dashboard_command("PolyscopeVersion")

            self.name = f"UR Robot: {self._model_name} ({self._serial_number})"
            self._update_state()

            if self._payload_kg > 0:
                self._rtde_conn.setPayload(
                    self._payload_kg, list(self._payload_cog)
                )

            await super().connect()

        except ImportError:
            from khp.errors import DriverLoadError
            raise DriverLoadError(
                "ur_rtde not installed. Install with: pip install ur-rtde",
                device_id=self.device_id,
            )
        except (socket.error, ConnectionRefusedError) as e:
            from khp.errors import DeviceOfflineError
            raise DeviceOfflineError(
                f"Cannot connect to UR robot at {self._host}: {e}",
                device_id=self.device_id,
            )

    async def disconnect(self):
        """Safely stop motion and disconnect."""
        if self._rtde_conn:
            try:
                self._rtde_conn.stopScript()
            except Exception:
                pass
            self._rtde_conn.disconnect()
            self._rtde_conn = None
        if self._rtde_recv:
            self._rtde_recv.disconnect()
            self._rtde_recv = None
        if self._dashboard_socket:
            try:
                self._dashboard_socket.close()
            except Exception:
                pass
            self._dashboard_socket = None
        await super().disconnect()

    async def emergency_stop(self):
        """Trigger protective stop on the robot."""
        if self._rtde_conn:
            self._rtde_conn.triggerProtectiveStop()
        self._dashboard_command("stop")

    def _ensure_connected(self):
        if not self._rtde_conn or not self._rtde_recv:
            from khp.errors import DeviceOfflineError
            raise DeviceOfflineError(
                "Not connected to UR robot", device_id=self.device_id
            )

    def _dashboard_command(self, cmd: str) -> str:
        """Send command to dashboard server and return response."""
        if not self._dashboard_socket:
            return ""
        try:
            self._dashboard_socket.send(f"{cmd}\n".encode())
            response = self._dashboard_socket.recv(2048).decode("utf-8", errors="replace")
            return response.strip()
        except (socket.timeout, OSError):
            return ""

    def _update_state(self):
        """Read current robot state from RTDE."""
        self._ensure_connected()
        self._joint_positions = list(self._rtde_recv.getActualQ())
        self._joint_velocities = list(self._rtde_recv.getActualQd())
        self._joint_currents = list(self._rtde_recv.getActualCurrent())
        self._joint_temperatures = list(self._rtde_recv.getJointTemperatures())
        self._tcp_pose = list(self._rtde_recv.getActualTCPPose())
        self._tcp_force = list(self._rtde_recv.getActualTCPForce())
        self._tcp_speed = list(self._rtde_recv.getActualTCPSpeed())
        self._digital_inputs = self._rtde_recv.getActualDigitalInputBits()
        self._digital_outputs = self._rtde_recv.getActualDigitalOutputBits()
        self._analog_inputs = [
            self._rtde_recv.getStandardAnalogInput0(),
            self._rtde_recv.getStandardAnalogInput1(),
        ]
        self._analog_outputs = [
            self._rtde_recv.getStandardAnalogOutput0(),
            self._rtde_recv.getStandardAnalogOutput1(),
        ]
        self._robot_mode = self._rtde_recv.getRobotMode()
        self._safety_mode = self._rtde_recv.getSafetyMode()
        self._speed_scaling = self._rtde_recv.getSpeedScaling()
        self._robot_voltage = self._rtde_recv.getRobotVoltage48V()
        self._robot_current = self._rtde_recv.getRobotCurrent()

    @readable(type="dict", description="Robot identification (model, serial, software version)")
    def robot_identity(self) -> dict:
        return {
            "model": self._model_name,
            "serial_number": self._serial_number,
            "software_version": self._software_version,
            "host": self._host,
        }

    @readable(type="list", description="Joint positions in radians [base, shoulder, elbow, w1, w2, w3]",
              unit="radians")
    def joint_positions(self) -> list[float]:
        self._update_state()
        return self._joint_positions

    @readable(type="list", description="Joint positions in degrees", unit="degrees")
    def joint_positions_degrees(self) -> list[float]:
        self._update_state()
        return [math.degrees(q) for q in self._joint_positions]

    @readable(type="list", description="Joint velocities in rad/s", unit="rad/s")
    def joint_velocities(self) -> list[float]:
        self._update_state()
        return self._joint_velocities

    @readable(type="list", description="Joint temperatures in Celsius", unit="celsius")
    def joint_temperatures(self) -> list[float]:
        self._update_state()
        return self._joint_temperatures

    @readable(type="list", description="Joint motor currents in amperes", unit="amperes")
    def joint_currents(self) -> list[float]:
        self._update_state()
        return self._joint_currents

    @readable(type="dict", description="TCP pose [x, y, z, rx, ry, rz] in meters and axis angle")
    def tcp_pose(self) -> dict:
        self._update_state()
        return {
            "x": self._tcp_pose[0],
            "y": self._tcp_pose[1],
            "z": self._tcp_pose[2],
            "rx": self._tcp_pose[3],
            "ry": self._tcp_pose[4],
            "rz": self._tcp_pose[5],
            "unit_position": "meters",
            "unit_rotation": "axis_angle_radians",
        }

    @readable(type="list", description="TCP force/torque [Fx, Fy, Fz, Tx, Ty, Tz]",
              unit="N and Nm")
    def tcp_force(self) -> list[float]:
        self._update_state()
        return self._tcp_force

    @readable(type="dict", description="Robot operating mode and safety status")
    def robot_status(self) -> dict:
        self._update_state()
        safety_names = {
            1: "normal", 2: "reduced", 3: "protective_stop",
            4: "recovery", 5: "safeguard_stop", 6: "system_emergency",
            7: "robot_emergency", 8: "violation", 9: "fault",
        }
        return {
            "robot_mode": self._robot_mode,
            "robot_mode_running": self._robot_mode == ROBOT_MODE_RUNNING,
            "safety_mode": self._safety_mode,
            "safety_mode_name": safety_names.get(self._safety_mode, "unknown"),
            "speed_scaling": self._speed_scaling,
            "voltage_48v": self._robot_voltage,
            "current_a": self._robot_current,
            "program_running": self._program_running,
        }

    @readable(type="dict", description="Digital I/O state (18 inputs, 18 outputs)")
    def digital_io(self) -> dict:
        self._update_state()
        inputs = {}
        outputs = {}
        for i in range(18):
            inputs[f"DI{i}"] = bool(self._digital_inputs & (1 << i))
            outputs[f"DO{i}"] = bool(self._digital_outputs & (1 << i))
        return {"inputs": inputs, "outputs": outputs}

    @readable(type="dict", description="Analog I/O values (2 inputs, 2 outputs)", unit="volts or mA")
    def analog_io(self) -> dict:
        self._update_state()
        return {
            "analog_in_0": self._analog_inputs[0],
            "analog_in_1": self._analog_inputs[1],
            "analog_out_0": self._analog_outputs[0],
            "analog_out_1": self._analog_outputs[1],
        }

    @writable(type="bool", description="Set digital output pin high or low")
    def set_digital_output(self, config: dict):
        """Config: {pin: int, value: bool}."""
        self._ensure_connected()
        pin = int(config.get("pin", 0))
        value = bool(config.get("value", False))
        if pin < 0 or pin > 17:
            from khp.errors import PropertyNotFoundError
            raise PropertyNotFoundError(f"Digital output pin {pin} out of range (0 to 17)", self.device_id)
        self._rtde_conn.setStandardDigitalOut(pin, value)

    @writable(type="float", description="Set analog output voltage (0.0 to 10.0V)")
    def set_analog_output(self, config: dict):
        """Config: {channel: int (0 or 1), voltage: float}."""
        self._ensure_connected()
        channel = int(config.get("channel", 0))
        voltage = float(config.get("voltage", 0.0))
        voltage = max(0.0, min(10.0, voltage))
        if channel == 0:
            self._rtde_conn.setStandardAnalogOut(0, voltage)
        elif channel == 1:
            self._rtde_conn.setStandardAnalogOut(1, voltage)

    @writable(type="float", description="Set global speed scaling (0.0 to 1.0)")
    def speed_scaling(self, value: float):
        value = max(0.0, min(1.0, float(value)))
        self._rtde_conn.setSpeedSlider(value)

    @safety(
        limit_type="hard",
        description="Joint position limits in radians (UR standard +/_ 2*pi)"
    )
    def joint_limits(self) -> dict:
        return {
            "min_position_rad": [-2 * math.pi] * JOINT_COUNT,
            "max_position_rad": [2 * math.pi] * JOINT_COUNT,
            "max_speed_rad_s": self._max_joint_speed,
            "max_accel_rad_s2": self._max_joint_accel,
        }

    @safety(
        limit_type="hard",
        description="TCP speed and force limits for collaborative operation"
    )
    def tcp_limits(self) -> dict:
        return {
            "max_linear_speed_m_s": self._max_linear_speed,
            "max_linear_accel_m_s2": self._max_linear_accel,
            "max_force_n": 150.0,
            "max_torque_nm": 28.0,
            "max_payload_kg": 30.0,
        }

    @procedure(description="Move joints to target positions (radians) with speed and acceleration")
    def move_joints(self, positions: list[float] | None = None, speed: float = 0.5,
                    acceleration: float = 0.8, asynchronous: bool = False):
        """Move to joint positions [q0...q5] in radians."""
        self._ensure_connected()
        if positions is None or len(positions) != JOINT_COUNT:
            return {"error": f"Exactly {JOINT_COUNT} joint positions required"}

        speed = min(speed, self._max_joint_speed)
        acceleration = min(acceleration, self._max_joint_accel)

        self._rtde_conn.moveJ(positions, speed, acceleration, asynchronous)
        if not asynchronous:
            self._update_state()
        return {
            "status": "moving" if asynchronous else "complete",
            "target_rad": positions,
            "target_deg": [math.degrees(q) for q in positions],
            "speed_rad_s": speed,
            "acceleration_rad_s2": acceleration,
        }

    @procedure(description="Move TCP to Cartesian position [x, y, z, rx, ry, rz] in meters/radians")
    def move_linear(self, pose: list[float] | None = None, speed: float = 0.1,
                    acceleration: float = 0.5, asynchronous: bool = False):
        """Linear move to [x, y, z, rx, ry, rz]."""
        self._ensure_connected()
        if pose is None or len(pose) != 6:
            return {"error": "Exactly 6 pose values required [x, y, z, rx, ry, rz]"}

        speed = min(speed, self._max_linear_speed)
        acceleration = min(acceleration, self._max_linear_accel)

        self._rtde_conn.moveL(pose, speed, acceleration, asynchronous)
        if not asynchronous:
            self._update_state()
        return {
            "status": "moving" if asynchronous else "complete",
            "target_pose": pose,
            "speed_m_s": speed,
            "acceleration_m_s2": acceleration,
        }

    @procedure(description="Move TCP along a circular arc defined by via point and end point")
    def move_circular(self, via_pose: list[float] | None = None,
                      end_pose: list[float] | None = None,
                      speed: float = 0.1, acceleration: float = 0.5,
                      mode: int = 0):
        """Circular move through via_pose to end_pose."""
        self._ensure_connected()
        if not via_pose or len(via_pose) != 6:
            return {"error": "via_pose must be [x, y, z, rx, ry, rz]"}
        if not end_pose or len(end_pose) != 6:
            return {"error": "end_pose must be [x, y, z, rx, ry, rz]"}

        speed = min(speed, self._max_linear_speed)
        self._rtde_conn.moveC(via_pose, end_pose, speed, acceleration, mode)
        self._update_state()
        return {"status": "complete", "via": via_pose, "end": end_pose}

    @procedure(description="Execute a waypoint path (series of joint moves with blending)")
    def execute_path(self, waypoints: list[list[float]] | None = None,
                     speed: float = 0.5, acceleration: float = 0.8,
                     blend_radius: float = 0.01):
        """Execute path: list of [q0...q5] joint positions."""
        self._ensure_connected()
        if not waypoints or len(waypoints) < 2:
            return {"error": "At least 2 waypoints required"}

        path = []
        for wp in waypoints:
            if len(wp) != JOINT_COUNT:
                return {"error": f"Each waypoint must have {JOINT_COUNT} values"}
            path.append(wp + [speed, acceleration, blend_radius])

        self._rtde_conn.moveJ(path[0][:6], speed, acceleration, False)
        for wp_full in path[1:]:
            self._rtde_conn.moveJ(wp_full[:6], speed, acceleration, False)

        self._update_state()
        return {
            "status": "complete",
            "waypoints_executed": len(waypoints),
            "final_position_rad": self._joint_positions,
        }

    @procedure(description="Apply force mode (compliant motion along specified axes)")
    def force_mode(self, task_frame: list[float] | None = None,
                   selection_vector: list[int] | None = None,
                   wrench: list[float] | None = None,
                   force_type: int = 2, limits: list[float] | None = None,
                   duration: float = 5.0):
        """Enable force/torque control. Selection: 1=force controlled, 0=position controlled."""
        self._ensure_connected()
        if task_frame is None:
            task_frame = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        if selection_vector is None:
            selection_vector = [0, 0, 1, 0, 0, 0]
        if wrench is None:
            wrench = [0.0, 0.0, -10.0, 0.0, 0.0, 0.0]
        if limits is None:
            limits = [2.0, 2.0, 1.5, 1.0, 1.0, 1.0]

        self._rtde_conn.forceMode(
            task_frame, selection_vector, wrench, force_type, limits
        )
        time.sleep(duration)
        self._rtde_conn.forceModeStop()
        self._update_state()
        return {
            "status": "complete",
            "duration_s": duration,
            "final_force": self._tcp_force,
        }

    @procedure(description="Stop all robot motion immediately (deceleration stop)")
    def stop_motion(self, deceleration: float = 2.0):
        self._ensure_connected()
        self._rtde_conn.stopJ(deceleration)
        return {"status": "stopped", "deceleration_rad_s2": deceleration}

    @procedure(description="Freedrive mode: allow manual guidance of the robot arm",
               requires_confirmation=True)
    def freedrive(self, duration: float = 30.0):
        """Enable freedrive for manual teaching."""
        self._ensure_connected()
        self._rtde_conn.teachMode()
        time.sleep(duration)
        self._rtde_conn.endTeachMode()
        self._update_state()
        return {
            "status": "freedrive_ended",
            "duration_s": duration,
            "final_position_rad": self._joint_positions,
            "final_position_deg": [math.degrees(q) for q in self._joint_positions],
        }

    @procedure(description="Power on and release brakes (from power off state)",
               requires_confirmation=True)
    def power_on(self):
        self._dashboard_command("power on")
        time.sleep(3.0)
        self._dashboard_command("brake release")
        time.sleep(2.0)
        self._update_state()
        return {
            "status": "powered_on",
            "robot_mode": self._robot_mode,
            "safety_mode": self._safety_mode,
        }

    @procedure(description="Power off the robot", requires_confirmation=True)
    def power_off(self):
        self._dashboard_command("power off")
        return {"status": "power_off_sent"}

    @procedure(description="Unlock protective stop and resume operation")
    def unlock_protective_stop(self):
        self._dashboard_command("unlock protective stop")
        time.sleep(1.0)
        self._update_state()
        return {
            "status": "unlocked",
            "safety_mode": self._safety_mode,
        }

    @procedure(description="Set TCP payload for accurate dynamics (mass and center of gravity)")
    def set_payload(self, mass_kg: float = 0.0,
                    center_of_gravity: list[float] | None = None):
        self._ensure_connected()
        if center_of_gravity is None:
            center_of_gravity = [0.0, 0.0, 0.0]
        self._payload_kg = mass_kg
        self._payload_cog = tuple(center_of_gravity)
        self._rtde_conn.setPayload(mass_kg, center_of_gravity)
        return {"mass_kg": mass_kg, "cog": center_of_gravity}

    @procedure(description="Set TCP offset (tool center point relative to flange)")
    def set_tcp(self, offset: list[float] | None = None):
        """Offset: [x, y, z, rx, ry, rz] in meters/radians from flange."""
        self._ensure_connected()
        if offset is None or len(offset) != 6:
            return {"error": "TCP offset must be [x, y, z, rx, ry, rz]"}
        self._rtde_conn.setTcp(offset)
        return {"status": "tcp_set", "offset": offset}

    @procedure(description="Get inverse kinematics solution for a target TCP pose")
    def inverse_kinematics(self, target_pose: list[float] | None = None,
                           near_position: list[float] | None = None):
        """Compute joint positions for target Cartesian pose."""
        self._ensure_connected()
        if target_pose is None or len(target_pose) != 6:
            return {"error": "target_pose must be [x, y, z, rx, ry, rz]"}
        if near_position is None:
            near_position = self._joint_positions
        result = self._rtde_conn.getInverseKinematics(target_pose, near_position)
        return {
            "joint_positions_rad": list(result),
            "joint_positions_deg": [math.degrees(q) for q in result],
            "target_pose": target_pose,
        }

    @procedure(description="Get forward kinematics (TCP pose from joint positions)")
    def forward_kinematics(self, joint_positions: list[float] | None = None):
        self._ensure_connected()
        if joint_positions is None:
            joint_positions = self._joint_positions
        result = self._rtde_conn.getForwardKinematics(joint_positions)
        return {
            "tcp_pose": list(result),
            "joint_positions_rad": joint_positions,
        }

    @monitor(interval_ms=500, description="Monitor robot safety status and joint temperatures")
    def check_robot_health(self) -> dict[str, Any]:
        alerts = []
        try:
            self._update_state()
        except Exception as e:
            return {"healthy": False, "alerts": [{"level": "critical", "message": str(e)}]}

        if self._safety_mode not in (SAFETY_MODE_NORMAL, SAFETY_MODE_REDUCED):
            alerts.append({
                "level": "critical",
                "message": f"Safety mode abnormal: {self._safety_mode}",
            })

        for i, temp in enumerate(self._joint_temperatures):
            if temp > 75.0:
                alerts.append({
                    "level": "warning",
                    "message": f"Joint {i} temperature high: {temp:.1f}C",
                })
            if temp > 85.0:
                alerts.append({
                    "level": "critical",
                    "message": f"Joint {i} overheating: {temp:.1f}C",
                })

        total_force = math.sqrt(sum(f * f for f in self._tcp_force[:3]))
        if total_force > 100.0:
            alerts.append({
                "level": "warning",
                "message": f"High TCP force: {total_force:.1f}N",
            })

        if self._robot_voltage < 44.0:
            alerts.append({
                "level": "warning",
                "message": f"Low bus voltage: {self._robot_voltage:.1f}V",
            })

        return {
            "healthy": len(alerts) == 0,
            "robot_mode": self._robot_mode,
            "safety_mode": self._safety_mode,
            "speed_scaling": self._speed_scaling,
            "max_joint_temp_c": max(self._joint_temperatures) if self._joint_temperatures else 0,
            "tcp_force_n": total_force,
            "voltage_48v": self._robot_voltage,
            "alerts": alerts,
        }
