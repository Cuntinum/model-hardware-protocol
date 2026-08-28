"""KHP Driver: Bluetooth Low Energy (BLE) via GATT.

Supports IoT sensors, wearables, smart home devices, health monitors,
environmental sensors, and any BLE peripheral exposing GATT services.
Uses the bleak library for cross platform BLE communication.

Requirements:
    pip install bleak
"""

from khp import Driver, readable, writable, procedure, safety, monitor
from khp.core import ConnectionType
from typing import Dict, Optional, List
import asyncio
import struct


STANDARD_UUIDS = {
    "heart_rate": "00002a37-0000-1000-8000-00805f9b34fb",
    "temperature": "00002a6e-0000-1000-8000-00805f9b34fb",
    "humidity": "00002a6f-0000-1000-8000-00805f9b34fb",
    "battery_level": "00002a19-0000-1000-8000-00805f9b34fb",
    "device_name": "00002a00-0000-1000-8000-00805f9b34fb",
    "firmware_revision": "00002a26-0000-1000-8000-00805f9b34fb",
    "manufacturer_name": "00002a29-0000-1000-8000-00805f9b34fb",
}


class BLEDevice(Driver):
    """Bluetooth Low Energy device driver via GATT characteristics.

    Connects to BLE peripherals using their MAC address or device name.
    Reads and writes GATT characteristics for sensor data, actuator control,
    and device configuration. Supports notifications for real time data.
    """

    name = "BLE Device"
    version = "1.0.0"
    device_type = "ble_sensor"
    description = "Bluetooth Low Energy device via GATT (sensors, wearables, IoT)"
    connection_type = ConnectionType.SDK

    def __init__(self, device_id: str = None, address: str = None,
                 name_filter: str = None, scan_timeout: float = 10.0,
                 custom_characteristics: Dict[str, str] = None, **config):
        super().__init__(device_id=device_id, address=address, **config)
        self._address = address
        self._name_filter = name_filter
        self._scan_timeout = scan_timeout
        self._custom_chars = custom_characteristics or {}
        self._client = None
        self._notification_data: Dict[str, bytes] = {}
        self._connected_device_name = ""
        self._rssi = 0

    async def connect(self):
        from bleak import BleakClient, BleakScanner

        if not self._address and self._name_filter:
            device = await BleakScanner.find_device_by_name(
                self._name_filter, timeout=self._scan_timeout
            )
            if device is None:
                from khp.errors import DeviceOfflineError
                raise DeviceOfflineError(
                    f"BLE device with name '{self._name_filter}' not found",
                    device_id=self.device_id,
                )
            self._address = device.address
            self._connected_device_name = device.name or ""
            self._rssi = device.rssi or 0

        if not self._address:
            from khp.errors import ConnectionFailedError
            raise ConnectionFailedError(
                "No BLE address or name filter provided",
                device_id=self.device_id,
            )

        self._client = BleakClient(self._address)
        try:
            await self._client.connect()
        except Exception as e:
            from khp.errors import ConnectionFailedError
            raise ConnectionFailedError(
                f"Failed to connect to BLE device at {self._address}: {e}",
                device_id=self.device_id,
            )

        await super().connect()

    async def disconnect(self):
        if self._client and self._client.is_connected:
            await self._client.disconnect()
        self._client = None
        await super().disconnect()

    async def _read_characteristic(self, uuid: str) -> Optional[bytes]:
        """Read raw bytes from a GATT characteristic."""
        if not self._client or not self._client.is_connected:
            return None
        try:
            return await self._client.read_gatt_char(uuid)
        except Exception:
            return None

    async def _write_characteristic(self, uuid: str, data: bytes, with_response: bool = True):
        """Write raw bytes to a GATT characteristic."""
        if not self._client or not self._client.is_connected:
            from khp.errors import DeviceOfflineError
            raise DeviceOfflineError(
                "BLE device not connected",
                device_id=self.device_id,
            )
        await self._client.write_gatt_char(uuid, data, response=with_response)

    @readable(type="int", description="Heart rate measurement in BPM", unit="bpm")
    def heart_rate(self) -> int:
        data = asyncio.get_event_loop().run_until_complete(
            self._read_characteristic(STANDARD_UUIDS["heart_rate"])
        )
        if data and len(data) >= 2:
            flags = data[0]
            if flags & 0x01:
                return struct.unpack("<H", data[1:3])[0]
            return data[1]
        return 0

    @readable(type="float", description="Temperature reading from BLE sensor", unit="celsius")
    def temperature(self) -> float:
        data = asyncio.get_event_loop().run_until_complete(
            self._read_characteristic(STANDARD_UUIDS["temperature"])
        )
        if data and len(data) >= 2:
            raw = struct.unpack("<h", data[:2])[0]
            return raw / 100.0
        return 0.0

    @readable(type="float", description="Humidity reading from BLE sensor", unit="percent")
    def humidity(self) -> float:
        data = asyncio.get_event_loop().run_until_complete(
            self._read_characteristic(STANDARD_UUIDS["humidity"])
        )
        if data and len(data) >= 2:
            raw = struct.unpack("<H", data[:2])[0]
            return raw / 100.0
        return 0.0

    @readable(type="int", description="Battery level percentage", unit="percent")
    def battery_level(self) -> int:
        data = asyncio.get_event_loop().run_until_complete(
            self._read_characteristic(STANDARD_UUIDS["battery_level"])
        )
        if data and len(data) >= 1:
            return data[0]
        return 0

    @readable(type="int", description="Signal strength (RSSI) at connection time", unit="dBm")
    def signal_strength(self) -> int:
        return self._rssi

    @readable(type="object", description="All discovered GATT services and characteristics")
    def services(self) -> dict:
        if not self._client or not self._client.is_connected:
            return {}
        result = {}
        for service in self._client.services:
            chars = []
            for char in service.characteristics:
                chars.append({
                    "uuid": char.uuid,
                    "properties": char.properties,
                    "description": char.description or "",
                })
            result[service.uuid] = {
                "description": service.description or "",
                "characteristics": chars,
            }
        return result

    @safety(min=0, max=255, reason="LED color channels are 0 to 255", hard=True)
    @writable(type="int", description="Set LED red channel (0 to 255)")
    def led_red(self, value: int):
        uuid = self._custom_chars.get("led_color", "0000ff01-0000-1000-8000-00805f9b34fb")
        current = asyncio.get_event_loop().run_until_complete(
            self._read_characteristic(uuid)
        ) or b"\x00\x00\x00"
        new_data = bytes([value]) + current[1:3]
        asyncio.get_event_loop().run_until_complete(
            self._write_characteristic(uuid, new_data)
        )

    @safety(min=0, max=255, reason="LED color channels are 0 to 255", hard=True)
    @writable(type="int", description="Set LED green channel (0 to 255)")
    def led_green(self, value: int):
        uuid = self._custom_chars.get("led_color", "0000ff01-0000-1000-8000-00805f9b34fb")
        current = asyncio.get_event_loop().run_until_complete(
            self._read_characteristic(uuid)
        ) or b"\x00\x00\x00"
        new_data = current[0:1] + bytes([value]) + current[2:3]
        asyncio.get_event_loop().run_until_complete(
            self._write_characteristic(uuid, new_data)
        )

    @safety(min=0, max=255, reason="LED color channels are 0 to 255", hard=True)
    @writable(type="int", description="Set LED blue channel (0 to 255)")
    def led_blue(self, value: int):
        uuid = self._custom_chars.get("led_color", "0000ff01-0000-1000-8000-00805f9b34fb")
        current = asyncio.get_event_loop().run_until_complete(
            self._read_characteristic(uuid)
        ) or b"\x00\x00\x00"
        new_data = current[0:2] + bytes([value])
        asyncio.get_event_loop().run_until_complete(
            self._write_characteristic(uuid, new_data)
        )

    @safety(min=0, max=100, reason="Motor speed limited to 100 percent", hard=True)
    @writable(type="int", description="Set motor speed (0 to 100 percent)", unit="percent")
    def motor_speed(self, value: int):
        uuid = self._custom_chars.get("motor", "0000ff02-0000-1000-8000-00805f9b34fb")
        asyncio.get_event_loop().run_until_complete(
            self._write_characteristic(uuid, bytes([value]))
        )

    @writable(type="string", description="Set display text on BLE device screen")
    def display_text(self, value: str):
        uuid = self._custom_chars.get("display", "0000ff03-0000-1000-8000-00805f9b34fb")
        encoded = value.encode("utf-8")[:20]
        asyncio.get_event_loop().run_until_complete(
            self._write_characteristic(uuid, encoded)
        )

    @procedure(description="Scan for nearby BLE devices", estimated_duration_s=10.0)
    def scan_devices(self, timeout: float = 5.0) -> dict:
        """Discover BLE peripherals in range."""
        from bleak import BleakScanner

        async def _scan():
            devices = await BleakScanner.discover(timeout=timeout)
            return [
                {
                    "address": d.address,
                    "name": d.name or "Unknown",
                    "rssi": d.rssi,
                }
                for d in devices
            ]

        found = asyncio.get_event_loop().run_until_complete(_scan())
        return {"devices": found, "count": len(found), "status": "completed"}

    @procedure(description="Enable notifications on a characteristic UUID",
               estimated_duration_s=1.0)
    def enable_notifications(self, uuid: str = "") -> dict:
        """Start receiving push notifications from a BLE characteristic."""
        if not uuid:
            return {"status": "failed", "reason": "UUID required"}

        def _handler(sender, data):
            self._notification_data[uuid] = data

        async def _subscribe():
            await self._client.start_notify(uuid, _handler)

        asyncio.get_event_loop().run_until_complete(_subscribe())
        return {"status": "completed", "uuid": uuid, "notifications": "enabled"}

    @procedure(description="Read a custom characteristic by UUID",
               estimated_duration_s=1.0)
    def read_characteristic(self, uuid: str = "") -> dict:
        """Read raw bytes from any GATT characteristic by UUID."""
        if not uuid:
            return {"status": "failed", "reason": "UUID required"}
        data = asyncio.get_event_loop().run_until_complete(
            self._read_characteristic(uuid)
        )
        if data:
            return {"uuid": uuid, "raw": list(data), "hex": data.hex(), "status": "completed"}
        return {"uuid": uuid, "status": "failed", "reason": "Could not read characteristic"}

    @procedure(description="Firmware update over the air (requires confirmation)",
               estimated_duration_s=60.0, requires_confirmation=True)
    def firmware_update_ota(self, firmware_path: str = "", chunk_size: int = 20) -> dict:
        """Upload firmware to BLE device via DFU characteristic."""
        if not firmware_path:
            return {"status": "failed", "reason": "Firmware path required"}

        dfu_control = self._custom_chars.get(
            "dfu_control", "0000ff10-0000-1000-8000-00805f9b34fb"
        )
        dfu_data = self._custom_chars.get(
            "dfu_data", "0000ff11-0000-1000-8000-00805f9b34fb"
        )

        try:
            with open(firmware_path, "rb") as f:
                firmware = f.read()
        except FileNotFoundError:
            return {"status": "failed", "reason": "Firmware file not found"}

        async def _upload():
            await self._write_characteristic(dfu_control, b"\x01")
            chunks_sent = 0
            offset = 0
            while offset < len(firmware):
                chunk = firmware[offset:offset + chunk_size]
                await self._write_characteristic(dfu_data, chunk, with_response=False)
                offset += chunk_size
                chunks_sent += 1
            await self._write_characteristic(dfu_control, b"\x02")
            return chunks_sent

        chunks = asyncio.get_event_loop().run_until_complete(_upload())
        return {
            "status": "completed",
            "chunks_sent": chunks,
            "total_bytes": len(firmware),
        }

    @procedure(description="Calibrate sensor by writing calibration command",
               estimated_duration_s=5.0)
    def calibrate(self, sensor_type: str = "temperature", reference_value: float = 0.0) -> dict:
        """Send calibration reference value to the device."""
        cal_uuid = self._custom_chars.get(
            "calibration", "0000ff20-0000-1000-8000-00805f9b34fb"
        )
        payload = struct.pack("<Bf", sensor_type.encode()[0], reference_value)

        asyncio.get_event_loop().run_until_complete(
            self._write_characteristic(cal_uuid, payload)
        )
        return {
            "status": "completed",
            "sensor": sensor_type,
            "reference": reference_value,
        }
