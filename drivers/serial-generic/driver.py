"""KHP Driver — Generic Serial (RS-232/RS-485/USB-Serial).

Supports any device communicating over serial with text-based or binary protocols.
Covers: lab instruments, Arduino, microcontrollers, PLCs, barcode readers,
scales, GPS modules, weather stations, power supplies, etc.

Requirements:
    pip install pyserial
"""

from khp import Driver, readable, writable, procedure, safety
from khp.core import ConnectionType
from typing import Optional
import time


class SerialDevice(Driver):
    """Generic serial device driver — works with any RS-232/RS-485/USB device."""

    name = "Generic Serial Device"
    version = "1.0.0"
    device_type = "serial_device"
    description = "Serial port communication with configurable protocol"
    connection_type = ConnectionType.SERIAL

    def __init__(self, device_id: str = None, port: str = "/dev/ttyUSB0",
                 baud_rate: int = 9600, timeout: float = 2.0,
                 line_ending: str = "\r\n", encoding: str = "utf-8", **config):
        super().__init__(device_id=device_id, port=port, baud_rate=baud_rate,
                         timeout=timeout, **config)
        self._port = port
        self._baud_rate = baud_rate
        self._timeout = timeout
        self._line_ending = line_ending
        self._encoding = encoding
        self._serial = None
        self._last_response = ""

    async def connect(self):
        try:
            import serial
            self._serial = serial.Serial(
                port=self._port,
                baudrate=self._baud_rate,
                timeout=self._timeout,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
            )
            time.sleep(0.5)
            await super().connect()
        except (ImportError, Exception) as e:
            self._serial = None
            raise

    async def disconnect(self):
        if self._serial and self._serial.is_open:
            self._serial.close()
        self._serial = None
        await super().disconnect()

    async def health_check(self) -> bool:
        if self._serial is None:
            return False
        return self._serial.is_open

    def _send(self, command: str) -> str:
        """Send a command and return the response."""
        if not self._serial or not self._serial.is_open:
            return ""
        full_cmd = command + self._line_ending
        self._serial.write(full_cmd.encode(self._encoding))
        self._serial.flush()
        time.sleep(0.1)
        response = self._serial.readline().decode(self._encoding).strip()
        self._last_response = response
        return response

    def _send_raw(self, data: bytes) -> bytes:
        """Send raw bytes and read response."""
        if not self._serial or not self._serial.is_open:
            return b""
        self._serial.write(data)
        self._serial.flush()
        time.sleep(0.1)
        return self._serial.read(self._serial.in_waiting or 256)

    @readable(type="string", description="Last response from device")
    def last_response(self) -> str:
        return self._last_response

    @readable(type="bool", description="Whether serial port is connected and open")
    def is_connected(self) -> bool:
        return self._serial is not None and self._serial.is_open

    @readable(type="int", description="Bytes waiting in input buffer")
    def bytes_available(self) -> int:
        if self._serial and self._serial.is_open:
            return self._serial.in_waiting
        return 0

    @writable(type="string", description="Send a text command to the device")
    def command(self, value: str):
        self._send(value)

    @procedure(description="Send command and return response",
               estimated_duration_s=1.0)
    def query(self, command: str) -> str:
        """Send a command and return the device's response."""
        return self._send(command)

    @procedure(description="Send raw bytes (hex-encoded) to device",
               estimated_duration_s=0.5)
    def send_raw(self, hex_data: str) -> str:
        """Send raw bytes. Input as hex string (e.g., 'FF01A2')."""
        data = bytes.fromhex(hex_data)
        response = self._send_raw(data)
        return response.hex()

    @procedure(description="Read all available data from buffer",
               estimated_duration_s=0.5)
    def read_buffer(self) -> str:
        """Read all available data from the serial buffer."""
        if not self._serial or not self._serial.is_open:
            return ""
        data = self._serial.read(self._serial.in_waiting or 0)
        return data.decode(self._encoding, errors="replace")

    @procedure(description="Read lines until timeout",
               estimated_duration_s=5.0)
    def read_lines(self, max_lines: int = 10, timeout_s: float = 2.0) -> list:
        """Read multiple lines from the device."""
        lines = []
        if not self._serial:
            return lines
        old_timeout = self._serial.timeout
        self._serial.timeout = timeout_s
        for _ in range(max_lines):
            line = self._serial.readline().decode(self._encoding, errors="replace").strip()
            if not line:
                break
            lines.append(line)
        self._serial.timeout = old_timeout
        return lines

    @procedure(description="Flush input and output buffers",
               estimated_duration_s=0.1)
    def flush(self):
        """Flush serial buffers."""
        if self._serial and self._serial.is_open:
            self._serial.reset_input_buffer()
            self._serial.reset_output_buffer()
        return {"flushed": True}

    @procedure(description="Change baud rate (reconnects)",
               estimated_duration_s=1.0)
    def set_baud_rate(self, baud_rate: int = 9600):
        """Change the baud rate. Closes and reopens the connection."""
        if self._serial and self._serial.is_open:
            self._serial.close()
        self._baud_rate = baud_rate
        import serial
        self._serial = serial.Serial(
            port=self._port,
            baudrate=self._baud_rate,
            timeout=self._timeout,
        )
        return {"baud_rate": baud_rate}


class SerialScale(SerialDevice):
    """Serial-connected scale/balance (Mettler-Toledo, Ohaus, AND, Sartorius)."""

    name = "Serial Scale"
    version = "1.0.0"
    device_type = "sensor"
    description = "Lab scale/balance via RS-232 (supports Mettler-Toledo protocol)"

    def __init__(self, device_id: str = None, port: str = "/dev/ttyUSB0",
                 protocol: str = "mt-sics", **config):
        super().__init__(device_id=device_id, port=port, baud_rate=9600, **config)
        self._protocol = protocol
        self._last_weight = 0.0
        self._unit = "g"
        self._stable = False

    @readable(type="float", description="Current weight reading", unit="grams")
    def weight(self) -> float:
        if self._protocol == "mt-sics":
            response = self._send("SI")  # Stable weight
            if not response:
                response = self._send("S")  # Immediate weight
            try:
                parts = response.split()
                if len(parts) >= 3:
                    self._last_weight = float(parts[2])
                    self._unit = parts[3] if len(parts) > 3 else "g"
                    self._stable = parts[0] == "S"
            except (ValueError, IndexError):
                pass
        return self._last_weight

    @readable(type="bool", description="Whether reading is stable")
    def stable(self) -> bool:
        return self._stable

    @procedure(description="Tare (zero) the scale", estimated_duration_s=3.0)
    def tare(self):
        """Zero the scale."""
        if self._protocol == "mt-sics":
            return self._send("T")
        return self._send("Z")

    @procedure(description="Calibrate scale with known weight",
               requires_confirmation=True, estimated_duration_s=30.0)
    def calibrate(self, weight_g: float = 100.0):
        """Internal calibration routine."""
        return self._send(f"CAL {weight_g}")
