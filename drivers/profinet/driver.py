"""KHP Driver: PROFINET Industrial Ethernet.

Communicates with Siemens S7 PLCs (S7-300, S7-400, S7-1200, S7-1500) and other
PROFINET IO devices using the S7 communication protocol over TCP/IP.
Supports reading/writing data blocks (DB), inputs (I), outputs (Q), markers (M),
timers, and counters. Provides structured access to PLC memory areas.

Covers: Siemens SIMATIC PLCs, PROFINET IO controllers, distributed I/O (ET 200),
HMI panels, variable frequency drives (SINAMICS), motion control (SIMOTION),
and any S7 compatible device.

Requirements:
    pip install python-snap7
    (Requires snap7 shared library installed on the system)
"""
from __future__ import annotations

import time
import struct
import threading
from typing import Any

from khp import Driver, readable, writable, procedure, safety, monitor
from khp.core import ConnectionType


AREA_CODES = {
    "DB": 0x84,
    "M": 0x83,
    "I": 0x81,
    "Q": 0x82,
    "T": 0x1D,
    "C": 0x1C,
}

DATA_TYPES = {
    "bool": (1, "?"),
    "int8": (1, "b"),
    "uint8": (1, "B"),
    "int16": (2, ">h"),
    "uint16": (2, ">H"),
    "int32": (4, ">i"),
    "uint32": (4, ">I"),
    "float32": (4, ">f"),
    "float64": (8, ">d"),
}


