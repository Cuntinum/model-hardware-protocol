"""KHP Driver: MAVLink Autonomous Vehicle (Drones, Rovers, Boats, Submarines).

Controls ArduPilot and PX4 vehicles via MAVLink v2 protocol. Provides
GPS position, attitude (roll/pitch/yaw), battery state, RC channels,
mission management, arm/disarm, takeoff/land, waypoint navigation,
and geofence enforcement.

Supports: quadcopters, hexacopters, fixed wing aircraft, ground rovers,
boats, submarines, and any MAVLink compatible autopilot.

Requirements:
    pip install pymavlink
    (serial or UDP connection to flight controller)
"""
from __future__ import annotations

import time
import math
import threading
from typing import Any

from khp import Driver, readable, writable, procedure, safety, monitor
from khp.core import ConnectionType


MAV_STATE_UNINIT = 0
MAV_STATE_BOOT = 1
MAV_STATE_CALIBRATING = 2
MAV_STATE_STANDBY = 3
MAV_STATE_ACTIVE = 4
MAV_STATE_CRITICAL = 5
MAV_STATE_EMERGENCY = 6

MAV_MODE_FLAG_ARMED = 128
MAV_CMD_COMPONENT_ARM_DISARM = 400
MAV_CMD_NAV_TAKEOFF = 22
MAV_CMD_NAV_LAND = 21
MAV_CMD_NAV_RETURN_TO_LAUNCH = 20
MAV_CMD_NAV_WAYPOINT = 16
MAV_CMD_DO_SET_MODE = 176
MAV_CMD_CONDITION_YAW = 115
MAV_CMD_DO_CHANGE_SPEED = 178
MAV_CMD_DO_SET_HOME = 179
MAV_CMD_DO_SET_SERVO = 183
MAV_CMD_DO_REPEAT_SERVO = 184
MAV_CMD_NAV_LOITER_UNLIM = 17
MAV_CMD_NAV_LOITER_TIME = 19

COPTER_MODE_STABILIZE = 0
COPTER_MODE_GUIDED = 4
COPTER_MODE_LOITER = 5
COPTER_MODE_RTL = 6
COPTER_MODE_LAND = 9
COPTER_MODE_AUTO = 3
COPTER_MODE_POSHOLD = 16


