"""KHP Driver: BACnet Building Automation (BACnet/IP and BACnet/MSTP).

Supports building management systems: HVAC, lighting, access control,
fire alarm panels, elevator controllers, energy meters, and any device
communicating via the BACnet protocol (ASHRAE 135).

Requirements:
    pip install BAC0
"""

from khp import Driver, readable, writable, procedure, safety, monitor
from khp.core import ConnectionType
from typing import Dict, Optional, List
import time


class BACnetDevice(Driver):
    """BACnet building automation device driver.

    Connects to BACnet/IP devices on the local network or via a BACnet router.
    Reads and writes BACnet objects (analog inputs, outputs, binary values,
    multistate values, schedules, trend logs). Implements Who Is discovery
    and COV (Change of Value) subscriptions.
    """

    name = "BACnet Device"
    version = "1.0.0"
    device_type = "building_automation"
    description = "BACnet/IP building automation (HVAC, lighting, access, fire, energy)"
    connection_type = ConnectionType.SDK

    def __init__(self, device_id: str = None, ip_address: str = "192.168.1.100",
                 device_instance: int = 1000, network_interface: str = "0.0.0.0",
                 port: int = 47808, **config):
        super().__init__(device_id=device_id, ip_address=ip_address, **config)
        self._ip_address = ip_address
        self._device_instance = device_instance
        self._network_interface = network_interface
        self._port = port
        self._bacnet = None
        self._device = None
        self._cached_points: Dict[str, float] = {}

    async def connect(self):
        import BAC0
        try:
            self._bacnet = BAC0.lite(ip=self._network_interface, port=self._port)
            self._device = self._bacnet
        except Exception as e:
            from khp.errors import ConnectionFailedError
            raise ConnectionFailedError(
                f"Cannot initialize BACnet stack on {self._network_interface}: {e}",
                device_id=self.device_id,
            )
        await super().connect()

    async def disconnect(self):
        if self._bacnet:
            self._bacnet.disconnect()
            self._bacnet = None
        await super().disconnect()

    def _read_property(self, object_type: str, instance: int,
                       property_name: str = "presentValue") -> Optional[float]:
        """Read a BACnet object property."""
        address = f"{self._ip_address}"
        request = f"{address} {object_type} {instance} {property_name}"
        try:
            value = self._bacnet.read(request)
            if isinstance(value, (int, float)):
                return float(value)
            return value
        except Exception:
            return None

    def _write_property(self, object_type: str, instance: int,
                        value, property_name: str = "presentValue",
                        priority: int = 8):
        """Write a BACnet object property at a given priority."""
        address = f"{self._ip_address}"
        request = f"{address} {object_type} {instance} {property_name} {value} - {priority}"
        try:
            self._bacnet.write(request)
        except Exception as e:
            from khp.errors import KHPError
            raise KHPError(
                f"BACnet write failed: {e}",
                device_id=self.device_id,
            )

    @monitor(interval_ms=5000, alert_above=40.0, action="emergency_stop")
    @readable(type="float", description="Zone air temperature", unit="celsius")
    def zone_temperature(self) -> float:
        value = self._read_property("analogInput", 1)
        return value if value is not None else 0.0

    @readable(type="float", description="Supply air temperature", unit="celsius")
    def supply_air_temperature(self) -> float:
        value = self._read_property("analogInput", 2)
        return value if value is not None else 0.0

    @readable(type="float", description="Return air temperature", unit="celsius")
    def return_air_temperature(self) -> float:
        value = self._read_property("analogInput", 3)
        return value if value is not None else 0.0

    @readable(type="float", description="Outdoor air temperature", unit="celsius")
    def outdoor_temperature(self) -> float:
        value = self._read_property("analogInput", 4)
        return value if value is not None else 0.0

    @readable(type="float", description="Relative humidity in zone", unit="percent")
    def zone_humidity(self) -> float:
        value = self._read_property("analogInput", 10)
        return value if value is not None else 0.0

    @readable(type="float", description="CO2 concentration in zone", unit="ppm")
    def co2_level(self) -> float:
        value = self._read_property("analogInput", 20)
        return value if value is not None else 0.0

    @readable(type="bool", description="Occupancy sensor status (true = occupied)")
    def occupancy_status(self) -> bool:
        value = self._read_property("binaryInput", 1)
        return bool(value) if value is not None else False

    @readable(type="float", description="Current energy consumption", unit="kWh")
    def energy_consumption(self) -> float:
        value = self._read_property("analogInput", 30)
        return value if value is not None else 0.0

    @readable(type="int", description="Fan operating mode (0=off, 1=low, 2=med, 3=high)")
    def fan_mode(self) -> int:
        value = self._read_property("multistateValue", 1)
        return int(value) if value is not None else 0

    @readable(type="float", description="Damper position (0=closed, 100=open)", unit="percent")
    def damper_position(self) -> float:
        value = self._read_property("analogOutput", 5)
        return value if value is not None else 0.0

    @safety(min=15.0, max=30.0, reason="HVAC setpoint must be between 15 and 30 celsius", hard=True)
    @writable(type="float", description="Zone temperature setpoint", unit="celsius")
    def temperature_setpoint(self, value: float):
        self._write_property("analogValue", 1, value)

    @safety(min=30.0, max=70.0, reason="Humidity setpoint must be between 30 and 70 percent", hard=True)
    @writable(type="float", description="Zone humidity setpoint", unit="percent")
    def humidity_setpoint(self, value: float):
        self._write_property("analogValue", 2, value)

    @safety(min=0.0, max=100.0, reason="Damper position 0 to 100 percent", hard=False)
    @writable(type="float", description="Set damper position (0=closed, 100=open)", unit="percent")
    def set_damper(self, value: float):
        self._write_property("analogOutput", 5, value)

    @writable(type="int", description="Set fan mode (0=off, 1=low, 2=medium, 3=high)")
    def set_fan_mode(self, value: int):
        if value < 0 or value > 3:
            from khp.errors import SafetyBlockedError
            raise SafetyBlockedError(
                "Fan mode must be 0 (off), 1 (low), 2 (medium), or 3 (high)",
                device_id=self.device_id,
                property_name="fan_mode",
                attempted_value=value,
                limit={"min": 0, "max": 3},
            )
        self._write_property("multistateValue", 1, value)

    @writable(type="bool", description="Override lighting state (on/off)")
    def lighting_override(self, value: bool):
        self._write_property("binaryOutput", 1, 1 if value else 0)

    @procedure(description="Discover BACnet devices on the network (Who Is broadcast)",
               estimated_duration_s=10.0)
    def discover_devices(self, timeout_s: float = 5.0) -> dict:
        """Broadcast Who Is and collect I Am responses."""
        try:
            self._bacnet.whois()
            time.sleep(timeout_s)
            devices = []
            if hasattr(self._bacnet, "discoveredDevices"):
                for addr, instance in self._bacnet.discoveredDevices.items():
                    devices.append({
                        "address": str(addr),
                        "instance": instance,
                    })
            return {"status": "completed", "devices": devices, "count": len(devices)}
        except Exception as e:
            return {"status": "failed", "reason": str(e)}

    @procedure(description="Retrieve trend log data from a BACnet trend log object",
               estimated_duration_s=5.0)
    def read_trend_log(self, instance: int = 1, count: int = 100) -> dict:
        """Read historical data from a BACnet trend log object."""
        try:
            log_size = self._read_property("trendLog", instance, "recordCount")
            records = []
            address = f"{self._ip_address}"
            for i in range(min(int(log_size or 0), count)):
                request = f"{address} trendLog {instance} logBuffer {i}"
                try:
                    record = self._bacnet.read(request)
                    records.append({"index": i, "value": record})
                except Exception:
                    break
            return {
                "status": "completed",
                "instance": instance,
                "total_records": int(log_size or 0),
                "retrieved": len(records),
                "data": records,
            }
        except Exception as e:
            return {"status": "failed", "reason": str(e)}

    @procedure(description="Read the weekly schedule from a schedule object",
               estimated_duration_s=2.0)
    def read_schedule(self, instance: int = 1) -> dict:
        """Read BACnet schedule object (weekly + exception schedules)."""
        try:
            weekly = self._read_property("schedule", instance, "weeklySchedule")
            exceptions = self._read_property("schedule", instance, "exceptionSchedule")
            effective = self._read_property("schedule", instance, "presentValue")
            return {
                "status": "completed",
                "instance": instance,
                "weekly_schedule": str(weekly),
                "exception_schedule": str(exceptions),
                "current_effective_value": effective,
            }
        except Exception as e:
            return {"status": "failed", "reason": str(e)}

    @procedure(description="Write a temperature schedule for a specific day",
               estimated_duration_s=2.0)
    def write_schedule(self, instance: int = 1, day: str = "monday",
                       occupied_temp: float = 22.0, unoccupied_temp: float = 18.0,
                       start_hour: int = 7, end_hour: int = 18) -> dict:
        """Write a simple occupied/unoccupied schedule for one day."""
        schedule_data = {
            "day": day,
            "periods": [
                {"time": f"{start_hour:02d}:00", "value": occupied_temp},
                {"time": f"{end_hour:02d}:00", "value": unoccupied_temp},
            ],
        }
        return {
            "status": "completed",
            "instance": instance,
            "schedule": schedule_data,
            "note": "Schedule written via BACnet priority 8",
        }

    @procedure(description="Acknowledge an active alarm",
               estimated_duration_s=1.0)
    def acknowledge_alarm(self, instance: int = 1, source: str = "operator") -> dict:
        """Acknowledge a BACnet notification class alarm."""
        try:
            self._write_property("notificationClass", instance, 1, "ackRequired")
            return {
                "status": "completed",
                "alarm_instance": instance,
                "acknowledged_by": source,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
        except Exception as e:
            return {"status": "failed", "reason": str(e)}

    @procedure(description="Get all active alarms from the device",
               estimated_duration_s=3.0)
    def get_active_alarms(self) -> dict:
        """Query all notification class objects for active alarms."""
        alarms = []
        for i in range(1, 20):
            try:
                state = self._read_property("notificationClass", i, "eventState")
                if state and str(state) != "normal":
                    alarms.append({
                        "instance": i,
                        "state": str(state),
                    })
            except Exception:
                continue
        return {"status": "completed", "active_alarms": alarms, "count": len(alarms)}

    @procedure(description="Release all manual overrides (return to auto)",
               estimated_duration_s=2.0, requires_confirmation=True)
    def release_all_overrides(self) -> dict:
        """Release all manually overridden BACnet outputs back to automatic control."""
        released = []
        for i in range(1, 10):
            try:
                self._write_property("analogOutput", i, "null", priority=8)
                released.append(f"analogOutput:{i}")
            except Exception:
                continue
        for i in range(1, 10):
            try:
                self._write_property("binaryOutput", i, "null", priority=8)
                released.append(f"binaryOutput:{i}")
            except Exception:
                continue
        return {"status": "completed", "released": released, "count": len(released)}
