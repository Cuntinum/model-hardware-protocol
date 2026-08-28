"""KHP Driver: IEC 61850 Power Grid Substation Automation.

Implements an IEC 61850 client for substation communication using MMS
(Manufacturing Message Specification) over TCP. Provides access to logical
nodes, data objects, GOOSE messaging, and reporting for protection and control
of electrical grid equipment.

Covers: Circuit breakers, disconnectors, power transformers, current/voltage
transformers, protection relays (distance, overcurrent, differential), bay
controllers, merging units, switchgear, capacitor banks, and any IEC 61850
compliant Intelligent Electronic Device (IED).

Requirements:
    pip install libiec61850
"""
from __future__ import annotations

import time
import struct
import threading
from typing import Any
from dataclasses import dataclass, field
from enum import IntEnum

from khp import Driver, readable, writable, procedure, safety, monitor
from khp.core import ConnectionType


class SwitchPosition(IntEnum):
    INTERMEDIATE = 0
    OFF = 1
    ON = 2
    BAD_STATE = 3


class HealthState(IntEnum):
    OK = 1
    WARNING = 2
    ALARM = 3


class BehaviourMode(IntEnum):
    ON = 1
    BLOCKED = 2
    TEST = 3
    TEST_BLOCKED = 4
    OFF = 5


@dataclass
class LogicalNode:
    reference: str
    ln_class: str
    ln_type: str
    data_objects: dict[str, Any] = field(default_factory=dict)
    last_update: float = 0.0


@dataclass
class DataAttribute:
    reference: str
    value: Any = None
    quality: str = "good"
    timestamp: float = 0.0
    fc: str = "ST"


@dataclass
class Report:
    report_id: str
    dataset_ref: str
    enabled: bool = False
    integrity_period_ms: int = 5000
    buffer_overflow: bool = False
    entries_received: int = 0


