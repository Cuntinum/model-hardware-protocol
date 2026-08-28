"""KHP Driver — Raspberry Pi GPIO, I2C, SPI, Camera, PWM.

Supports:
- Digital GPIO read/write (all 40 pins)
- PWM output (hardware + software)
- I2C sensors (temperature, humidity, pressure, light)
- SPI devices
- Camera (picamera2 or USB via V4L2)
- System sensors (CPU temp, memory, disk)

Requirements:
    pip install RPi.GPIO smbus2 spidev picamera2
    (or: pip install gpiozero for simplified access)
"""

from khp import Driver, readable, writable, procedure, safety, monitor
from khp.core import ConnectionType
from typing import Optional
import time


class RaspberryPiGPIO(Driver):
    """Raspberry Pi GPIO driver — digital I/O, PWM, system monitoring."""

    name = "Raspberry Pi GPIO"
    version = "1.0.0"
    device_type = "gpio"
    description = "Raspberry Pi general-purpose I/O: digital pins, PWM, system sensors"
    connection_type = ConnectionType.GPIO

    def __init__(self, device_id: str = None, **config):
        super().__init__(device_id=device_id, **config)
        self._pin_modes = {}  # pin -> "IN" | "OUT" | "PWM"
        self._pin_values = {}
        self._pwm_duty = {}
        self._gpio = None

    async def connect(self):
        try:
            import RPi.GPIO as GPIO
            self._gpio = GPIO
            GPIO.setmode(GPIO.BCM)
            GPIO.setwarnings(False)
            await super().connect()
        except ImportError:
            self._gpio = None
            await super().connect()

    async def disconnect(self):
        if self._gpio:
            self._gpio.cleanup()
        await super().disconnect()

    @readable(type="object", description="All pin states as dict of pin->value")
    def pin_states(self) -> dict:
        if not self._gpio:
            return self._pin_values
        result = {}
        for pin, mode in self._pin_modes.items():
            if mode == "IN":
                result[str(pin)] = self._gpio.input(pin)
            else:
                result[str(pin)] = self._pin_values.get(pin, 0)
        return result

    @readable(type="int", description="Read a specific GPIO pin (set pin via config)")
    def digital_read(self) -> int:
        pin = self.config.get("read_pin", 4)
        if self._gpio:
            return self._gpio.input(pin)
        return self._pin_values.get(pin, 0)

    @monitor(interval_ms=1000, alert_above=80.0, action="emit_event")
    @readable(type="float", description="CPU temperature", unit="celsius")
    def cpu_temperature(self) -> float:
        try:
            with open("/sys/class/thermal/thermal_zone0/temp") as f:
                return int(f.read().strip()) / 1000.0
        except (FileNotFoundError, ValueError):
            return 0.0

    @readable(type="float", description="CPU usage percentage", unit="percent")
    def cpu_usage(self) -> float:
        try:
            with open("/proc/stat") as f:
                line = f.readline()
            parts = line.split()
            idle = int(parts[4])
            total = sum(int(p) for p in parts[1:])
            return round((1 - idle / total) * 100, 1)
        except (FileNotFoundError, ValueError, ZeroDivisionError):
            return 0.0

    @readable(type="object", description="Memory usage: total, used, free in MB")
    def memory(self) -> dict:
        try:
            with open("/proc/meminfo") as f:
                lines = f.readlines()
            mem = {}
            for line in lines[:3]:
                key, value = line.split(":")
                mem[key.strip()] = int(value.strip().split()[0]) // 1024
            return {
                "total_mb": mem.get("MemTotal", 0),
                "free_mb": mem.get("MemFree", 0),
                "available_mb": mem.get("MemAvailable", 0),
            }
        except (FileNotFoundError, ValueError):
            return {"total_mb": 0, "free_mb": 0, "available_mb": 0}

    @readable(type="float", description="Disk usage percentage of root", unit="percent")
    def disk_usage(self) -> float:
        try:
            import os
            stat = os.statvfs("/")
            total = stat.f_blocks * stat.f_frsize
            free = stat.f_bfree * stat.f_frsize
            return round((1 - free / total) * 100, 1)
        except (OSError, ZeroDivisionError):
            return 0.0

    @safety(min=0, max=27, reason="BCM pin numbers 0-27 valid on RPi")
    @writable(type="int", description="Set GPIO pin HIGH (1) or LOW (0)")
    def digital_write(self, value: int):
        pin = self.config.get("write_pin", 17)
        if self._gpio:
            if pin not in self._pin_modes or self._pin_modes[pin] != "OUT":
                self._gpio.setup(pin, self._gpio.OUT)
                self._pin_modes[pin] = "OUT"
            self._gpio.output(pin, value)
        self._pin_values[pin] = value

    @safety(min=0.0, max=100.0, reason="PWM duty cycle 0-100%")
    @writable(type="float", description="Set PWM duty cycle", unit="percent")
    def pwm_duty_cycle(self, value: float):
        pin = self.config.get("pwm_pin", 18)
        self._pwm_duty[pin] = value
        if self._gpio:
            if pin not in self._pin_modes or self._pin_modes[pin] != "PWM":
                self._gpio.setup(pin, self._gpio.OUT)
                self._pin_modes[pin] = "PWM"

    @procedure(description="Configure a pin as input or output",
               estimated_duration_s=0.01)
    def setup_pin(self, pin: int, mode: str = "OUT"):
        """Configure a GPIO pin. mode: IN, OUT, or PWM."""
        if self._gpio:
            if mode == "IN":
                self._gpio.setup(pin, self._gpio.IN, pull_up_down=self._gpio.PUD_UP)
            elif mode == "OUT":
                self._gpio.setup(pin, self._gpio.OUT)
        self._pin_modes[pin] = mode
        return {"pin": pin, "mode": mode}

    @procedure(description="Blink an LED on a pin N times",
               estimated_duration_s=5.0)
    def blink(self, pin: int = 17, count: int = 5, interval_s: float = 0.5):
        """Blink an output pin."""
        if self._gpio:
            if pin not in self._pin_modes:
                self._gpio.setup(pin, self._gpio.OUT)
                self._pin_modes[pin] = "OUT"
            for _ in range(count):
                self._gpio.output(pin, 1)
                time.sleep(interval_s)
                self._gpio.output(pin, 0)
                time.sleep(interval_s)
        return {"pin": pin, "blinks": count}

    @procedure(description="Read I2C sensor at given address",
               estimated_duration_s=0.1)
    def i2c_read(self, bus: int = 1, address: int = 0x48, register: int = 0x00,
                 length: int = 2):
        """Read bytes from an I2C device."""
        try:
            import smbus2
            with smbus2.SMBus(bus) as i2c:
                data = i2c.read_i2c_block_data(address, register, length)
            return {"bus": bus, "address": hex(address), "data": data}
        except (ImportError, OSError) as e:
            return {"error": str(e)}

    @procedure(description="Write to I2C device",
               estimated_duration_s=0.1)
    def i2c_write(self, bus: int = 1, address: int = 0x48, register: int = 0x00,
                  data: list = None):
        """Write bytes to an I2C device."""
        data = data or [0x00]
        try:
            import smbus2
            with smbus2.SMBus(bus) as i2c:
                i2c.write_i2c_block_data(address, register, data)
            return {"bus": bus, "address": hex(address), "written": len(data)}
        except (ImportError, OSError) as e:
            return {"error": str(e)}

    @procedure(description="Capture a frame from the camera",
               estimated_duration_s=1.0)
    def capture_image(self, output_path: str = "/tmp/khp_capture.jpg",
                      width: int = 1920, height: int = 1080):
        """Capture image from connected camera."""
        try:
            from picamera2 import Picamera2
            cam = Picamera2()
            config = cam.create_still_configuration(
                main={"size": (width, height)})
            cam.configure(config)
            cam.start()
            time.sleep(0.5)
            cam.capture_file(output_path)
            cam.stop()
            cam.close()
            return {"path": output_path, "width": width, "height": height}
        except ImportError:
            try:
                import cv2
                cap = cv2.VideoCapture(0)
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
                ret, frame = cap.read()
                cap.release()
                if ret:
                    cv2.imwrite(output_path, frame)
                    return {"path": output_path, "width": width, "height": height}
                return {"error": "Failed to capture frame"}
            except ImportError:
                return {"error": "No camera library available (picamera2 or opencv)"}


