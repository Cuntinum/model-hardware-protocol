"""KHP Driver: KNX Building Automation Integration.

Connects to KNX/IP gateways for complete building automation control.
Supports lighting, blinds, HVAC, scenes, energy metering, and any KNX
certified actuator or sensor. Uses KNXnet/IP tunneling protocol for
real time communication with the KNX bus.

Covers: ETS programmed installations, group addressing, datapoint types
(DPT), bus monitoring, and multi room scene control.

Requirements:
    pip install xknx
"""
from __future__ import annotations

import time
import asyncio
import struct
from typing import Any
from datetime import datetime, timezone

from khp import Driver, readable, writable, procedure, safety, monitor
from khp.core import ConnectionType


DPT_TYPES = {
    "switch": "1.001",
    "boolean": "1.002",
    "dimming": "3.007",
    "percentage": "5.001",
    "angle": "5.003",
    "temperature": "9.001",
    "humidity": "9.007",
    "lux": "9.004",
    "speed": "9.005",
    "pressure": "9.006",
    "power": "14.056",
    "energy": "13.010",
    "counter": "12.001",
    "time": "10.001",
    "date": "11.001",
    "scene": "17.001",
    "hvac_mode": "20.102",
}

HVAC_MODES = {
    0: "Auto",
    1: "Comfort",
    2: "Standby",
    3: "Economy",
    4: "Building Protection",
}