class MAVLinkDevice(Driver):
    """MAVLink vehicle driver for drones, rovers, and autonomous platforms."""

    name = "MAVLink Vehicle"
    version = "1.0.0"
    device_type = "autonomous_vehicle"
    description = "ArduPilot/PX4 vehicle control via MAVLink v2 (serial, UDP, TCP)"
    connection_type = ConnectionType.SERIAL

    def __init__(self, device_id: str | None = None,
                 connection_string: str = "udpin:0.0.0.0:14550",
                 baud: int = 57600, source_system: int = 255,
                 source_component: int = 0, target_system: int = 1,
                 target_component: int = 1, heartbeat_timeout: float = 5.0,
                 max_altitude_m: float = 120.0, geofence_radius_m: float = 500.0,
                 home_lat: float = 0.0, home_lon: float = 0.0, **config):
        super().__init__(device_id=device_id, connection_string=connection_string, **config)
        self._connection_string = connection_string
        self._baud = baud
        self._source_system = source_system
        self._source_component = source_component
        self._target_system = target_system
        self._target_component = target_component
        self._heartbeat_timeout = heartbeat_timeout
        self._max_altitude_m = max_altitude_m
        self._geofence_radius_m = geofence_radius_m
        self._home_lat = home_lat
        self._home_lon = home_lon

        self._mav = None
        self._lock = threading.Lock()
        self._heartbeat_thread = None
        self._running = False

        self._lat = 0.0
        self._lon = 0.0
        self._alt_msl = 0.0
        self._alt_rel = 0.0
        self._heading_deg = 0.0
        self._groundspeed = 0.0
        self._airspeed = 0.0
        self._climb_rate = 0.0
        self._roll = 0.0
        self._pitch = 0.0
        self._yaw = 0.0
        self._rollspeed = 0.0
        self._pitchspeed = 0.0
        self._yawspeed = 0.0
        self._battery_voltage = 0.0
        self._battery_current = 0.0
        self._battery_remaining = 100
        self._armed = False
        self._flight_mode = ""
        self._system_status = 0
        self._gps_fix_type = 0
        self._gps_satellites = 0
        self._gps_hdop = 99.99
        self._gps_vdop = 99.99
        self._rc_channels: list[int] = []
        self._servo_outputs: list[int] = []
        self._vehicle_type = ""
        self._autopilot_type = ""
        self._firmware_version = ""
        self._messages_received = 0
        self._last_heartbeat = 0.0

    async def connect(self):
        """Connect to vehicle via MAVLink."""
        try:
            from pymavlink import mavutil

            self._mav = mavutil.mavlink_connection(
                self._connection_string,
                baud=self._baud,
                source_system=self._source_system,
                source_component=self._source_component,
            )

            self._mav.wait_heartbeat(timeout=self._heartbeat_timeout)
            self._last_heartbeat = time.time()

            hb = self._mav.messages.get("HEARTBEAT")
            if hb:
                type_map = {
                    1: "fixed_wing", 2: "quadrotor", 3: "coaxial",
                    4: "helicopter", 10: "ground_rover", 11: "surface_boat",
                    12: "submarine", 13: "hexarotor", 14: "octorotor",
                }
                self._vehicle_type = type_map.get(hb.type, f"type_{hb.type}")
                ap_map = {3: "ArduPilot", 12: "PX4"}
                self._autopilot_type = ap_map.get(hb.autopilot, f"ap_{hb.autopilot}")
                self._armed = bool(hb.base_mode & MAV_MODE_FLAG_ARMED)
                self._system_status = hb.system_status

            self._mav.mav.request_data_stream_send(
                self._target_system, self._target_component,
                0, 10, 1
            )

            self._request_message_interval(33, 100000)
            self._request_message_interval(24, 200000)
            self._request_message_interval(30, 100000)
            self._request_message_interval(74, 500000)
            self._request_message_interval(147, 500000)

            self._running = True
            self._heartbeat_thread = threading.Thread(
                target=self._heartbeat_loop, daemon=True
            )
            self._heartbeat_thread.start()

            self.name = f"MAVLink: {self._vehicle_type} ({self._autopilot_type})"
            self._update_telemetry()
            await super().connect()

        except ImportError:
            from khp.errors import DriverLoadError
            raise DriverLoadError(
                "pymavlink not installed. Install with: pip install pymavlink",
                device_id=self.device_id,
            )
        except Exception as e:
            from khp.errors import DeviceOfflineError
            raise DeviceOfflineError(
                f"No heartbeat from vehicle on {self._connection_string}: {e}",
                device_id=self.device_id,
            )

    async def disconnect(self):
        """Close MAVLink connection."""
        self._running = False
        if self._heartbeat_thread:
            self._heartbeat_thread.join(timeout=3.0)
        if self._mav:
            self._mav.close()
            self._mav = None
        await super().disconnect()

    async def emergency_stop(self):
        """Command immediate motor kill (EMERGENCY only)."""
        if self._mav:
            self._mav.mav.command_long_send(
                self._target_system, self._target_component,
                MAV_CMD_COMPONENT_ARM_DISARM, 0,
                0, 21196, 0, 0, 0, 0, 0
            )

    def _ensure_connected(self):
        if not self._mav:
            from khp.errors import DeviceOfflineError
            raise DeviceOfflineError("Not connected to vehicle", device_id=self.device_id)

    def _heartbeat_loop(self):
        """Send heartbeat at 1Hz to maintain connection."""
        from pymavlink import mavutil
        while self._running:
            if self._mav:
                self._mav.mav.heartbeat_send(
                    6, 8, 0, 0, 0
                )
            time.sleep(1.0)

    def _request_message_interval(self, msg_id: int, interval_us: int):
        """Request specific message at interval."""
        self._mav.mav.command_long_send(
            self._target_system, self._target_component,
            511, 0,
            msg_id, interval_us, 0, 0, 0, 0, 0
        )

    def _wait_command_ack(self, command: int, timeout: float = 5.0) -> bool:
        """Wait for COMMAND_ACK for specified command."""
        start = time.time()
        while time.time() - start < timeout:
            msg = self._mav.recv_match(type="COMMAND_ACK", blocking=True, timeout=1.0)
            if msg and msg.command == command:
                return msg.result == 0
        return False

    def _update_telemetry(self):
        """Read latest telemetry messages."""
        self._ensure_connected()
        for _ in range(50):
            msg = self._mav.recv_match(blocking=False)
            if msg is None:
                break
            self._messages_received += 1
            msg_type = msg.get_type()

            if msg_type == "GLOBAL_POSITION_INT":
                self._lat = msg.lat / 1e7
                self._lon = msg.lon / 1e7
                self._alt_msl = msg.alt / 1000.0
                self._alt_rel = msg.relative_alt / 1000.0
                self._heading_deg = msg.hdg / 100.0
                self._climb_rate = msg.vz / 100.0
            elif msg_type == "ATTITUDE":
                self._roll = math.degrees(msg.roll)
                self._pitch = math.degrees(msg.pitch)
                self._yaw = math.degrees(msg.yaw)
                self._rollspeed = math.degrees(msg.rollspeed)
                self._pitchspeed = math.degrees(msg.pitchspeed)
                self._yawspeed = math.degrees(msg.yawspeed)
            elif msg_type == "VFR_HUD":
                self._airspeed = msg.airspeed
                self._groundspeed = msg.groundspeed
                self._heading_deg = msg.heading
                self._alt_msl = msg.alt
                self._climb_rate = msg.climb
            elif msg_type == "SYS_STATUS":
                self._battery_voltage = msg.voltage_battery / 1000.0
                self._battery_current = msg.current_battery / 100.0
                self._battery_remaining = msg.battery_remaining
            elif msg_type == "GPS_RAW_INT":
                self._gps_fix_type = msg.fix_type
                self._gps_satellites = msg.satellites_visible
                self._gps_hdop = msg.eph / 100.0
                self._gps_vdop = msg.epv / 100.0
            elif msg_type == "HEARTBEAT":
                self._last_heartbeat = time.time()
                self._armed = bool(msg.base_mode & MAV_MODE_FLAG_ARMED)
                self._system_status = msg.system_status
                custom = msg.custom_mode
                mode_map = {
                    0: "STABILIZE", 1: "ACRO", 2: "ALT_HOLD",
                    3: "AUTO", 4: "GUIDED", 5: "LOITER",
                    6: "RTL", 7: "CIRCLE", 9: "LAND",
                    16: "POSHOLD", 17: "BRAKE", 19: "THROW",
                    21: "FLOWHOLD", 25: "SMART_RTL",
                }
                self._flight_mode = mode_map.get(custom, f"MODE_{custom}")
            elif msg_type == "RC_CHANNELS":
                self._rc_channels = [
                    msg.chan1_raw, msg.chan2_raw, msg.chan3_raw, msg.chan4_raw,
                    msg.chan5_raw, msg.chan6_raw, msg.chan7_raw, msg.chan8_raw,
                    msg.chan9_raw, msg.chan10_raw, msg.chan11_raw, msg.chan12_raw,
                    msg.chan13_raw, msg.chan14_raw, msg.chan15_raw, msg.chan16_raw,
                ]
            elif msg_type == "SERVO_OUTPUT_RAW":
                self._servo_outputs = [
                    msg.servo1_raw, msg.servo2_raw, msg.servo3_raw, msg.servo4_raw,
                    msg.servo5_raw, msg.servo6_raw, msg.servo7_raw, msg.servo8_raw,
                ]

    @readable(type="dict", description="GPS position (lat, lon, altitude MSL and relative)")
    def gps_position(self) -> dict:
        self._update_telemetry()
        return {
            "latitude": self._lat,
            "longitude": self._lon,
            "altitude_msl_m": self._alt_msl,
            "altitude_relative_m": self._alt_rel,
            "fix_type": self._gps_fix_type,
            "satellites": self._gps_satellites,
            "hdop": self._gps_hdop,
            "vdop": self._gps_vdop,
        }

    @readable(type="dict", description="Vehicle attitude (roll, pitch, yaw) in degrees")
    def attitude(self) -> dict:
        self._update_telemetry()
        return {
            "roll_deg": self._roll,
            "pitch_deg": self._pitch,
            "yaw_deg": self._yaw,
            "rollspeed_deg_s": self._rollspeed,
            "pitchspeed_deg_s": self._pitchspeed,
            "yawspeed_deg_s": self._yawspeed,
        }

    @readable(type="dict", description="Speed information (ground, air, climb rate)")
    def speed(self) -> dict:
        self._update_telemetry()
        return {
            "groundspeed_m_s": self._groundspeed,
            "airspeed_m_s": self._airspeed,
            "climb_rate_m_s": self._climb_rate,
            "heading_deg": self._heading_deg,
        }

    @readable(type="dict", description="Battery state (voltage, current, remaining percentage)")
    def battery(self) -> dict:
        self._update_telemetry()
        return {
            "voltage_v": self._battery_voltage,
            "current_a": self._battery_current,
            "remaining_percent": self._battery_remaining,
        }

    @readable(type="dict", description="Vehicle status (armed, mode, system state)")
    def vehicle_status(self) -> dict:
        self._update_telemetry()
        state_names = {
            0: "uninit", 1: "boot", 2: "calibrating",
            3: "standby", 4: "active", 5: "critical",
            6: "emergency",
        }
        return {
            "armed": self._armed,
            "flight_mode": self._flight_mode,
            "system_status": state_names.get(self._system_status, "unknown"),
            "vehicle_type": self._vehicle_type,
            "autopilot": self._autopilot_type,
            "messages_received": self._messages_received,
            "heartbeat_age_s": time.time() - self._last_heartbeat if self._last_heartbeat else 999,
        }

    @readable(type="list", description="RC channel inputs (PWM values, 16 channels)")
    def rc_channels(self) -> list[int]:
        self._update_telemetry()
        return self._rc_channels

    @readable(type="list", description="Servo output values (PWM, 8 channels)")
    def servo_outputs(self) -> list[int]:
        self._update_telemetry()
        return self._servo_outputs

    @safety(limit_type="hard", description="Altitude and geofence limits")
    def flight_envelope(self) -> dict:
        return {
            "max_altitude_m": self._max_altitude_m,
            "geofence_radius_m": self._geofence_radius_m,
            "home_latitude": self._home_lat,
            "home_longitude": self._home_lon,
            "min_battery_percent": 20,
            "max_speed_m_s": 20.0,
        }

    @procedure(description="Arm the vehicle motors (requires GPS lock and pre-arm checks passed)",
               requires_confirmation=True)
    def arm(self):
        self._ensure_connected()
        self._mav.mav.command_long_send(
            self._target_system, self._target_component,
            MAV_CMD_COMPONENT_ARM_DISARM, 0,
            1, 0, 0, 0, 0, 0, 0
        )
        success = self._wait_command_ack(MAV_CMD_COMPONENT_ARM_DISARM)
        if success:
            self._armed = True
        return {"status": "armed" if success else "arm_failed", "armed": self._armed}

    @procedure(description="Disarm the vehicle motors")
    def disarm(self):
        self._ensure_connected()
        self._mav.mav.command_long_send(
            self._target_system, self._target_component,
            MAV_CMD_COMPONENT_ARM_DISARM, 0,
            0, 0, 0, 0, 0, 0, 0
        )
        success = self._wait_command_ack(MAV_CMD_COMPONENT_ARM_DISARM)
        if success:
            self._armed = False
        return {"status": "disarmed" if success else "disarm_failed"}

    @procedure(description="Take off to specified altitude in meters",
               requires_confirmation=True)
    def takeoff(self, altitude_m: float = 10.0):
        self._ensure_connected()
        altitude_m = min(altitude_m, self._max_altitude_m)

        self._set_mode(COPTER_MODE_GUIDED)
        time.sleep(0.5)

        if not self._armed:
            self.arm()
            time.sleep(1.0)

        self._mav.mav.command_long_send(
            self._target_system, self._target_component,
            MAV_CMD_NAV_TAKEOFF, 0,
            0, 0, 0, 0, 0, 0, altitude_m
        )
        success = self._wait_command_ack(MAV_CMD_NAV_TAKEOFF)
        return {
            "status": "taking_off" if success else "takeoff_failed",
            "target_altitude_m": altitude_m,
        }

    @procedure(description="Land at current position")
    def land(self):
        self._ensure_connected()
        self._set_mode(COPTER_MODE_LAND)
        return {"status": "landing", "mode": "LAND"}

    @procedure(description="Return to launch position")
    def return_to_launch(self):
        self._ensure_connected()
        self._set_mode(COPTER_MODE_RTL)
        return {"status": "returning_to_launch", "mode": "RTL"}

    @procedure(description="Fly to GPS coordinates (lat, lon, alt)")
    def goto(self, latitude: float = 0.0, longitude: float = 0.0,
             altitude_m: float = 10.0):
        self._ensure_connected()
        altitude_m = min(altitude_m, self._max_altitude_m)

        if self._home_lat != 0 and self._home_lon != 0:
            dist = self._haversine_distance(
                self._home_lat, self._home_lon, latitude, longitude
            )
            if dist > self._geofence_radius_m:
                return {
                    "error": "Target outside geofence",
                    "distance_m": dist,
                    "geofence_m": self._geofence_radius_m,
                }

        self._set_mode(COPTER_MODE_GUIDED)
        self._mav.mav.set_position_target_global_int_send(
            0, self._target_system, self._target_component,
            6, 0b0000111111111000,
            int(latitude * 1e7), int(longitude * 1e7),
            altitude_m,
            0, 0, 0, 0, 0, 0, 0, 0
        )
        return {
            "status": "navigating",
            "target_lat": latitude,
            "target_lon": longitude,
            "target_alt_m": altitude_m,
        }

    @procedure(description="Set flight mode (STABILIZE, GUIDED, LOITER, AUTO, RTL, LAND, POSHOLD)")
    def set_mode(self, mode: str = "LOITER"):
        mode_map = {
            "STABILIZE": 0, "ACRO": 1, "ALT_HOLD": 2,
            "AUTO": 3, "GUIDED": 4, "LOITER": 5,
            "RTL": 6, "CIRCLE": 7, "LAND": 9,
            "POSHOLD": 16, "BRAKE": 17,
        }
        mode_num = mode_map.get(mode.upper())
        if mode_num is None:
            return {"error": f"Unknown mode: {mode}", "available": list(mode_map.keys())}
        self._set_mode(mode_num)
        self._flight_mode = mode.upper()
        return {"status": "mode_set", "mode": mode.upper()}

    def _set_mode(self, mode_num: int):
        """Set custom mode via MAVLink."""
        from pymavlink import mavutil
        self._mav.mav.set_mode_send(
            self._target_system,
            mavutil.mavlink.MAV_MODE_FLAG_CUSTOM_MODE_ENABLED,
            mode_num
        )

    @procedure(description="Change vehicle speed (m/s)")
    def set_speed(self, speed_m_s: float = 5.0, speed_type: int = 1):
        """speed_type: 0=airspeed, 1=groundspeed, 2=climb, 3=descent."""
        self._ensure_connected()
        self._mav.mav.command_long_send(
            self._target_system, self._target_component,
            MAV_CMD_DO_CHANGE_SPEED, 0,
            speed_type, speed_m_s, -1, 0, 0, 0, 0
        )
        return {"status": "speed_set", "speed_m_s": speed_m_s, "type": speed_type}

    @procedure(description="Set vehicle yaw/heading (degrees, 0=north, clockwise)")
    def set_yaw(self, heading_deg: float = 0.0, relative: bool = False,
                yaw_speed_deg_s: float = 10.0):
        self._ensure_connected()
        self._mav.mav.command_long_send(
            self._target_system, self._target_component,
            MAV_CMD_CONDITION_YAW, 0,
            heading_deg, yaw_speed_deg_s,
            1 if heading_deg >= 0 else -1,
            1.0 if relative else 0.0,
            0, 0, 0
        )
        return {"status": "yaw_set", "heading_deg": heading_deg, "relative": relative}

    @procedure(description="Loiter at current position for specified duration")
    def loiter(self, duration_s: float = 0.0):
        self._ensure_connected()
        if duration_s > 0:
            self._mav.mav.command_long_send(
                self._target_system, self._target_component,
                MAV_CMD_NAV_LOITER_TIME, 0,
                duration_s, 0, 0, 0, 0, 0, 0
            )
        else:
            self._set_mode(COPTER_MODE_LOITER)
        return {"status": "loitering", "duration_s": duration_s}

    @procedure(description="Set a servo channel to specific PWM value")
    def set_servo(self, channel: int = 9, pwm: int = 1500):
        self._ensure_connected()
        self._mav.mav.command_long_send(
            self._target_system, self._target_component,
            MAV_CMD_DO_SET_SERVO, 0,
            channel, pwm, 0, 0, 0, 0, 0
        )
        return {"status": "servo_set", "channel": channel, "pwm": pwm}

    @procedure(description="Upload a mission of waypoints to the vehicle")
    def upload_mission(self, waypoints: list[dict] | None = None):
        """Each waypoint: {lat, lon, alt_m, hold_time_s (optional), speed_m_s (optional)}."""
        self._ensure_connected()
        if not waypoints:
            return {"error": "No waypoints provided"}

        from pymavlink import mavutil

        self._mav.waypoint_clear_all_send()
        time.sleep(0.5)
        self._mav.waypoint_count_send(len(waypoints))
        time.sleep(0.5)

        for i, wp in enumerate(waypoints):
            lat = wp.get("lat", 0.0)
            lon = wp.get("lon", 0.0)
            alt = min(wp.get("alt_m", 10.0), self._max_altitude_m)
            hold = wp.get("hold_time_s", 0)

            self._mav.mav.mission_item_int_send(
                self._target_system, self._target_component,
                i, 3, MAV_CMD_NAV_WAYPOINT, 0, 1,
                hold, 0, 0, 0,
                int(lat * 1e7), int(lon * 1e7), alt
            )
            time.sleep(0.1)

        return {"status": "mission_uploaded", "waypoint_count": len(waypoints)}

    @procedure(description="Start the uploaded mission in AUTO mode")
    def start_mission(self):
        self._ensure_connected()
        self._set_mode(COPTER_MODE_AUTO)
        return {"status": "mission_started", "mode": "AUTO"}

    @monitor(interval_ms=2000, description="Monitor battery, GPS, and connection health")
    def check_vehicle_health(self) -> dict[str, Any]:
        alerts = []
        try:
            self._update_telemetry()
        except Exception as e:
            return {"healthy": False, "alerts": [{"level": "critical", "message": str(e)}]}

        heartbeat_age = time.time() - self._last_heartbeat if self._last_heartbeat else 999
        if heartbeat_age > 3.0:
            alerts.append({
                "level": "critical",
                "message": f"Heartbeat lost ({heartbeat_age:.1f}s ago)",
            })

        if self._battery_remaining < 20:
            alerts.append({
                "level": "critical",
                "message": f"Battery critical: {self._battery_remaining}%",
            })
        elif self._battery_remaining < 35:
            alerts.append({
                "level": "warning",
                "message": f"Battery low: {self._battery_remaining}%",
            })

        if self._gps_fix_type < 3:
            alerts.append({
                "level": "warning",
                "message": f"Poor GPS fix (type {self._gps_fix_type}, {self._gps_satellites} sats)",
            })

        if self._alt_rel > self._max_altitude_m:
            alerts.append({
                "level": "critical",
                "message": f"Altitude exceeds limit: {self._alt_rel:.1f}m > {self._max_altitude_m}m",
            })

        if self._system_status == MAV_STATE_CRITICAL:
            alerts.append({"level": "critical", "message": "Vehicle in CRITICAL state"})
        elif self._system_status == MAV_STATE_EMERGENCY:
            alerts.append({"level": "critical", "message": "Vehicle in EMERGENCY state"})

        return {
            "healthy": len(alerts) == 0,
            "armed": self._armed,
            "mode": self._flight_mode,
            "battery_percent": self._battery_remaining,
            "gps_fix": self._gps_fix_type,
            "satellites": self._gps_satellites,
            "altitude_m": self._alt_rel,
            "groundspeed_m_s": self._groundspeed,
            "heartbeat_age_s": heartbeat_age,
            "alerts": alerts,
        }

    @staticmethod
    def _haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        """Calculate distance between two GPS points in meters."""
        R = 6371000
        phi1 = math.radians(lat1)
        phi2 = math.radians(lat2)
        dphi = math.radians(lat2 - lat1)
        dlambda = math.radians(lon2 - lon1)
        a = (math.sin(dphi / 2) ** 2 +
             math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2)
        return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