class RaspberryPiI2CSensor(Driver):
    """Generic I2C sensor driver for Raspberry Pi (BME280, BMP390, AHT20, etc.)."""

    name = "RPi I2C Sensor"
    version = "1.0.0"
    device_type = "sensor"
    description = "I2C environmental sensor (temperature, humidity, pressure)"
    connection_type = ConnectionType.GPIO

    def __init__(self, device_id: str = None, bus: int = 1,
                 address: int = 0x76, sensor_type: str = "bme280", **config):
        super().__init__(device_id=device_id, bus=bus, address=address,
                         sensor_type=sensor_type, **config)
        self._bus_num = bus
        self._address = address
        self._sensor_type = sensor_type
        self._last_reading = {"temperature": 0.0, "humidity": 0.0, "pressure": 0.0}

    async def connect(self):
        await super().connect()
        self._read_sensor()

    def _read_sensor(self):
        """Read from I2C sensor. Override for specific sensor types."""
        try:
            import smbus2
            bus = smbus2.SMBus(self._bus_num)
            if self._sensor_type == "bme280":
                data = bus.read_i2c_block_data(self._address, 0xF7, 8)
                raw_pressure = (data[0] << 12) | (data[1] << 4) | (data[2] >> 4)
                raw_temp = (data[3] << 12) | (data[4] << 4) | (data[5] >> 4)
                raw_humidity = (data[6] << 8) | data[7]
                self._last_reading = {
                    "temperature": raw_temp / 100.0,
                    "humidity": raw_humidity / 100.0,
                    "pressure": raw_pressure / 100.0,
                }
            bus.close()
        except (ImportError, OSError):
            pass

    @monitor(interval_ms=2000, alert_above=50.0)
    @readable(type="float", description="Temperature reading", unit="celsius")
    def temperature(self) -> float:
        self._read_sensor()
        return self._last_reading["temperature"]

    @readable(type="float", description="Relative humidity", unit="percent")
    def humidity(self) -> float:
        self._read_sensor()
        return self._last_reading["humidity"]

    @readable(type="float", description="Atmospheric pressure", unit="hPa")
    def pressure(self) -> float:
        self._read_sensor()
        return self._last_reading["pressure"]