class KNXDevice(Driver):
    """KNX/IP building automation driver for comprehensive BMS control."""

    name = "KNX Building Controller"
    version = "1.0.0"
    device_type = "building_automation"
    description = "KNX/IP tunneling for lighting, HVAC, blinds, scenes, and energy metering"
    connection_type = ConnectionType.UDP

    def __init__(self, device_id: str | None = None, gateway_ip: str = "192.168.1.10",
                 gateway_port: int = 3671, local_ip: str | None = None,
                 rate_limit_per_sec: int = 50, **config):
        super().__init__(device_id=device_id, gateway_ip=gateway_ip, **config)
        self._gateway_ip = gateway_ip
        self._gateway_port = gateway_port
        self._local_ip = local_ip
        self._rate_limit = rate_limit_per_sec
        self._xknx = None
        self._connected = False
        self._group_cache: dict[str, Any] = {}
        self._telegram_count = 0
        self._last_telegrams: list[dict] = []
        self._scenes: dict[int, str] = {}
        self._room_temperatures: dict[str, float] = {}
        self._energy_counters: dict[str, float] = {}
        self._telegram_rate: float = 0.0
        self._last_rate_check = time.time()
        self._rate_count = 0

    async def connect(self):
        """Establish KNXnet/IP tunneling connection to the gateway."""
        try:
            from xknx import XKNX
            from xknx.io import ConnectionConfig, ConnectionType as XKNXConnType

            connection_config = ConnectionConfig(
                connection_type=XKNXConnType.TUNNELING,
                gateway_ip=self._gateway_ip,
                gateway_port=self._gateway_port,
                local_ip=self._local_ip,
            )

            self._xknx = XKNX(connection_config=connection_config)
            await self._xknx.start()

            if self._xknx.connected:
                self._connected = True
                await super().connect()
            else:
                from khp.errors import ConnectionFailedError
                raise ConnectionFailedError(
                    f"KNX gateway at {self._gateway_ip}:{self._gateway_port} not responding",
                    device_id=self.device_id,
                )

        except ImportError:
            from khp.errors import DriverLoadError
            raise DriverLoadError(
                "xknx not installed. Install with: pip install xknx",
                device_id=self.device_id,
            )

    async def disconnect(self):
        """Close KNXnet/IP tunnel."""
        if self._xknx:
            await self._xknx.stop()
            self._xknx = None
        self._connected = False
        await super().disconnect()

    def _ensure_connected(self):
        if not self._connected or not self._xknx:
            from khp.errors import DeviceOfflineError
            raise DeviceOfflineError("KNX gateway not connected", device_id=self.device_id)

    def _check_rate_limit(self):
        """Enforce telegram rate limiting to prevent bus overload."""
        now = time.time()
        elapsed = now - self._last_rate_check
        if elapsed >= 1.0:
            self._telegram_rate = self._rate_count / elapsed
            self._rate_count = 0
            self._last_rate_check = now
        elif self._rate_count >= self._rate_limit:
            from khp.errors import SafetyBlockedError
            raise SafetyBlockedError(
                f"Telegram rate limit exceeded ({self._rate_limit}/sec). "
                f"Throttling to prevent KNX bus overload.",
                device_id=self.device_id,
                property_name="telegram_rate",
                value=self._rate_count,
                limit=self._rate_limit,
            )
        self._rate_count += 1
        self._telegram_count += 1

    def _log_telegram(self, direction: str, group_address: str, value: Any, dpt: str = ""):
        entry = {
            "direction": direction,
            "group_address": group_address,
            "value": value,
            "dpt": dpt,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self._last_telegrams.append(entry)
        if len(self._last_telegrams) > 500:
            self._last_telegrams = self._last_telegrams[-250:]

    @readable(type="bool", description="Whether KNXnet/IP tunnel is established")
    def gateway_connected(self) -> bool:
        return self._connected

    @readable(type="int", description="Total telegrams sent this session", unit="count")
    def telegram_count(self) -> int:
        return self._telegram_count

    @readable(type="float", description="Current telegram send rate", unit="telegrams/sec")
    def telegram_rate(self) -> float:
        return self._telegram_rate

    @readable(type="dict", description="Cached group address values from recent reads")
    def group_values(self) -> dict:
        return self._group_cache

    @readable(type="dict", description="Room temperature readings from KNX sensors")
    def temperatures(self) -> dict:
        return self._room_temperatures

    @readable(type="dict", description="Energy counter readings from KNX meters")
    def energy_meters(self) -> dict:
        return self._energy_counters

    @safety(min=1, max=100, reason="Bus overload protection: max 100 telegrams per second", hard=True)
    @writable(type="int", description="Maximum telegrams per second rate limit", unit="telegrams/sec")
    def rate_limit(self, value: int):
        self._rate_limit = value

    @procedure(description="Send a boolean value to a group address (on/off, true/false)")
    def group_write_bool(self, group_address: str = "1/1/1", value: bool = True):
        """Write a DPT 1.x boolean value to a KNX group address."""
        self._ensure_connected()
        self._check_rate_limit()

        from xknx.core import ValueReader
        from xknx.dpt import DPTBinary
        from xknx.telegram import Telegram, GroupAddress
        from xknx.telegram.apci import GroupValueWrite

        telegram = Telegram(
            destination_address=GroupAddress(group_address),
            payload=GroupValueWrite(DPTBinary(1 if value else 0)),
        )
        asyncio.get_event_loop().run_until_complete(
            self._xknx.telegrams.put(telegram)
        )

        self._group_cache[group_address] = {"value": value, "dpt": "1.001"}
        self._log_telegram("write", group_address, value, "1.001")

        return {"group_address": group_address, "value": value, "dpt": "1.001", "status": "sent"}

    @procedure(description="Send a percentage value (0 to 100) to a group address for dimming or blinds")
    def group_write_percentage(self, group_address: str = "1/1/2", value: int = 50):
        """Write a DPT 5.001 percentage value (0 to 100)."""
        self._ensure_connected()
        self._check_rate_limit()

        if value < 0 or value > 100:
            from khp.errors import SafetyBlockedError
            raise SafetyBlockedError(
                "Percentage must be 0 to 100",
                device_id=self.device_id,
                property_name="percentage",
                value=value,
                limit=100,
            )

        from xknx.telegram import Telegram, GroupAddress
        from xknx.telegram.apci import GroupValueWrite
        from xknx.dpt import DPTArray

        raw_value = int(value * 255 / 100)
        telegram = Telegram(
            destination_address=GroupAddress(group_address),
            payload=GroupValueWrite(DPTArray(raw_value)),
        )
        asyncio.get_event_loop().run_until_complete(
            self._xknx.telegrams.put(telegram)
        )

        self._group_cache[group_address] = {"value": value, "dpt": "5.001"}
        self._log_telegram("write", group_address, value, "5.001")

        return {"group_address": group_address, "value": value, "dpt": "5.001", "status": "sent"}

    @procedure(description="Send a 2 byte float value (temperature, humidity) to a group address")
    def group_write_float(self, group_address: str = "1/2/1", value: float = 21.0,
                          dpt: str = "9.001"):
        """Write a DPT 9.x two byte float value."""
        self._ensure_connected()
        self._check_rate_limit()

        from xknx.telegram import Telegram, GroupAddress
        from xknx.telegram.apci import GroupValueWrite
        from xknx.dpt import DPTArray, DPTTemperature

        encoded = DPTTemperature.to_knx(value)
        telegram = Telegram(
            destination_address=GroupAddress(group_address),
            payload=GroupValueWrite(DPTArray(encoded)),
        )
        asyncio.get_event_loop().run_until_complete(
            self._xknx.telegrams.put(telegram)
        )

        self._group_cache[group_address] = {"value": value, "dpt": dpt}
        self._log_telegram("write", group_address, value, dpt)

        return {"group_address": group_address, "value": value, "dpt": dpt, "status": "sent"}

    @procedure(description="Read the current value from a group address")
    def group_read(self, group_address: str = "1/1/1"):
        """Send a GroupValueRead and return the cached response."""
        self._ensure_connected()
        self._check_rate_limit()

        from xknx.telegram import Telegram, GroupAddress
        from xknx.telegram.apci import GroupValueRead

        telegram = Telegram(
            destination_address=GroupAddress(group_address),
            payload=GroupValueRead(),
        )
        asyncio.get_event_loop().run_until_complete(
            self._xknx.telegrams.put(telegram)
        )

        self._log_telegram("read", group_address, None)
        cached = self._group_cache.get(group_address)
        return {"group_address": group_address, "cached_value": cached, "status": "read_request_sent"}

    @procedure(description="Control lighting: on/off plus optional dimming level")
    def control_light(self, switch_address: str = "1/1/1", dim_address: str = "",
                      on: bool = True, level: int = 100):
        """Combined light switch and dim control."""
        results = []
        results.append(self.group_write_bool(switch_address, on))
        if dim_address and on and level < 100:
            results.append(self.group_write_percentage(dim_address, level))
        return {"actions": results}

    @procedure(description="Control window blinds: position and optional slat angle")
    def control_blinds(self, position_address: str = "2/1/1", slat_address: str = "",
                       position: int = 0, slat_angle: int = 50):
        """Set blind position (0=open, 100=closed) and optional slat angle."""
        if position < 0 or position > 100:
            from khp.errors import SafetyBlockedError
            raise SafetyBlockedError(
                "Blind position must be 0 (open) to 100 (closed)",
                device_id=self.device_id,
                property_name="position",
                value=position,
                limit=100,
            )

        results = []
        results.append(self.group_write_percentage(position_address, position))
        if slat_address:
            results.append(self.group_write_percentage(slat_address, slat_angle))
        return {"actions": results}

    @procedure(description="Set HVAC mode for a room (comfort, standby, economy, protection)")
    def set_hvac_mode(self, mode_address: str = "3/1/1", mode: str = "comfort"):
        """Write HVAC operating mode (DPT 20.102)."""
        mode_map = {"auto": 0, "comfort": 1, "standby": 2, "economy": 3, "protection": 4}
        mode_val = mode_map.get(mode.lower(), 1)

        self._ensure_connected()
        self._check_rate_limit()

        from xknx.telegram import Telegram, GroupAddress
        from xknx.telegram.apci import GroupValueWrite
        from xknx.dpt import DPTArray

        telegram = Telegram(
            destination_address=GroupAddress(mode_address),
            payload=GroupValueWrite(DPTArray(mode_val)),
        )
        asyncio.get_event_loop().run_until_complete(
            self._xknx.telegrams.put(telegram)
        )

        self._group_cache[mode_address] = {"value": mode, "dpt": "20.102"}
        self._log_telegram("write", mode_address, mode, "20.102")

        return {"group_address": mode_address, "mode": mode, "mode_value": mode_val, "status": "sent"}

    @procedure(description="Activate a predefined KNX scene by number (0 to 63)")
    def activate_scene(self, scene_address: str = "1/7/1", scene_number: int = 0):
        """Trigger a KNX scene (DPT 17.001)."""
        if scene_number < 0 or scene_number > 63:
            from khp.errors import SafetyBlockedError
            raise SafetyBlockedError(
                "Scene number must be 0 to 63",
                device_id=self.device_id,
                property_name="scene_number",
                value=scene_number,
                limit=63,
            )

        self._ensure_connected()
        self._check_rate_limit()

        from xknx.telegram import Telegram, GroupAddress
        from xknx.telegram.apci import GroupValueWrite
        from xknx.dpt import DPTArray

        telegram = Telegram(
            destination_address=GroupAddress(scene_address),
            payload=GroupValueWrite(DPTArray(scene_number)),
        )
        asyncio.get_event_loop().run_until_complete(
            self._xknx.telegrams.put(telegram)
        )

        self._log_telegram("write", scene_address, scene_number, "17.001")
        return {"scene_address": scene_address, "scene_number": scene_number, "status": "activated"}

    @procedure(description="Read temperature from a KNX sensor and cache it per room")
    def read_temperature(self, group_address: str = "4/1/1", room_name: str = ""):
        """Read and cache a room temperature value."""
        result = self.group_read(group_address)
        cached = self._group_cache.get(group_address, {})
        temp = cached.get("value")
        if temp is not None and room_name:
            self._room_temperatures[room_name] = temp
        return {"group_address": group_address, "room": room_name, "temperature": temp}

    @procedure(description="Read energy meter counter value from a KNX meter")
    def read_energy_meter(self, group_address: str = "5/1/1", meter_name: str = ""):
        """Read energy counter (DPT 13.x) from a KNX energy meter."""
        result = self.group_read(group_address)
        cached = self._group_cache.get(group_address, {})
        energy = cached.get("value")
        if energy is not None and meter_name:
            self._energy_counters[meter_name] = energy
        return {"group_address": group_address, "meter": meter_name, "energy_kwh": energy}

    @procedure(description="Get recent bus telegrams for diagnostics")
    def get_bus_log(self, last_n: int = 50):
        """Return recent KNX bus telegrams."""
        entries = self._last_telegrams[-last_n:]
        return {"total_telegrams": self._telegram_count, "returned": len(entries), "telegrams": entries}

    @monitor(interval_ms=10000, description="Monitor KNX bus health, telegram rate, and gateway connection")
    def check_knx_health(self) -> dict[str, Any]:
        alerts = []

        if not self._connected:
            alerts.append({"level": "critical", "message": "KNX gateway disconnected"})

        if self._telegram_rate > self._rate_limit * 0.8:
            alerts.append({
                "level": "warning",
                "message": f"Telegram rate near limit: {self._telegram_rate:.1f}/{self._rate_limit} per sec",
            })

        return {
            "healthy": len(alerts) == 0,
            "gateway": f"{self._gateway_ip}:{self._gateway_port}",
            "connected": self._connected,
            "telegram_count": self._telegram_count,
            "telegram_rate": round(self._telegram_rate, 1),
            "cached_addresses": len(self._group_cache),
            "alerts": alerts,
        }
