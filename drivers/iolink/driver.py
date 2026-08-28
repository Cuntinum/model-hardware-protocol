"""KHP Driver: IO Link Smart Sensor/Actuator Interface.

Communicates with IO Link master devices via their REST API to access
smart sensors and actuators on IO Link ports. Supports ISDU (Indexed
Service Data Unit) reads and writes for parameterization, process data
exchange, and device identification.

Covers: proximity sensors, photoelectric sensors, pressure transmitters,
flow meters, temperature sensors, valve terminals, RFID readers,
IO Link hubs, and any IO Link v1.1 compatible device.

Requirements:
    pip install httpx
    (IO Link master must expose REST/JSON API, e.g. ifm AL1350, Balluff BNI)
"""
from __future__ import annotations

import time
import threading
from typing import Any

from khp import Driver, readable, writable, procedure, safety, monitor
from khp.core import ConnectionType


class IOLinkDevice(Driver):
    """IO Link driver for smart industrial sensors and actuators via master REST API."""

    name = "IO Link Smart Sensor"
    version = "1.0.0"
    device_type = "smart_sensor"
    description = "IO Link v1.1 device access via master REST API (ifm, Balluff, Siemens)"
    connection_type = ConnectionType.REST

    def __init__(self, device_id: str | None = None, master_url: str = "http://192.168.0.100",
                 port_number: int = 1, polling_interval_ms: int = 100, **config):
        super().__init__(device_id=device_id, master_url=master_url,
                         port_number=port_number, **config)
        self._master_url = master_url.rstrip("/")
        self._port_number = port_number
        self._polling_interval_ms = polling_interval_ms
        self._client = None
        self._lock = threading.Lock()

        self._vendor_id = 0
        self._device_id_code = 0
        self._product_name = ""
        self._serial_number = ""
        self._firmware_version = ""
        self._process_data: bytes = b""
        self._process_data_in: dict = {}
        self._port_status = "disconnected"
        self._communication_quality = 0
        self._cycle_counter = 0
        self._last_error = ""
        self._parameters: dict[int, Any] = {}
        self._events: list[dict] = []

    async def connect(self):
        """Connect to IO Link master and identify device on port."""
        try:
            import httpx
            self._client = httpx.Client(base_url=self._master_url, timeout=10.0)

            status_resp = self._client.get(f"/iolinkmaster/port[{self._port_number}]/iolinkdevice/status")
            if status_resp.status_code != 200:
                from khp.errors import DeviceOfflineError
                raise DeviceOfflineError(
                    f"IO Link master not responding at {self._master_url}",
                    device_id=self.device_id,
                )

            status_data = status_resp.json()
            self._port_status = status_data.get("port_status", "unknown")

            if self._port_status not in ("OPERATE", "operate", "DI", "di"):
                from khp.errors import DeviceOfflineError
                raise DeviceOfflineError(
                    f"No IO Link device on port {self._port_number} "
                    f"(status: {self._port_status})",
                    device_id=self.device_id,
                )

            ident_resp = self._client.get(
                f"/iolinkmaster/port[{self._port_number}]/iolinkdevice/ident"
            )
            if ident_resp.status_code == 200:
                ident = ident_resp.json()
                self._vendor_id = ident.get("vendor_id", 0)
                self._device_id_code = ident.get("device_id", 0)
                self._product_name = ident.get("product_name", "Unknown")
                self._serial_number = ident.get("serial_number", "")
                self._firmware_version = ident.get("firmware_version", "")
                self.name = f"IO Link: {self._product_name}"

            await super().connect()

        except ImportError:
            from khp.errors import DriverLoadError
            raise DriverLoadError(
                "httpx not installed. Install with: pip install httpx",
                device_id=self.device_id,
            )

    async def disconnect(self):
        """Close connection to IO Link master."""
        if self._client:
            self._client.close()
            self._client = None
        self._port_status = "disconnected"
        await super().disconnect()

    def _ensure_connected(self):
        if not self._client:
            from khp.errors import DeviceOfflineError
            raise DeviceOfflineError("Not connected to IO Link master", device_id=self.device_id)

    def _read_isdu(self, index: int, subindex: int = 0) -> bytes:
        """Read ISDU parameter from IO Link device."""
        self._ensure_connected()
        resp = self._client.get(
            f"/iolinkmaster/port[{self._port_number}]/iolinkdevice/isdu/"
            f"index/{index}/subindex/{subindex}"
        )
        if resp.status_code != 200:
            from khp.errors import PropertyNotFoundError
            raise PropertyNotFoundError(
                f"ISDU index {index}/{subindex}", self.device_id
            )
        data = resp.json()
        return bytes.fromhex(data.get("data", ""))

    def _write_isdu(self, index: int, subindex: int, data: bytes):
        """Write ISDU parameter to IO Link device."""
        self._ensure_connected()
        resp = self._client.put(
            f"/iolinkmaster/port[{self._port_number}]/iolinkdevice/isdu/"
            f"index/{index}/subindex/{subindex}",
            json={"data": data.hex()}
        )
        if resp.status_code != 200:
            self._last_error = f"ISDU write failed: {resp.status_code}"
            from khp.errors import ConnectionFailedError
            raise ConnectionFailedError(self._last_error, device_id=self.device_id)

    @readable(type="dict", description="Device identification (vendor, product, serial, firmware)")
    def device_identity(self) -> dict:
        return {
            "vendor_id": self._vendor_id,
            "device_id": self._device_id_code,
            "product_name": self._product_name,
            "serial_number": self._serial_number,
            "firmware_version": self._firmware_version,
        }

    @readable(type="str", description="Current port operating status")
    def port_status(self) -> str:
        self._ensure_connected()
        resp = self._client.get(
            f"/iolinkmaster/port[{self._port_number}]/iolinkdevice/status"
        )
        if resp.status_code == 200:
            self._port_status = resp.json().get("port_status", "unknown")
        return self._port_status

    @readable(type="dict", description="Current process data from the sensor (raw and parsed)")
    def process_data(self) -> dict:
        self._ensure_connected()
        resp = self._client.get(
            f"/iolinkmaster/port[{self._port_number}]/iolinkdevice/pdin"
        )
        if resp.status_code == 200:
            data = resp.json()
            self._process_data = bytes.fromhex(data.get("data", ""))
            self._cycle_counter += 1
        return {
            "raw_hex": self._process_data.hex(),
            "length_bytes": len(self._process_data),
            "raw_bytes": list(self._process_data),
            "cycle": self._cycle_counter,
        }

    @readable(type="int", description="Communication quality percentage (0 to 100)", unit="percent")
    def communication_quality(self) -> int:
        self._ensure_connected()
        resp = self._client.get(
            f"/iolinkmaster/port[{self._port_number}]/quality"
        )
        if resp.status_code == 200:
            self._communication_quality = resp.json().get("quality", 0)
        return self._communication_quality

    @readable(type="dict", description="Diagnostic information from the device")
    def diagnostics(self) -> dict:
        self._ensure_connected()
        resp = self._client.get(
            f"/iolinkmaster/port[{self._port_number}]/iolinkdevice/diag"
        )
        if resp.status_code == 200:
            return resp.json()
        return {"error": "Cannot read diagnostics", "status_code": resp.status_code}

    @readable(type="int", description="Total process data exchange cycles", unit="count")
    def cycle_count(self) -> int:
        return self._cycle_counter

    @readable(type="list", description="Recent device events (alarms, warnings)")
    def recent_events(self) -> list:
        self._ensure_connected()
        resp = self._client.get(
            f"/iolinkmaster/port[{self._port_number}]/iolinkdevice/events"
        )
        if resp.status_code == 200:
            self._events = resp.json().get("events", [])
        return self._events[-20:]

    @writable(type="bytes", description="Write process data output to the actuator")
    def process_data_out(self, value: str):
        """Write process data (hex string) to the device output."""
        self._ensure_connected()
        data_bytes = bytes.fromhex(value) if isinstance(value, str) else value
        resp = self._client.put(
            f"/iolinkmaster/port[{self._port_number}]/iolinkdevice/pdout",
            json={"data": data_bytes.hex()}
        )
        if resp.status_code != 200:
            from khp.errors import ConnectionFailedError
            raise ConnectionFailedError(
                f"Process data write failed: {resp.status_code}",
                device_id=self.device_id,
            )

    @writable(type="dict", description="Write an ISDU parameter by index and subindex")
    def write_parameter(self, config: dict):
        """Config: {index: int, subindex: int, data_hex: str}."""
        index = int(config.get("index", 0))
        subindex = int(config.get("subindex", 0))
        data_hex = config.get("data_hex", "")
        self._write_isdu(index, subindex, bytes.fromhex(data_hex))
        self._parameters[index] = data_hex

    @procedure(description="Read an ISDU parameter from the device by index")
    def read_parameter(self, index: int = 0, subindex: int = 0):
        raw = self._read_isdu(index, subindex)
        return {
            "index": index,
            "subindex": subindex,
            "data_hex": raw.hex(),
            "data_bytes": list(raw),
            "length": len(raw),
        }

    @procedure(description="Scan all ports on the IO Link master for connected devices")
    def port_scan(self, max_ports: int = 8):
        self._ensure_connected()
        results = []
        for port in range(1, max_ports + 1):
            resp = self._client.get(
                f"/iolinkmaster/port[{port}]/iolinkdevice/status"
            )
            if resp.status_code == 200:
                status = resp.json()
                port_info = {"port": port, "status": status.get("port_status", "empty")}
                if port_info["status"] in ("OPERATE", "operate"):
                    ident_resp = self._client.get(
                        f"/iolinkmaster/port[{port}]/iolinkdevice/ident"
                    )
                    if ident_resp.status_code == 200:
                        ident = ident_resp.json()
                        port_info["product_name"] = ident.get("product_name", "")
                        port_info["vendor_id"] = ident.get("vendor_id", 0)
                results.append(port_info)
            else:
                results.append({"port": port, "status": "no_response"})
        return {"ports_scanned": max_ports, "devices": results}

    @procedure(description="Trigger teach in on a sensor (learn current state as reference)")
    def teach_in(self, teach_command: int = 1):
        """Write teach command to standard ISDU teach index (64)."""
        self._write_isdu(64, 0, bytes([teach_command]))
        time.sleep(1.0)
        result = self._read_isdu(64, 0)
        return {
            "status": "teach_complete",
            "teach_command": teach_command,
            "response": result.hex(),
        }

    @procedure(description="Reset the IO Link device to factory defaults",
               requires_confirmation=True)
    def device_reset(self):
        """Send system command to reset device parameters."""
        self._write_isdu(2, 0, bytes([0x82]))
        time.sleep(2.0)
        return {"status": "reset_sent", "note": "Device may need re-enumeration"}

    @procedure(description="Initiate firmware update on the device",
               requires_confirmation=True)
    def firmware_update(self, firmware_hex: str = ""):
        """Upload firmware block to device via ISDU bulk transfer."""
        if not firmware_hex:
            return {"error": "No firmware data provided"}

        firmware_bytes = bytes.fromhex(firmware_hex)
        chunk_size = 232
        chunks_sent = 0

        for i in range(0, len(firmware_bytes), chunk_size):
            chunk = firmware_bytes[i:i + chunk_size]
            self._write_isdu(0x1000 + chunks_sent, 0, chunk)
            chunks_sent += 1
            time.sleep(0.05)

        return {
            "status": "firmware_uploaded",
            "chunks_sent": chunks_sent,
            "total_bytes": len(firmware_bytes),
        }

    @procedure(description="Read device application specific tag (user label)")
    def read_application_tag(self):
        raw = self._read_isdu(24, 0)
        try:
            tag = raw.decode("utf-8").rstrip("\x00")
        except UnicodeDecodeError:
            tag = raw.hex()
        return {"application_tag": tag, "raw_hex": raw.hex()}

    @procedure(description="Write device application specific tag (user label)")
    def write_application_tag(self, tag: str = ""):
        encoded = tag.encode("utf-8")[:32].ljust(32, b"\x00")
        self._write_isdu(24, 0, encoded)
        return {"status": "written", "tag": tag}

    @monitor(interval_ms=1000, description="Monitor communication quality and device health")
    def check_iolink_health(self) -> dict[str, Any]:
        alerts = []

        quality = self.communication_quality()
        if quality < 50:
            alerts.append({
                "level": "warning",
                "message": f"Low communication quality: {quality}%",
            })
        if quality == 0:
            alerts.append({
                "level": "critical",
                "message": "Communication lost with IO Link device",
            })

        status = self._port_status
        if status not in ("OPERATE", "operate"):
            alerts.append({
                "level": "critical",
                "message": f"Port not in operate mode (status: {status})",
            })

        return {
            "healthy": len(alerts) == 0,
            "port": self._port_number,
            "status": status,
            "quality": quality,
            "cycles": self._cycle_counter,
            "alerts": alerts,
        }