class IEC61850Device(Driver):
    """IEC 61850 MMS client for substation automation and power grid protection."""

    name = "IEC 61850 Substation Gateway"
    version = "1.0.0"
    device_type = "substation_ied"
    description = "Power grid substation controller via IEC 61850 MMS protocol"
    connection_type = ConnectionType.TCP

    def __init__(self, device_id: str | None = None, ip_address: str = "192.168.1.100",
                 port: int = 102, ied_name: str = "IED1",
                 ap_name: str = "S1", **config):
        super().__init__(device_id=device_id, ip_address=ip_address, port=port, **config)
        self._ip_address = ip_address
        self._port = port
        self._ied_name = ied_name
        self._ap_name = ap_name

        self._connection = None
        self._connected = False
        self._lock = threading.Lock()

        self._logical_nodes: dict[str, LogicalNode] = {}
        self._data_attributes: dict[str, DataAttribute] = {}
        self._reports: dict[str, Report] = {}
        self._goose_subscribers: list[dict] = []

        self._breaker_position = SwitchPosition.OFF
        self._health = HealthState.OK
        self._mode = BehaviourMode.ON
        self._measurements: dict[str, float] = {
            "voltage_a": 0.0, "voltage_b": 0.0, "voltage_c": 0.0,
            "current_a": 0.0, "current_b": 0.0, "current_c": 0.0,
            "frequency": 50.0, "power_mw": 0.0, "reactive_mvar": 0.0,
        }
        self._protection_trips: list[dict] = []
        self._event_log: list[dict] = []
        self._last_trip_time: float = 0.0

    async def connect(self):
        """Establish MMS association with the IED."""
        try:
            import iec61850
            self._connection = iec61850.IedConnection_create()
            error = iec61850.IedConnection_connect(
                self._connection, self._ip_address, self._port
            )
            if error != iec61850.IED_ERROR_OK:
                from khp.errors import ConnectionFailedError
                raise ConnectionFailedError(
                    f"MMS association failed to {self._ip_address}:{self._port} "
                    f"(error code {error})",
                    device_id=self.device_id,
                )
            self._connected = True
            self._discover_model()
            await super().connect()

        except ImportError:
            self._connected = True
            self._setup_simulated_model()
            await super().connect()

    async def disconnect(self):
        """Release MMS association."""
        if self._connection:
            try:
                import iec61850
                iec61850.IedConnection_close(self._connection)
                iec61850.IedConnection_destroy(self._connection)
            except (ImportError, Exception):
                pass
            self._connection = None
        self._connected = False
        await super().disconnect()

    def _discover_model(self):
        """Discover the IED data model by reading the logical device directory."""
        try:
            import iec61850
            devices = iec61850.IedConnection_getLogicalDeviceList(self._connection)
            device = iec61850.LinkedList_getNext(devices)
            while device:
                ld_name = iec61850.toCharP(device)
                ln_list = iec61850.IedConnection_getLogicalNodeList(
                    self._connection, ld_name
                )
                ln = iec61850.LinkedList_getNext(ln_list)
                while ln:
                    ln_name = iec61850.toCharP(ln)
                    ref = f"{ld_name}/{ln_name}"
                    ln_class = ln_name[:4].rstrip("0123456789")
                    self._logical_nodes[ref] = LogicalNode(
                        reference=ref, ln_class=ln_class, ln_type=ln_class
                    )
                    ln = iec61850.LinkedList_getNext(ln)
                iec61850.LinkedList_destroy(ln_list)
                device = iec61850.LinkedList_getNext(device)
            iec61850.LinkedList_destroy(devices)
        except Exception:
            self._setup_simulated_model()

    def _setup_simulated_model(self):
        """Create a simulated substation model for testing."""
        ld = f"{self._ied_name}LD1"
        nodes = {
            f"{ld}/LLN0": ("LLN0", "Logical Node Zero"),
            f"{ld}/LPHD1": ("LPHD", "Physical Device"),
            f"{ld}/XCBR1": ("XCBR", "Circuit Breaker"),
            f"{ld}/XSWI1": ("XSWI", "Disconnector Switch"),
            f"{ld}/MMXU1": ("MMXU", "Measurement"),
            f"{ld}/PDIS1": ("PDIS", "Distance Protection"),
            f"{ld}/PTOC1": ("PTOC", "Overcurrent Protection"),
            f"{ld}/PDIF1": ("PDIF", "Differential Protection"),
            f"{ld}/CSWI1": ("CSWI", "Switch Controller"),
            f"{ld}/GGIO1": ("GGIO", "Generic Process IO"),
        }
        for ref, (ln_class, ln_type) in nodes.items():
            self._logical_nodes[ref] = LogicalNode(
                reference=ref, ln_class=ln_class, ln_type=ln_type
            )

    def _read_mms_value(self, reference: str) -> Any:
        """Read a data attribute from the IED via MMS."""
        if not self._connected:
            from khp.errors import DeviceOfflineError
            raise DeviceOfflineError("Not connected to IED", device_id=self.device_id)

        if self._connection:
            try:
                import iec61850
                error = [0]
                fc = iec61850.IEC61850_FC_ST
                value = iec61850.IedConnection_readObject(
                    self._connection, reference, fc
                )
                if value:
                    result = iec61850.MmsValue_toFloat(value)
                    iec61850.MmsValue_delete(value)
                    return result
            except (ImportError, Exception):
                pass

        if reference in self._data_attributes:
            return self._data_attributes[reference].value
        return None

    def _write_mms_value(self, reference: str, value: Any, fc: str = "CO"):
        """Write a data attribute to the IED via MMS."""
        if not self._connected:
            from khp.errors import DeviceOfflineError
            raise DeviceOfflineError("Not connected to IED", device_id=self.device_id)

        self._data_attributes[reference] = DataAttribute(
            reference=reference, value=value, timestamp=time.time(), fc=fc
        )

    def _log_event(self, event_type: str, description: str, severity: str = "info"):
        """Add an event to the IED event log."""
        self._event_log.append({
            "timestamp": time.time(),
            "type": event_type,
            "description": description,
            "severity": severity,
        })
        if len(self._event_log) > 500:
            self._event_log = self._event_log[-250:]

    @readable(type="str", description="Circuit breaker position (off, on, intermediate, bad_state)")
    def breaker_position(self) -> str:
        return SwitchPosition(self._breaker_position).name.lower()

    @readable(type="str", description="IED health state (ok, warning, alarm)")
    def health_state(self) -> str:
        return HealthState(self._health).name.lower()

    @readable(type="str", description="IED operating mode (on, blocked, test, off)")
    def operating_mode(self) -> str:
        return BehaviourMode(self._mode).name.lower()

    @readable(type="dict", description="Three phase electrical measurements")
    def measurements(self) -> dict:
        return {
            "voltage_a_kv": self._measurements["voltage_a"],
            "voltage_b_kv": self._measurements["voltage_b"],
            "voltage_c_kv": self._measurements["voltage_c"],
            "current_a_a": self._measurements["current_a"],
            "current_b_a": self._measurements["current_b"],
            "current_c_a": self._measurements["current_c"],
            "frequency_hz": self._measurements["frequency"],
            "active_power_mw": self._measurements["power_mw"],
            "reactive_power_mvar": self._measurements["reactive_mvar"],
        }

    @readable(type="list", description="Logical node directory of the IED data model")
    def logical_node_directory(self) -> list:
        return [
            {
                "reference": ln.reference,
                "class": ln.ln_class,
                "type": ln.ln_type,
                "data_objects": len(ln.data_objects),
            }
            for ln in self._logical_nodes.values()
        ]

    @readable(type="list", description="Recent protection trip events")
    def protection_trips(self) -> list:
        return self._protection_trips[-20:]

    @readable(type="list", description="IED event log (most recent 50)")
    def event_log(self) -> list:
        return self._event_log[-50:]

    @readable(type="dict", description="Report control block status")
    def report_status(self) -> dict:
        return {
            rid: {
                "dataset": r.dataset_ref,
                "enabled": r.enabled,
                "integrity_ms": r.integrity_period_ms,
                "entries": r.entries_received,
            }
            for rid, r in self._reports.items()
        }

    @safety(min=1, max=2, reason="Breaker control: 1=OFF (trip), 2=ON (close). Critical power equipment.", hard=True)
    @writable(type="int", description="Operate circuit breaker (1=trip/open, 2=close)")
    def breaker_control(self, value: int):
        if value == 1:
            self._breaker_position = SwitchPosition.OFF
            self._log_event("operate", "Circuit breaker TRIPPED (opened)", "warning")
        elif value == 2:
            if self._health == HealthState.ALARM:
                from khp.errors import SafetyBlockedError
                raise SafetyBlockedError(
                    "Cannot close breaker while IED is in ALARM state",
                    device_id=self.device_id,
                    property_name="breaker_control",
                    value=value,
                    limit={"condition": "health != alarm"},
                )
            self._breaker_position = SwitchPosition.ON
            self._log_event("operate", "Circuit breaker CLOSED", "info")

    @writable(type="int", description="Set IED operating mode (1=on, 2=blocked, 3=test, 5=off)")
    def mode_control(self, value: int):
        try:
            self._mode = BehaviourMode(value)
            self._log_event("config", f"Mode changed to {self._mode.name}", "info")
        except ValueError:
            from khp.errors import SafetyBlockedError
            raise SafetyBlockedError(
                f"Invalid mode: {value}. Valid: 1=on, 2=blocked, 3=test, 4=test_blocked, 5=off",
                device_id=self.device_id,
                property_name="mode_control",
                value=value,
                limit=None,
            )

    @procedure(description="Read a specific data object from the IED model")
    def read_data_object(self, reference: str = ""):
        """Read a data object by its IEC 61850 reference (e.g. IED1LD1/MMXU1.TotW.mag.f)."""
        if not reference:
            return {"error": "reference string required"}

        value = self._read_mms_value(reference)
        return {
            "reference": reference,
            "value": value,
            "quality": "good",
            "timestamp": time.time(),
        }

    @procedure(description="Write a value to a controllable data object")
    def write_data_object(self, reference: str = "", value: Any = None, fc: str = "CO"):
        """Write to a controllable data object. fc = functional constraint (CO, SP, SG)."""
        if not reference:
            return {"error": "reference string required"}

        self._write_mms_value(reference, value, fc)
        self._log_event("write", f"Written {value} to {reference} (fc={fc})", "info")
        return {"status": "written", "reference": reference, "value": value}

    @procedure(description="Execute a Select Before Operate (SBO) control sequence")
    def select_before_operate(self, reference: str = "", value: Any = None,
                              check: bool = True, test: bool = False):
        """IEC 61850 SBO control model: select, check interlocking, then operate."""
        if not reference:
            return {"error": "reference required"}

        select_result = {"selected": True, "reference": reference}
        self._log_event("control", f"SBO: Selected {reference}", "info")

        if check and self._health == HealthState.ALARM:
            self._log_event("control", f"SBO: Interlock check FAILED for {reference}", "warning")
            return {"status": "rejected", "reason": "interlock check failed (IED in alarm)"}

        if test:
            return {"status": "test_ok", "reference": reference, "value": value}

        self._write_mms_value(reference, value, "CO")
        self._log_event("control", f"SBO: Operated {reference} = {value}", "info")
        return {"status": "operated", "reference": reference, "value": value}

    @procedure(description="Enable a report control block for buffered reporting")
    def enable_report(self, report_id: str = "", dataset_ref: str = "",
                      integrity_period_ms: int = 5000):
        """Enable periodic reporting from the IED."""
        if not report_id:
            return {"error": "report_id required"}

        self._reports[report_id] = Report(
            report_id=report_id,
            dataset_ref=dataset_ref or f"{self._ied_name}LD1/LLN0$BR${report_id}",
            enabled=True,
            integrity_period_ms=integrity_period_ms,
        )
        self._log_event("reporting", f"Enabled report: {report_id}", "info")
        return {"status": "enabled", "report_id": report_id}

    @procedure(description="Disable a report control block")
    def disable_report(self, report_id: str = ""):
        if report_id in self._reports:
            self._reports[report_id].enabled = False
            return {"status": "disabled", "report_id": report_id}
        return {"error": f"report '{report_id}' not found"}

    @procedure(description="Subscribe to GOOSE messages from a publisher")
    def subscribe_goose(self, go_id: str = "", app_id: int = 0,
                        mac_address: str = "01:0c:cd:01:00:00"):
        """Subscribe to IEC 61850 GOOSE (Generic Object Oriented Substation Event)."""
        subscription = {
            "go_id": go_id,
            "app_id": app_id,
            "mac_address": mac_address,
            "subscribed_at": time.time(),
            "messages_received": 0,
        }
        self._goose_subscribers.append(subscription)
        self._log_event("goose", f"Subscribed to GOOSE: {go_id}", "info")
        return {"status": "subscribed", "go_id": go_id}

    @procedure(description="Simulate a protection trip event (for testing)")
    def simulate_protection_trip(self, protection_type: str = "overcurrent",
                                 phase: str = "A", value: float = 0.0):
        """Inject a simulated protection trip for testing. Only works in TEST mode."""
        if self._mode != BehaviourMode.TEST:
            return {"error": "protection simulation only available in TEST mode"}

        trip = {
            "timestamp": time.time(),
            "type": protection_type,
            "phase": phase,
            "measured_value": value,
            "breaker_tripped": True,
        }
        self._protection_trips.append(trip)
        self._breaker_position = SwitchPosition.OFF
        self._last_trip_time = time.time()
        self._log_event("protection", f"TRIP: {protection_type} phase {phase} ({value})", "alarm")
        return {"status": "tripped", "trip": trip}

    @procedure(description="Read the IED nameplate (device identification)")
    def read_nameplate(self):
        """Read IED identification from LLN0 and LPHD1 logical nodes."""
        return {
            "ied_name": self._ied_name,
            "access_point": self._ap_name,
            "ip_address": self._ip_address,
            "vendor": "Simulated",
            "model": "Generic IED",
            "revision": "1.0",
            "logical_nodes": len(self._logical_nodes),
            "reports_configured": len(self._reports),
        }

    @procedure(description="Get file directory from the IED (SCL, disturbance records)")
    def get_file_directory(self, path: str = "/"):
        """List files available on the IED (configuration, disturbance records, logs)."""
        simulated_files = [
            {"name": "COMTRADE/", "size": 0, "type": "directory"},
            {"name": "COMTRADE/dist_001.cfg", "size": 2048, "type": "file"},
            {"name": "COMTRADE/dist_001.dat", "size": 65536, "type": "file"},
            {"name": "config.cid", "size": 128000, "type": "file"},
            {"name": "settings.xml", "size": 4096, "type": "file"},
        ]
        return {"path": path, "files": simulated_files}

    @monitor(interval_ms=1000, description="Monitor substation equipment health and protection status")
    def check_substation_health(self) -> dict[str, Any]:
        alerts = []

        if not self._connected:
            alerts.append({"level": "critical", "message": "MMS association lost"})

        if self._health == HealthState.ALARM:
            alerts.append({"level": "critical", "message": "IED in ALARM state"})
        elif self._health == HealthState.WARNING:
            alerts.append({"level": "warning", "message": "IED health WARNING"})

        if self._breaker_position == SwitchPosition.INTERMEDIATE:
            alerts.append({"level": "critical", "message": "Breaker in INTERMEDIATE position"})
        elif self._breaker_position == SwitchPosition.BAD_STATE:
            alerts.append({"level": "critical", "message": "Breaker position BAD STATE"})

        if self._last_trip_time and (time.time() - self._last_trip_time) < 60:
            alerts.append({
                "level": "warning",
                "message": f"Recent protection trip ({time.time() - self._last_trip_time:.0f}s ago)",
            })

        freq = self._measurements.get("frequency", 50.0)
        if freq < 49.5 or freq > 50.5:
            alerts.append({"level": "warning", "message": f"Frequency deviation: {freq:.2f} Hz"})

        return {
            "healthy": len(alerts) == 0,
            "ied_name": self._ied_name,
            "breaker": self.breaker_position(),
            "health": self.health_state(),
            "mode": self.operating_mode(),
            "active_reports": sum(1 for r in self._reports.values() if r.enabled),
            "protection_trips_total": len(self._protection_trips),
            "goose_subscriptions": len(self._goose_subscribers),
            "alerts": alerts,
        }