class PROFINETDevice(Driver):
    """PROFINET/S7 driver for Siemens PLCs and compatible industrial controllers."""

    name = "PROFINET S7 Controller"
    version = "1.0.0"
    device_type = "plc"
    description = "Siemens S7 PLC communication via PROFINET (TCP/ISO on TCP)"
    connection_type = ConnectionType.TCP

    def __init__(self, device_id: str | None = None, ip_address: str = "192.168.0.1",
                 rack: int = 0, slot: int = 1, port: int = 102,
                 plc_type: str = "s7-1500", **config):
        super().__init__(device_id=device_id, ip_address=ip_address,
                         rack=rack, slot=slot, **config)
        self._ip_address = ip_address
        self._rack = rack
        self._slot = slot
        self._port = port
        self._plc_type = plc_type
        self._client = None
        self._lock = threading.Lock()

        self._cpu_state = "unknown"
        self._last_error = ""
        self._db_cache: dict[str, Any] = {}
        self._monitored_addresses: list[dict] = []
        self._cycle_count = 0

    async def connect(self):
        """Establish S7 connection to the PLC."""
        try:
            import snap7
            from snap7.util import get_bool, get_int, get_real

            self._client = snap7.client.Client()
            self._client.set_connection_type(3)

            result = self._client.connect(self._ip_address, self._rack, self._slot, self._port)
            if not self._client.get_connected():
                from khp.errors import DeviceOfflineError
                raise DeviceOfflineError(
                    f"Cannot connect to PLC at {self._ip_address}:{self._port} "
                    f"(rack={self._rack}, slot={self._slot})",
                    device_id=self.device_id,
                )

            cpu_info = self._client.get_cpu_info()
            self._cpu_state = "run"
            self.name = f"S7 PLC ({cpu_info.ModuleTypeName.decode().strip()})"
            await super().connect()

        except ImportError:
            from khp.errors import DriverLoadError
            raise DriverLoadError(
                "python-snap7 not installed. Install with: pip install python-snap7",
                device_id=self.device_id,
            )
        except Exception as e:
            if "snap7" not in str(type(e).__module__):
                raise
            from khp.errors import ConnectionFailedError
            raise ConnectionFailedError(
                f"S7 connection failed: {e}",
                device_id=self.device_id,
            )

    async def disconnect(self):
        """Close S7 connection."""
        if self._client:
            self._client.disconnect()
            self._client.destroy()
            self._client = None
        self._cpu_state = "unknown"
        await super().disconnect()

    def _ensure_connected(self):
        if not self._client or not self._client.get_connected():
            from khp.errors import DeviceOfflineError
            raise DeviceOfflineError(
                "PLC not connected", device_id=self.device_id
            )

    def _read_area(self, area: str, db_number: int, start: int, size: int) -> bytes:
        """Read raw bytes from a PLC memory area."""
        self._ensure_connected()
        area_code = AREA_CODES.get(area.upper(), 0x84)
        with self._lock:
            data = self._client.read_area(area_code, db_number, start, size)
        return bytes(data)

    def _write_area(self, area: str, db_number: int, start: int, data: bytes):
        """Write raw bytes to a PLC memory area."""
        self._ensure_connected()
        area_code = AREA_CODES.get(area.upper(), 0x84)
        with self._lock:
            self._client.write_area(area_code, db_number, start, bytearray(data))

    @readable(type="str", description="Current CPU operating state (run, stop, unknown)")
    def cpu_state(self) -> str:
        self._ensure_connected()
        try:
            state = self._client.get_cpu_state()
            state_map = {"S7CpuStatusRun": "run", "S7CpuStatusStop": "stop"}
            self._cpu_state = state_map.get(state, "unknown")
        except Exception:
            self._cpu_state = "unknown"
        return self._cpu_state

    @readable(type="dict", description="PLC CPU hardware and firmware information")
    def cpu_info(self) -> dict:
        self._ensure_connected()
        info = self._client.get_cpu_info()
        return {
            "module_type": info.ModuleTypeName.decode().strip(),
            "serial_number": info.SerialNumber.decode().strip(),
            "as_name": info.ASName.decode().strip(),
            "module_name": info.ModuleName.decode().strip(),
        }

    @readable(type="dict", description="PLC communication diagnostics and statistics")
    def plc_diagnostics(self) -> dict:
        self._ensure_connected()
        try:
            order_code = self._client.get_order_code()
            return {
                "connected": self._client.get_connected(),
                "order_code": order_code.OrderCode.decode().strip(),
                "plc_type": self._plc_type,
                "ip_address": self._ip_address,
                "rack": self._rack,
                "slot": self._slot,
                "cycle_count": self._cycle_count,
            }
        except Exception as e:
            return {"connected": False, "error": str(e)}

    @readable(type="int", description="Number of read/write operations performed", unit="count")
    def operation_count(self) -> int:
        return self._cycle_count

    @writable(type="dict", description="Write a typed value to a PLC address (area, db, offset, type, value)")
    def write_address(self, config: dict):
        """Write to PLC. Config: {area, db_number, offset, data_type, value}."""
        area = config.get("area", "DB")
        db_number = int(config.get("db_number", 1))
        offset = int(config.get("offset", 0))
        data_type = config.get("data_type", "int16")
        value = config.get("value", 0)

        if data_type not in DATA_TYPES:
            from khp.errors import SafetyBlockedError
            raise SafetyBlockedError(
                f"Unsupported data type: {data_type}. "
                f"Supported: {list(DATA_TYPES.keys())}",
                device_id=self.device_id,
                property_name="data_type",
                value=data_type,
                limit=None,
            )

        size, fmt = DATA_TYPES[data_type]

        if data_type == "bool":
            bit_offset = int(config.get("bit", 0))
            current = self._read_area(area, db_number, offset, 1)
            byte_val = current[0]
            if value:
                byte_val |= (1 << bit_offset)
            else:
                byte_val &= ~(1 << bit_offset)
            self._write_area(area, db_number, offset, bytes([byte_val]))
        else:
            packed = struct.pack(fmt, value)
            self._write_area(area, db_number, offset, packed)

        self._cycle_count += 1

    @procedure(description="Read a typed value from any PLC memory address")
    def read_address(self, area: str = "DB", db_number: int = 1,
                     offset: int = 0, data_type: str = "int16", bit: int = 0):
        """Read a value from PLC memory. Returns typed result."""
        if data_type not in DATA_TYPES:
            return {"error": f"Unsupported type: {data_type}", "supported": list(DATA_TYPES.keys())}

        size, fmt = DATA_TYPES[data_type]

        if data_type == "bool":
            raw = self._read_area(area, db_number, offset, 1)
            value = bool(raw[0] & (1 << bit))
        else:
            raw = self._read_area(area, db_number, offset, size)
            value = struct.unpack(fmt, raw)[0]

        self._cycle_count += 1
        return {
            "address": f"{area}{db_number}.DBW{offset}" if area == "DB" else f"{area}{offset}",
            "data_type": data_type,
            "value": value,
            "raw_hex": raw.hex(),
        }

    @procedure(description="Read an entire data block (DB) from the PLC")
    def read_data_block(self, db_number: int = 1, start: int = 0, size: int = 256):
        """Read a contiguous block of bytes from a data block."""
        if size > 65536:
            from khp.errors import SafetyBlockedError
            raise SafetyBlockedError(
                "Maximum read size is 65536 bytes",
                device_id=self.device_id,
                property_name="size",
                value=size,
                limit={"max": 65536},
            )

        raw = self._read_area("DB", db_number, start, size)
        self._cycle_count += 1
        return {
            "db_number": db_number,
            "start": start,
            "size": len(raw),
            "data_hex": raw.hex(),
            "data_bytes": list(raw),
        }

    @procedure(description="Write raw bytes to a data block")
    def write_data_block(self, db_number: int = 1, start: int = 0, data_hex: str = ""):
        """Write raw hex bytes to a data block. Use with caution."""
        data = bytes.fromhex(data_hex)
        if len(data) > 65536:
            from khp.errors import SafetyBlockedError
            raise SafetyBlockedError(
                "Maximum write size is 65536 bytes",
                device_id=self.device_id,
                property_name="size",
                value=len(data),
                limit={"max": 65536},
            )

        self._write_area("DB", db_number, start, data)
        self._cycle_count += 1
        return {
            "status": "written",
            "db_number": db_number,
            "start": start,
            "bytes_written": len(data),
        }

    @procedure(description="Read multiple addresses in a single batch operation")
    def batch_read(self, addresses: list[dict] = None):
        """Read multiple addresses efficiently.
        Each address: {area, db_number, offset, data_type, bit?}"""
        if not addresses:
            return {"results": [], "error": "No addresses provided"}

        results = []
        for addr in addresses:
            try:
                result = self.read_address(
                    area=addr.get("area", "DB"),
                    db_number=int(addr.get("db_number", 1)),
                    offset=int(addr.get("offset", 0)),
                    data_type=addr.get("data_type", "int16"),
                    bit=int(addr.get("bit", 0)),
                )
                results.append(result)
            except Exception as e:
                results.append({"error": str(e), "address": addr})

        return {"results": results, "count": len(results)}

    @procedure(description="Set PLC CPU to RUN mode", requires_confirmation=True)
    def cpu_start(self):
        """Start the PLC CPU (transition from STOP to RUN)."""
        self._ensure_connected()
        self._client.plc_hot_start()
        time.sleep(0.5)
        self._cpu_state = "run"
        return {"status": "started", "state": self.cpu_state()}

    @procedure(description="Set PLC CPU to STOP mode (halts program execution)",
               requires_confirmation=True)
    def cpu_stop(self):
        """Stop the PLC CPU (halts all outputs and program execution)."""
        self._ensure_connected()
        self._client.plc_stop()
        time.sleep(0.5)
        self._cpu_state = "stop"
        return {"status": "stopped", "state": self.cpu_state()}

    @procedure(description="List all data blocks available in the PLC")
    def list_data_blocks(self):
        """Enumerate all data blocks in the PLC."""
        self._ensure_connected()
        try:
            block_list = self._client.list_blocks()
            return {
                "ob_count": block_list.OBCount,
                "fb_count": block_list.FBCount,
                "fc_count": block_list.FCCount,
                "sdb_count": block_list.SDBCount,
                "db_count": block_list.DBCount,
            }
        except Exception as e:
            return {"error": str(e)}

    @procedure(description="Get detailed information about a specific block")
    def block_info(self, block_type: str = "DB", block_number: int = 1):
        """Get info about a specific block (DB, OB, FB, FC)."""
        self._ensure_connected()
        type_map = {"DB": 0x41, "OB": 0x38, "FB": 0x42, "FC": 0x43}
        block_code = type_map.get(block_type.upper(), 0x41)

        try:
            import snap7
            info = self._client.get_block_info(block_code, block_number)
            return {
                "block_type": block_type,
                "block_number": block_number,
                "size": info.mc7_size,
                "load_size": info.load_size,
                "author": info.Author.decode().strip(),
                "family": info.Family.decode().strip(),
                "header": info.Header.decode().strip(),
            }
        except Exception as e:
            return {"error": str(e), "block_type": block_type, "block_number": block_number}

    @procedure(description="Read PLC date and time")
    def read_plc_time(self):
        """Read the real time clock from the PLC."""
        self._ensure_connected()
        try:
            plc_time = self._client.get_plc_datetime()
            return {
                "datetime": plc_time.isoformat(),
                "year": plc_time.year,
                "month": plc_time.month,
                "day": plc_time.day,
                "hour": plc_time.hour,
                "minute": plc_time.minute,
                "second": plc_time.second,
            }
        except Exception as e:
            return {"error": str(e)}

    @procedure(description="Compress PLC memory (garbage collection)")
    def compress_memory(self):
        """Trigger memory compaction on the PLC."""
        self._ensure_connected()
        self._client.compress(self._client.get_param(2))
        return {"status": "compressed"}

    @procedure(description="Copy a data block from PLC to local buffer")
    def upload_block(self, block_type: str = "DB", block_number: int = 1):
        """Upload (read) an entire block from PLC."""
        self._ensure_connected()
        type_map = {"DB": 0x41, "OB": 0x38, "FB": 0x42, "FC": 0x43}
        block_code = type_map.get(block_type.upper(), 0x41)

        try:
            data = self._client.full_upload(block_code, block_number)
            return {
                "block_type": block_type,
                "block_number": block_number,
                "size": len(data),
                "data_hex": bytes(data).hex()[:200] + ("..." if len(data) > 100 else ""),
            }
        except Exception as e:
            return {"error": str(e)}

    @monitor(interval_ms=500, description="Monitor PLC health and connectivity")
    def check_plc_health(self) -> dict[str, Any]:
        alerts = []

        if not self._client or not self._client.get_connected():
            alerts.append({
                "level": "critical",
                "message": "Lost connection to PLC",
            })
            return {"healthy": False, "alerts": alerts}

        try:
            state = self._client.get_cpu_state()
            if "Stop" in state:
                alerts.append({
                    "level": "warning",
                    "message": "PLC CPU is in STOP mode",
                })
        except Exception as e:
            alerts.append({
                "level": "critical",
                "message": f"Cannot read CPU state: {e}",
            })

        return {
            "healthy": len(alerts) == 0,
            "cpu_state": self._cpu_state,
            "ip_address": self._ip_address,
            "cycle_count": self._cycle_count,
            "alerts": alerts,
        }
