"""KHP Driver: G-code CNC and 3D Printer Controller.

Controls CNC machines and 3D printers running Marlin, Grbl, Klipper,
RepRapFirmware, or Smoothieware via serial (USB) or network connection.
Provides full motion control, temperature management, tool handling,
print job management, and real time position/status feedback.

Supports: FDM/FFF 3D printers, CNC routers, laser cutters/engravers,
pick and place machines, and any device accepting G-code over serial.

Requirements:
    pip install pyserial
    (USB serial connection to controller board, or TCP socket for networked printers)
"""
from __future__ import annotations

import re
import time
import threading
from typing import Any

from khp import Driver, readable, writable, procedure, safety, monitor
from khp.core import ConnectionType


FIRMWARE_MARLIN = "marlin"
FIRMWARE_GRBL = "grbl"
FIRMWARE_KLIPPER = "klipper"
FIRMWARE_REPRAP = "reprapfirmware"
FIRMWARE_SMOOTHIE = "smoothieware"

STATE_IDLE = "idle"
STATE_PRINTING = "printing"
STATE_PAUSED = "paused"
STATE_ERROR = "error"
STATE_HOMING = "homing"
STATE_PROBING = "probing"


class GCodeDevice(Driver):
    """G-code controller driver for CNC machines and 3D printers."""

    name = "G-code Controller"
    version = "1.0.0"
    device_type = "cnc_printer"
    description = "Marlin/Grbl/Klipper/RepRap controller via serial G-code commands"
    connection_type = ConnectionType.SERIAL

    def __init__(self, device_id: str | None = None,
                 port: str = "/dev/ttyUSB0", baudrate: int = 115200,
                 firmware: str = FIRMWARE_MARLIN, timeout: float = 5.0,
                 bed_size_x: float = 220.0, bed_size_y: float = 220.0,
                 max_z: float = 250.0, num_extruders: int = 1,
                 heated_bed: bool = True, max_hotend_temp: float = 300.0,
                 max_bed_temp: float = 120.0, tcp_host: str | None = None,
                 tcp_port: int = 23, **config):
        super().__init__(device_id=device_id, port=port, **config)
        self._port = port
        self._baudrate = baudrate
        self._firmware = firmware.lower()
        self._timeout = timeout
        self._bed_size_x = bed_size_x
        self._bed_size_y = bed_size_y
        self._max_z = max_z
        self._num_extruders = num_extruders
        self._heated_bed = heated_bed
        self._max_hotend_temp = max_hotend_temp
        self._max_bed_temp = max_bed_temp
        self._tcp_host = tcp_host
        self._tcp_port = tcp_port

        self._serial = None
        self._socket = None
        self._lock = threading.Lock()
        self._response_buffer = []

        self._pos_x = 0.0
        self._pos_y = 0.0
        self._pos_z = 0.0
        self._pos_e = 0.0
        self._hotend_temps: list[dict] = [{"current": 0.0, "target": 0.0}] * num_extruders
        self._bed_temp = {"current": 0.0, "target": 0.0}
        self._fan_speed = 0
        self._feedrate = 0
        self._flow_rate = 100
        self._speed_factor = 100
        self._state = STATE_IDLE
        self._firmware_info = ""
        self._progress_percent = 0.0
        self._print_time_s = 0
        self._filament_used_mm = 0.0
        self._sd_printing = False
        self._sd_progress = 0.0
        self._endstop_states: dict[str, bool] = {}
        self._line_number = 0
        self._errors: list[str] = []

    async def connect(self):
        """Connect to controller via serial or TCP."""
        try:
            if self._tcp_host:
                import socket
                self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self._socket.settimeout(self._timeout)
                self._socket.connect((self._tcp_host, self._tcp_port))
                time.sleep(1.0)
                self._read_until_idle()
            else:
                import serial
                self._serial = serial.Serial(
                    self._port, self._baudrate, timeout=self._timeout
                )
                time.sleep(2.0)
                self._read_until_idle()

            info = self._send_command("M115")
            self._firmware_info = info
            if "FIRMWARE_NAME" in info:
                match = re.search(r"FIRMWARE_NAME:([^\s]+)", info)
                if match:
                    self.name = f"G-code: {match.group(1)}"

            self._update_position()
            self._update_temperatures()
            await super().connect()

        except ImportError as e:
            from khp.errors import DriverLoadError
            raise DriverLoadError(
                f"Required package not installed: {e}. Install with: pip install pyserial",
                device_id=self.device_id,
            )
        except Exception as e:
            from khp.errors import DeviceOfflineError
            raise DeviceOfflineError(
                f"Cannot connect to controller at {self._port}: {e}",
                device_id=self.device_id,
            )

    async def disconnect(self):
        """Close serial/TCP connection."""
        if self._serial:
            self._serial.close()
            self._serial = None
        if self._socket:
            self._socket.close()
            self._socket = None
        await super().disconnect()

    async def emergency_stop(self):
        """Send emergency stop (M112) and reset."""
        try:
            self._send_raw("M112\n")
            self._state = STATE_ERROR
        except Exception:
            pass

    def _ensure_connected(self):
        if not self._serial and not self._socket:
            from khp.errors import DeviceOfflineError
            raise DeviceOfflineError(
                "Not connected to G-code controller", device_id=self.device_id
            )

    def _send_raw(self, data: str):
        """Send raw bytes to controller."""
        encoded = data.encode("ascii")
        if self._serial:
            self._serial.write(encoded)
            self._serial.flush()
        elif self._socket:
            self._socket.sendall(encoded)

    def _read_line(self) -> str:
        """Read one line from controller."""
        if self._serial:
            line = self._serial.readline().decode("ascii", errors="replace").strip()
            return line
        elif self._socket:
            buf = b""
            while True:
                byte = self._socket.recv(1)
                if not byte or byte == b"\n":
                    break
                buf += byte
            return buf.decode("ascii", errors="replace").strip()
        return ""

    def _read_until_idle(self):
        """Read and discard all pending data."""
        if self._serial:
            while self._serial.in_waiting:
                self._serial.readline()
        elif self._socket:
            self._socket.settimeout(0.5)
            try:
                while True:
                    data = self._socket.recv(4096)
                    if not data:
                        break
            except Exception:
                pass
            self._socket.settimeout(self._timeout)

    def _send_command(self, gcode: str, timeout: float = 10.0) -> str:
        """Send G-code command and wait for ok response."""
        with self._lock:
            self._ensure_connected()
            self._line_number += 1
            self._send_raw(f"{gcode}\n")

            responses = []
            start = time.time()
            while time.time() - start < timeout:
                line = self._read_line()
                if not line:
                    continue
                if line.startswith("ok"):
                    break
                if line.startswith("error") or line.startswith("!!"):
                    self._errors.append(line)
                    self._state = STATE_ERROR
                    break
                responses.append(line)

            return "\n".join(responses)

    def _update_position(self):
        """Query and parse current position."""
        resp = self._send_command("M114")
        match = re.search(
            r"X:([0-9.\-]+)\s+Y:([0-9.\-]+)\s+Z:([0-9.\-]+)\s+E:([0-9.\-]+)",
            resp
        )
        if match:
            self._pos_x = float(match.group(1))
            self._pos_y = float(match.group(2))
            self._pos_z = float(match.group(3))
            self._pos_e = float(match.group(4))

    def _update_temperatures(self):
        """Query and parse temperature readings."""
        resp = self._send_command("M105")
        t_match = re.search(r"T:([0-9.]+)\s*/\s*([0-9.]+)", resp)
        if t_match:
            self._hotend_temps[0] = {
                "current": float(t_match.group(1)),
                "target": float(t_match.group(2)),
            }

        for i in range(1, self._num_extruders):
            ti_match = re.search(rf"T{i}:([0-9.]+)\s*/\s*([0-9.]+)", resp)
            if ti_match:
                self._hotend_temps[i] = {
                    "current": float(ti_match.group(1)),
                    "target": float(ti_match.group(2)),
                }

        b_match = re.search(r"B:([0-9.]+)\s*/\s*([0-9.]+)", resp)
        if b_match:
            self._bed_temp = {
                "current": float(b_match.group(1)),
                "target": float(b_match.group(2)),
            }

    @readable(type="dict", description="Current tool position (X, Y, Z, E in mm)")
    def position(self) -> dict:
        self._update_position()
        return {
            "x_mm": self._pos_x,
            "y_mm": self._pos_y,
            "z_mm": self._pos_z,
            "e_mm": self._pos_e,
        }

    @readable(type="dict", description="All temperature readings (hotend(s) and bed)")
    def temperatures(self) -> dict:
        self._update_temperatures()
        result = {"bed": self._bed_temp}
        for i, t in enumerate(self._hotend_temps):
            result[f"hotend_{i}"] = t
        return result

    @readable(type="str", description="Current machine state (idle, printing, paused, error)")
    def machine_state(self) -> str:
        return self._state

    @readable(type="dict", description="Print progress (percent, time, filament used)")
    def print_progress(self) -> dict:
        if self._sd_printing:
            resp = self._send_command("M27")
            match = re.search(r"SD printing byte (\d+)/(\d+)", resp)
            if match:
                done = int(match.group(1))
                total = int(match.group(2))
                self._sd_progress = (done / total * 100) if total > 0 else 0
        return {
            "state": self._state,
            "progress_percent": self._sd_progress if self._sd_printing else self._progress_percent,
            "print_time_s": self._print_time_s,
            "filament_used_mm": self._filament_used_mm,
            "sd_printing": self._sd_printing,
        }

    @readable(type="dict", description="Firmware identification and capabilities")
    def firmware_info(self) -> dict:
        return {
            "firmware_type": self._firmware,
            "firmware_string": self._firmware_info,
            "bed_size_x": self._bed_size_x,
            "bed_size_y": self._bed_size_y,
            "max_z": self._max_z,
            "num_extruders": self._num_extruders,
            "heated_bed": self._heated_bed,
        }

    @readable(type="dict", description="Endstop trigger states")
    def endstops(self) -> dict:
        resp = self._send_command("M119")
        states = {}
        for line in resp.split("\n"):
            match = re.match(r"(\w+):\s*(open|TRIGGERED|triggered|closed)", line.strip())
            if match:
                states[match.group(1)] = match.group(2).lower() in ("triggered", "closed")
        self._endstop_states = states
        return states

    @readable(type="int", description="Part cooling fan speed (0 to 255)")
    def fan_speed(self) -> int:
        return self._fan_speed

    @readable(type="list", description="Recent error messages from controller")
    def error_log(self) -> list:
        return self._errors[-20:]

    @writable(type="float", description="Set hotend target temperature (Celsius)")
    def hotend_temperature(self, value: float):
        value = max(0.0, min(float(value), self._max_hotend_temp))
        self._send_command(f"M104 S{value:.1f}")
        self._hotend_temps[0]["target"] = value

    @writable(type="float", description="Set bed target temperature (Celsius)")
    def bed_temperature(self, value: float):
        if not self._heated_bed:
            return
        value = max(0.0, min(float(value), self._max_bed_temp))
        self._send_command(f"M140 S{value:.1f}")
        self._bed_temp["target"] = value

    @writable(type="int", description="Set fan speed (0 to 255)")
    def set_fan_speed(self, value: int):
        value = max(0, min(255, int(value)))
        self._send_command(f"M106 S{value}")
        self._fan_speed = value

    @writable(type="int", description="Set feedrate percentage override (10 to 500)")
    def feedrate_override(self, value: int):
        value = max(10, min(500, int(value)))
        self._send_command(f"M220 S{value}")
        self._speed_factor = value

    @writable(type="int", description="Set flow rate percentage (10 to 300)")
    def flow_rate(self, value: int):
        value = max(10, min(300, int(value)))
        self._send_command(f"M221 S{value}")
        self._flow_rate = value

    @safety(limit_type="hard", description="Physical travel limits and temperature maximums")
    def machine_limits(self) -> dict:
        return {
            "bed_size_x_mm": self._bed_size_x,
            "bed_size_y_mm": self._bed_size_y,
            "max_z_mm": self._max_z,
            "max_hotend_temp_c": self._max_hotend_temp,
            "max_bed_temp_c": self._max_bed_temp,
            "min_position": {"x": 0, "y": 0, "z": 0},
            "max_position": {"x": self._bed_size_x, "y": self._bed_size_y, "z": self._max_z},
        }

    @procedure(description="Send raw G-code command and return response")
    def send_gcode(self, command: str = ""):
        if not command:
            return {"error": "No command provided"}
        response = self._send_command(command)
        return {"command": command, "response": response}

    @procedure(description="Home all axes (G28)")
    def home_all(self):
        self._state = STATE_HOMING
        resp = self._send_command("G28", timeout=60.0)
        self._state = STATE_IDLE
        self._update_position()
        return {
            "status": "homed",
            "position": {"x": self._pos_x, "y": self._pos_y, "z": self._pos_z},
        }

    @procedure(description="Home specific axes")
    def home_axis(self, x: bool = False, y: bool = False, z: bool = False):
        axes = ""
        if x:
            axes += " X"
        if y:
            axes += " Y"
        if z:
            axes += " Z"
        if not axes:
            return {"error": "No axis specified"}
        self._state = STATE_HOMING
        self._send_command(f"G28{axes}", timeout=60.0)
        self._state = STATE_IDLE
        self._update_position()
        return {"status": "homed", "axes": axes.strip()}

    @procedure(description="Move to absolute position (mm) with optional feedrate (mm/min)")
    def move_to(self, x: float | None = None, y: float | None = None,
                z: float | None = None, e: float | None = None,
                feedrate: float | None = None):
        cmd = "G1"
        if x is not None:
            x = max(0, min(x, self._bed_size_x))
            cmd += f" X{x:.3f}"
        if y is not None:
            y = max(0, min(y, self._bed_size_y))
            cmd += f" Y{y:.3f}"
        if z is not None:
            z = max(0, min(z, self._max_z))
            cmd += f" Z{z:.3f}"
        if e is not None:
            cmd += f" E{e:.4f}"
        if feedrate is not None:
            cmd += f" F{feedrate:.0f}"
        self._send_command("G90")
        self._send_command(cmd)
        self._update_position()
        return {"status": "moved", "position": {"x": self._pos_x, "y": self._pos_y, "z": self._pos_z}}

    @procedure(description="Relative move (mm) from current position")
    def move_relative(self, x: float = 0, y: float = 0, z: float = 0,
                      e: float = 0, feedrate: float = 1000):
        self._send_command("G91")
        cmd = f"G1 X{x:.3f} Y{y:.3f} Z{z:.3f} E{e:.4f} F{feedrate:.0f}"
        self._send_command(cmd)
        self._send_command("G90")
        self._update_position()
        return {"status": "moved", "position": {"x": self._pos_x, "y": self._pos_y, "z": self._pos_z}}

    @procedure(description="Auto bed leveling probe sequence (G29)")
    def auto_level(self):
        self._state = STATE_PROBING
        resp = self._send_command("G29", timeout=300.0)
        self._state = STATE_IDLE
        return {"status": "leveling_complete", "response": resp}

    @procedure(description="Set Z offset for first layer adjustment (mm)")
    def set_z_offset(self, offset_mm: float = 0.0):
        offset_mm = max(-2.0, min(2.0, offset_mm))
        self._send_command(f"M851 Z{offset_mm:.3f}")
        return {"status": "z_offset_set", "offset_mm": offset_mm}

    @procedure(description="Start printing from SD card file")
    def print_sd_file(self, filename: str = ""):
        if not filename:
            return {"error": "No filename provided"}
        self._send_command(f"M23 {filename}")
        self._send_command("M24")
        self._sd_printing = True
        self._state = STATE_PRINTING
        return {"status": "printing", "file": filename}

    @procedure(description="List files on SD card")
    def list_sd_files(self):
        resp = self._send_command("M20")
        files = []
        for line in resp.split("\n"):
            line = line.strip()
            if line and not line.startswith("Begin") and not line.startswith("End"):
                parts = line.split()
                if parts:
                    files.append({"name": parts[0], "size": int(parts[1]) if len(parts) > 1 else 0})
        return {"files": files, "count": len(files)}

    @procedure(description="Pause the current print")
    def pause_print(self):
        self._send_command("M25")
        self._state = STATE_PAUSED
        return {"status": "paused"}

    @procedure(description="Resume a paused print")
    def resume_print(self):
        self._send_command("M24")
        self._state = STATE_PRINTING
        return {"status": "resumed"}

    @procedure(description="Cancel the current print", requires_confirmation=True)
    def cancel_print(self):
        self._send_command("M524")
        self._send_command("M104 S0")
        self._send_command("M140 S0")
        self._send_command("M106 S0")
        self._send_command("G91")
        self._send_command("G1 Z10 F3000")
        self._send_command("G90")
        self._send_command("G28 X Y")
        self._sd_printing = False
        self._state = STATE_IDLE
        return {"status": "cancelled", "heaters_off": True, "head_raised": True}

    @procedure(description="Preheat for PLA (hotend 200C, bed 60C)")
    def preheat_pla(self):
        self._send_command("M104 S200")
        self._send_command("M140 S60")
        self._hotend_temps[0]["target"] = 200.0
        self._bed_temp["target"] = 60.0
        return {"status": "preheating", "hotend_target": 200, "bed_target": 60, "material": "PLA"}

    @procedure(description="Preheat for ABS/ASA (hotend 240C, bed 100C)")
    def preheat_abs(self):
        self._send_command("M104 S240")
        self._send_command("M140 S100")
        self._hotend_temps[0]["target"] = 240.0
        self._bed_temp["target"] = 100.0
        return {"status": "preheating", "hotend_target": 240, "bed_target": 100, "material": "ABS"}

    @procedure(description="Cool down all heaters")
    def cooldown(self):
        self._send_command("M104 S0")
        self._send_command("M140 S0")
        self._send_command("M106 S0")
        self._hotend_temps[0]["target"] = 0.0
        self._bed_temp["target"] = 0.0
        self._fan_speed = 0
        return {"status": "cooling_down"}

    @procedure(description="Extrude filament (mm) at specified temperature")
    def extrude(self, length_mm: float = 50.0, feedrate: float = 200.0,
                temperature: float | None = None):
        if temperature is not None:
            temperature = min(temperature, self._max_hotend_temp)
            self._send_command(f"M109 S{temperature:.0f}", timeout=120.0)
        self._send_command("G91")
        self._send_command(f"G1 E{length_mm:.2f} F{feedrate:.0f}")
        self._send_command("G90")
        self._filament_used_mm += abs(length_mm)
        return {"status": "extruded", "length_mm": length_mm, "feedrate": feedrate}

    @procedure(description="Retract filament (for filament change or clog clearing)")
    def retract(self, length_mm: float = 50.0, feedrate: float = 300.0):
        self._send_command("G91")
        self._send_command(f"G1 E-{abs(length_mm):.2f} F{feedrate:.0f}")
        self._send_command("G90")
        return {"status": "retracted", "length_mm": length_mm}

    @procedure(description="Disable stepper motors (release torque)")
    def disable_steppers(self):
        self._send_command("M84")
        return {"status": "steppers_disabled"}

    @procedure(description="Save settings to EEPROM")
    def save_settings(self):
        self._send_command("M500")
        return {"status": "settings_saved"}

    @procedure(description="Reset controller to factory settings", requires_confirmation=True)
    def factory_reset(self):
        self._send_command("M502")
        self._send_command("M500")
        return {"status": "factory_reset_complete"}

    @monitor(interval_ms=3000, description="Monitor temperatures and print progress")
    def check_printer_health(self) -> dict[str, Any]:
        alerts = []
        try:
            self._update_temperatures()
        except Exception as e:
            return {"healthy": False, "alerts": [{"level": "critical", "message": str(e)}]}

        for i, temp in enumerate(self._hotend_temps):
            if temp["current"] > self._max_hotend_temp:
                alerts.append({
                    "level": "critical",
                    "message": f"Hotend {i} over max temp: {temp['current']:.1f}C",
                })
            if temp["target"] > 0 and abs(temp["current"] - temp["target"]) > 15:
                if temp["current"] > temp["target"]:
                    alerts.append({
                        "level": "warning",
                        "message": f"Hotend {i} overshooting: {temp['current']:.1f}C (target {temp['target']:.1f}C)",
                    })

        if self._bed_temp["current"] > self._max_bed_temp:
            alerts.append({
                "level": "critical",
                "message": f"Bed over max temp: {self._bed_temp['current']:.1f}C",
            })

        if self._state == STATE_ERROR:
            alerts.append({"level": "critical", "message": "Controller in error state"})

        if self._errors and time.time() % 60 < 5:
            alerts.append({
                "level": "warning",
                "message": f"Recent error: {self._errors[-1]}",
            })

        return {
            "healthy": len(alerts) == 0,
            "state": self._state,
            "hotend_temp_c": self._hotend_temps[0]["current"],
            "hotend_target_c": self._hotend_temps[0]["target"],
            "bed_temp_c": self._bed_temp["current"],
            "bed_target_c": self._bed_temp["target"],
            "fan_speed": self._fan_speed,
            "progress_percent": self._sd_progress if self._sd_printing else self._progress_percent,
            "alerts": alerts,
        }
