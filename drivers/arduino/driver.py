"""KHP Driver — Arduino/Microcontroller (Serial-based).

Supports Arduino, ESP32, ESP8266, Teensy, STM32, and any microcontroller
communicating via serial with a simple text protocol.

Expected firmware protocol (implement on Arduino side):
    Commands sent as: "CMD:arg1:arg2\n"
    Responses as: "OK:value\n" or "ERR:message\n"

    Built-in commands:
        "PIN_READ:13"       → "OK:1"
        "PIN_WRITE:13:1"    → "OK"
        "ANALOG_READ:A0"    → "OK:512"
        "PWM_WRITE:9:128"   → "OK"
        "SERVO:9:90"        → "OK"
        "INFO"              → "OK:Arduino Uno:v1.0"
        "TEMP"              → "OK:23.5" (if sensor connected)

Requirements:
    pip install pyserial
"""

from khp import Driver, readable, writable, procedure, safety, monitor
from khp.core import ConnectionType
from typing import Optional
import time


class ArduinoDevice(Driver):
    """Arduino/microcontroller driver via serial text protocol."""

    name = "Arduino"
    version = "1.0.0"
    device_type = "gpio"
    description = "Arduino/ESP32/Teensy microcontroller via USB serial"
    connection_type = ConnectionType.SERIAL

    def __init__(self, device_id: str = None, port: str = "/dev/ttyACM0",
                 baud_rate: int = 115200, timeout: float = 2.0, **config):
        super().__init__(device_id=device_id, port=port, baud_rate=baud_rate, **config)
        self._port = port
        self._baud_rate = baud_rate
        self._timeout = timeout
        self._serial = None
        self._board_info = ""

    async def connect(self):
        import serial
        self._serial = serial.Serial(
            port=self._port,
            baudrate=self._baud_rate,
            timeout=self._timeout,
        )
        time.sleep(2.0)  # Arduino resets on serial connect
        self._serial.reset_input_buffer()
        info = self._send_command("INFO")
        self._board_info = info
        await super().connect()

    async def disconnect(self):
        if self._serial and self._serial.is_open:
            self._serial.close()
        self._serial = None
        await super().disconnect()

    def _send_command(self, cmd: str) -> str:
        """Send command and read response. Returns value after 'OK:' or empty on error."""
        if not self._serial or not self._serial.is_open:
            return ""
        self._serial.write(f"{cmd}\n".encode())
        self._serial.flush()
        response = self._serial.readline().decode().strip()
        if response.startswith("OK:"):
            return response[3:]
        elif response == "OK":
            return "OK"
        return ""

    @readable(type="string", description="Board info (name and firmware version)")
    def board_info(self) -> str:
        return self._board_info or self._send_command("INFO")

    @readable(type="int", description="Digital pin state (configured read pin)")
    def digital_input(self) -> int:
        pin = self.config.get("input_pin", 2)
        result = self._send_command(f"PIN_READ:{pin}")
        try:
            return int(result)
        except ValueError:
            return 0

    @readable(type="int", description="Analog reading (0-1023 for 10-bit ADC)")
    def analog_input(self) -> int:
        pin = self.config.get("analog_pin", "A0")
        result = self._send_command(f"ANALOG_READ:{pin}")
        try:
            return int(result)
        except ValueError:
            return 0

    @readable(type="float", description="Temperature from connected sensor", unit="celsius")
    def temperature(self) -> float:
        result = self._send_command("TEMP")
        try:
            return float(result)
        except ValueError:
            return 0.0

    @readable(type="float", description="Humidity from connected sensor", unit="percent")
    def humidity(self) -> float:
        result = self._send_command("HUMIDITY")
        try:
            return float(result)
        except ValueError:
            return 0.0

    @safety(min=0, max=1)
    @writable(type="int", description="Set digital output pin HIGH(1) or LOW(0)")
    def digital_output(self, value: int):
        pin = self.config.get("output_pin", 13)
        self._send_command(f"PIN_WRITE:{pin}:{value}")

    @safety(min=0, max=255, reason="PWM duty cycle 0-255 (8-bit)")
    @writable(type="int", description="Set PWM output (0-255)")
    def pwm_output(self, value: int):
        pin = self.config.get("pwm_pin", 9)
        self._send_command(f"PWM_WRITE:{pin}:{value}")

    @safety(min=0, max=180, reason="Servo angle 0-180 degrees")
    @writable(type="int", description="Set servo angle", unit="degrees")
    def servo_angle(self, value: int):
        pin = self.config.get("servo_pin", 9)
        self._send_command(f"SERVO:{pin}:{value}")

    @procedure(description="Read a specific digital pin",
               estimated_duration_s=0.1)
    def read_pin(self, pin: int = 2) -> dict:
        """Read digital state of a specific pin."""
        result = self._send_command(f"PIN_READ:{pin}")
        try:
            return {"pin": pin, "value": int(result)}
        except ValueError:
            return {"pin": pin, "error": "read failed"}

    @procedure(description="Write to a specific digital pin",
               estimated_duration_s=0.1)
    def write_pin(self, pin: int = 13, value: int = 1) -> dict:
        """Set a specific pin HIGH(1) or LOW(0)."""
        self._send_command(f"PIN_WRITE:{pin}:{value}")
        return {"pin": pin, "value": value}

    @procedure(description="Read analog value from specific pin",
               estimated_duration_s=0.1)
    def read_analog(self, pin: str = "A0") -> dict:
        """Read analog value (0-1023) from specified analog pin."""
        result = self._send_command(f"ANALOG_READ:{pin}")
        try:
            return {"pin": pin, "value": int(result)}
        except ValueError:
            return {"pin": pin, "error": "read failed"}

    @procedure(description="Sweep servo from min to max angle",
               estimated_duration_s=5.0)
    def servo_sweep(self, pin: int = 9, min_angle: int = 0,
                    max_angle: int = 180, step: int = 5,
                    delay_ms: int = 50) -> dict:
        """Sweep servo back and forth."""
        for angle in range(min_angle, max_angle + 1, step):
            self._send_command(f"SERVO:{pin}:{angle}")
            time.sleep(delay_ms / 1000.0)
        for angle in range(max_angle, min_angle - 1, -step):
            self._send_command(f"SERVO:{pin}:{angle}")
            time.sleep(delay_ms / 1000.0)
        return {"pin": pin, "sweep": f"{min_angle}-{max_angle}"}

    @procedure(description="Blink an LED N times",
               estimated_duration_s=10.0)
    def blink_led(self, pin: int = 13, count: int = 5,
                  on_ms: int = 500, off_ms: int = 500) -> dict:
        """Blink LED on specified pin."""
        for _ in range(count):
            self._send_command(f"PIN_WRITE:{pin}:1")
            time.sleep(on_ms / 1000.0)
            self._send_command(f"PIN_WRITE:{pin}:0")
            time.sleep(off_ms / 1000.0)
        return {"pin": pin, "blinks": count}

    @procedure(description="Send raw command to microcontroller",
               estimated_duration_s=1.0)
    def raw_command(self, command: str) -> str:
        """Send any command string and return the response."""
        return self._send_command(command)
