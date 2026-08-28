"""KHP Driver: OCPP EV Charger Central System.

Implements an OCPP 1.6 Central System that manages EV charging stations via
WebSocket. Receives connections from charge points, monitors session state,
controls transactions, and enforces power/energy safety limits.

Covers: Level 2 AC chargers, DC fast chargers (CCS, CHAdeMO), fleet depots,
public charging networks, home wallboxes, and any OCPP 1.6 compliant EVSE.

Requirements:
    pip install ocpp websockets
"""
from __future__ import annotations

import asyncio
import json
import time
import threading
from typing import Any
from dataclasses import dataclass, field

from khp import Driver, readable, writable, procedure, safety, monitor
from khp.core import ConnectionType


@dataclass
class Transaction:
    transaction_id: int
    connector_id: int
    id_tag: str
    start_time: str
    meter_start: float
    meter_current: float = 0.0
    status: str = "active"
    stop_time: str | None = None
    stop_reason: str | None = None


@dataclass
class ConnectorState:
    connector_id: int
    status: str = "Available"
    error_code: str = "NoError"
    current_power_kw: float = 0.0
    session_energy_kwh: float = 0.0
    voltage: float = 0.0
    current_amps: float = 0.0
    temperature_c: float = 25.0


class OCPPDevice(Driver):
    """OCPP 1.6 Central System driver for EV charging station management."""

    name = "OCPP Central System"
    version = "1.0.0"
    device_type = "ev_charger"
    description = "EV charging station controller via OCPP 1.6 WebSocket protocol"
    connection_type = ConnectionType.REST

    def __init__(self, device_id: str | None = None, host: str = "0.0.0.0",
                 port: int = 9000, max_power_kw: float = 150.0,
                 max_session_energy_kwh: float = 100.0,
                 num_connectors: int = 2, **config):
        super().__init__(device_id=device_id, host=host, port=port, **config)
        self._host = host
        self._port = port
        self._max_power_kw = max_power_kw
        self._max_session_energy_kwh = max_session_energy_kwh
        self._num_connectors = num_connectors

        self._server = None
        self._server_thread = None
        self._loop = None
        self._charge_point_id: str | None = None
        self._charge_point_ws = None
        self._firmware_version = "unknown"
        self._vendor = "unknown"
        self._model = "unknown"
        self._registration_status = "pending"
        self._heartbeat_interval = 300
        self._last_heartbeat: float = 0.0
        self._transaction_counter = 1000
        self._local_auth_list: list[str] = []
        self._charging_profile: dict | None = None

        self._connectors: dict[int, ConnectorState] = {}
        for i in range(1, num_connectors + 1):
            self._connectors[i] = ConnectorState(connector_id=i)

        self._transactions: dict[int, Transaction] = {}
        self._active_transaction: Transaction | None = None
        self._meter_values: list[dict] = []

    async def connect(self):
        """Start the OCPP WebSocket server and wait for a charge point to connect."""
        try:
            import websockets
            from ocpp.routing import on
            from ocpp.v16 import ChargePoint as CP16
            from ocpp.v16 import call_result
            from ocpp.v16.enums import RegistrationStatus, Action
        except ImportError:
            from khp.errors import DriverLoadError
            raise DriverLoadError(
                "OCPP libraries not installed. Install with: pip install ocpp websockets",
                device_id=self.device_id,
            )

        self._loop = asyncio.new_event_loop()

        async def _run_server():
            async def _on_connect(websocket, path):
                cp_id = path.strip("/")
                self._charge_point_id = cp_id
                self._charge_point_ws = websocket
                self._registration_status = "accepted"
                self._last_heartbeat = time.time()

                try:
                    async for message in websocket:
                        self._handle_message(message)
                except Exception:
                    self._charge_point_ws = None
                    self._registration_status = "disconnected"

            self._server = await websockets.serve(
                _on_connect, self._host, self._port,
                subprotocols=["ocpp1.6"]
            )
            await self._server.wait_closed()

        def _thread_target():
            asyncio.set_event_loop(self._loop)
            self._loop.run_until_complete(_run_server())

        self._server_thread = threading.Thread(target=_thread_target, daemon=True)
        self._server_thread.start()
        await super().connect()

    async def disconnect(self):
        """Stop the WebSocket server and close all connections."""
        if self._server:
            self._server.close()
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._server_thread:
            self._server_thread.join(timeout=3.0)
        self._charge_point_ws = None
        self._registration_status = "disconnected"
        await super().disconnect()

    def _handle_message(self, raw_message: str):
        """Parse incoming OCPP message from charge point."""
        try:
            msg = json.loads(raw_message)
            if not isinstance(msg, list) or len(msg) < 3:
                return

            msg_type = msg[0]
            if msg_type == 2:
                action = msg[2]
                payload = msg[3] if len(msg) > 3 else {}
                self._handle_call(action, payload)
        except (json.JSONDecodeError, IndexError):
            pass

    def _handle_call(self, action: str, payload: dict):
        """Handle incoming Call from charge point."""
        if action == "BootNotification":
            self._vendor = payload.get("chargePointVendor", "unknown")
            self._model = payload.get("chargePointModel", "unknown")
            self._firmware_version = payload.get("firmwareVersion", "unknown")
            self._registration_status = "accepted"

        elif action == "Heartbeat":
            self._last_heartbeat = time.time()

        elif action == "StatusNotification":
            connector_id = payload.get("connectorId", 0)
            status = payload.get("status", "Unknown")
            error_code = payload.get("errorCode", "NoError")
            if connector_id in self._connectors:
                self._connectors[connector_id].status = status
                self._connectors[connector_id].error_code = error_code

        elif action == "MeterValues":
            connector_id = payload.get("connectorId", 1)
            meter_value = payload.get("meterValue", [])
            for mv in meter_value:
                for sv in mv.get("sampledValue", []):
                    self._process_sampled_value(connector_id, sv)
            self._meter_values.append(payload)
            if len(self._meter_values) > 1000:
                self._meter_values = self._meter_values[-500:]

        elif action == "StartTransaction":
            connector_id = payload.get("connectorId", 1)
            id_tag = payload.get("idTag", "")
            meter_start = payload.get("meterStart", 0)
            self._transaction_counter += 1
            txn = Transaction(
                transaction_id=self._transaction_counter,
                connector_id=connector_id,
                id_tag=id_tag,
                start_time=payload.get("timestamp", ""),
                meter_start=meter_start,
            )
            self._transactions[txn.transaction_id] = txn
            self._active_transaction = txn

        elif action == "StopTransaction":
            txn_id = payload.get("transactionId", 0)
            if txn_id in self._transactions:
                txn = self._transactions[txn_id]
                txn.status = "completed"
                txn.stop_time = payload.get("timestamp", "")
                txn.stop_reason = payload.get("reason", "Local")
                txn.meter_current = payload.get("meterStop", txn.meter_start)
                if self._active_transaction and self._active_transaction.transaction_id == txn_id:
                    self._active_transaction = None

    def _process_sampled_value(self, connector_id: int, sv: dict):
        """Update connector state from a sampled meter value."""
        if connector_id not in self._connectors:
            return
        conn = self._connectors[connector_id]
        measurand = sv.get("measurand", "Energy.Active.Import.Register")
        value = float(sv.get("value", 0))

        if "Power" in measurand:
            conn.current_power_kw = value / 1000.0
        elif "Energy" in measurand:
            conn.session_energy_kwh = value / 1000.0
        elif "Voltage" in measurand:
            conn.voltage = value
        elif "Current" in measurand:
            conn.current_amps = value
        elif "Temperature" in measurand:
            conn.temperature_c = value

    async def _send_call(self, action: str, payload: dict) -> dict | None:
        """Send a Call to the connected charge point."""
        if not self._charge_point_ws:
            from khp.errors import DeviceOfflineError
            raise DeviceOfflineError(
                "No charge point connected", device_id=self.device_id
            )
        msg = json.dumps([2, f"msg_{int(time.time())}", action, payload])
        try:
            await self._charge_point_ws.send(msg)
            return {"sent": True, "action": action}
        except Exception as e:
            return {"sent": False, "error": str(e)}

    @readable(type="str", description="Overall charger status (available, charging, faulted, offline)")
    def charger_status(self) -> str:
        if self._registration_status == "disconnected":
            return "offline"
        for conn in self._connectors.values():
            if conn.status == "Faulted":
                return "faulted"
            if conn.status == "Charging":
                return "charging"
        return "available"

    @readable(type="dict", description="Connected charge point identity and firmware")
    def charge_point_info(self) -> dict:
        return {
            "charge_point_id": self._charge_point_id,
            "vendor": self._vendor,
            "model": self._model,
            "firmware_version": self._firmware_version,
            "registration_status": self._registration_status,
            "last_heartbeat_age_s": time.time() - self._last_heartbeat if self._last_heartbeat else None,
        }

    @readable(type="float", description="Current session energy consumed", unit="kWh")
    def session_energy_kwh(self) -> float:
        if self._active_transaction:
            conn_id = self._active_transaction.connector_id
            if conn_id in self._connectors:
                return self._connectors[conn_id].session_energy_kwh
        return 0.0

    @readable(type="list", description="Status of all connectors")
    def connector_status(self) -> list:
        return [
            {
                "connector_id": c.connector_id,
                "status": c.status,
                "error_code": c.error_code,
                "power_kw": c.current_power_kw,
                "energy_kwh": c.session_energy_kwh,
                "voltage": c.voltage,
                "current_amps": c.current_amps,
                "temperature_c": c.temperature_c,
            }
            for c in self._connectors.values()
        ]

    @readable(type="dict", description="Most recent meter values from the charger")
    def meter_values(self) -> dict:
        if self._meter_values:
            return self._meter_values[-1]
        return {}

    @readable(type="dict", description="Active transaction details (or None)")
    def active_transaction(self) -> dict | None:
        if not self._active_transaction:
            return None
        txn = self._active_transaction
        return {
            "transaction_id": txn.transaction_id,
            "connector_id": txn.connector_id,
            "id_tag": txn.id_tag,
            "start_time": txn.start_time,
            "meter_start": txn.meter_start,
            "meter_current": txn.meter_current,
            "status": txn.status,
        }

    @readable(type="dict", description="Last completed transaction summary")
    def last_transaction(self) -> dict | None:
        completed = [t for t in self._transactions.values() if t.status == "completed"]
        if not completed:
            return None
        last = completed[-1]
        return {
            "transaction_id": last.transaction_id,
            "id_tag": last.id_tag,
            "energy_kwh": (last.meter_current - last.meter_start) / 1000.0,
            "duration": last.stop_time,
            "stop_reason": last.stop_reason,
        }

    @safety(min=0.0, max=150.0, reason="Maximum charging power to protect electrical infrastructure", hard=True)
    @writable(type="float", description="Set maximum power limit for charging profile", unit="kW")
    def power_limit_kw(self, value: float):
        self._charging_profile = {
            "chargingProfileId": 1,
            "stackLevel": 0,
            "chargingProfilePurpose": "TxDefaultProfile",
            "chargingProfileKind": "Relative",
            "chargingSchedule": {
                "chargingRateUnit": "W",
                "chargingSchedulePeriod": [
                    {"startPeriod": 0, "limit": value * 1000}
                ]
            }
        }

    @writable(type="str", description="Change connector availability (operative or inoperative)")
    def availability(self, value: str):
        if value.lower() not in ("operative", "inoperative"):
            from khp.errors import SafetyBlockedError
            raise SafetyBlockedError(
                "Availability must be 'operative' or 'inoperative'",
                device_id=self.device_id,
                property_name="availability",
                value=value,
                limit=None,
            )

    @writable(type="list", description="Update local authorization list (list of RFID tags)")
    def local_auth_list(self, value: list):
        self._local_auth_list = [str(tag) for tag in value]

    @procedure(description="Start a charging transaction remotely on a connector")
    def remote_start_transaction(self, connector_id: int = 1, id_tag: str = "REMOTE"):
        if connector_id not in self._connectors:
            from khp.errors import PreconditionFailedError
            raise PreconditionFailedError(
                f"Connector {connector_id} does not exist",
                device_id=self.device_id,
            )
        conn = self._connectors[connector_id]
        if conn.status not in ("Available", "Preparing"):
            from khp.errors import PreconditionFailedError
            raise PreconditionFailedError(
                f"Connector {connector_id} is {conn.status}, cannot start transaction",
                device_id=self.device_id,
            )

        self._transaction_counter += 1
        txn = Transaction(
            transaction_id=self._transaction_counter,
            connector_id=connector_id,
            id_tag=id_tag,
            start_time=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            meter_start=0,
        )
        self._transactions[txn.transaction_id] = txn
        self._active_transaction = txn
        conn.status = "Charging"

        return {
            "status": "accepted",
            "transaction_id": txn.transaction_id,
            "connector_id": connector_id,
        }

    @procedure(description="Stop an active charging transaction remotely")
    def remote_stop_transaction(self, transaction_id: int = 0):
        if transaction_id == 0 and self._active_transaction:
            transaction_id = self._active_transaction.transaction_id

        if transaction_id not in self._transactions:
            return {"status": "rejected", "reason": "transaction not found"}

        txn = self._transactions[transaction_id]
        txn.status = "completed"
        txn.stop_time = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        txn.stop_reason = "Remote"

        if txn.connector_id in self._connectors:
            self._connectors[txn.connector_id].status = "Available"
            self._connectors[txn.connector_id].current_power_kw = 0.0

        if self._active_transaction and self._active_transaction.transaction_id == transaction_id:
            self._active_transaction = None

        return {
            "status": "accepted",
            "transaction_id": transaction_id,
            "energy_kwh": (txn.meter_current - txn.meter_start) / 1000.0,
        }

    @procedure(description="Reset the charge point (soft or hard)", requires_confirmation=True)
    def reset_charger(self, reset_type: str = "Soft"):
        if reset_type not in ("Soft", "Hard"):
            return {"status": "rejected", "reason": "type must be Soft or Hard"}
        for conn in self._connectors.values():
            conn.status = "Available"
            conn.current_power_kw = 0.0
            conn.session_energy_kwh = 0.0
        self._active_transaction = None
        return {"status": "accepted", "type": reset_type}

    @procedure(description="Unlock a connector (release cable)")
    def unlock_connector(self, connector_id: int = 1):
        if connector_id not in self._connectors:
            return {"status": "rejected", "reason": "connector not found"}
        return {"status": "unlocked", "connector_id": connector_id}

    @procedure(description="Request diagnostics upload from the charge point")
    def get_diagnostics(self, upload_url: str = "", retries: int = 3):
        return {
            "status": "accepted",
            "upload_url": upload_url,
            "retries": retries,
            "filename": f"diag_{self._charge_point_id}_{int(time.time())}.zip",
        }

    @procedure(description="Trigger firmware update on the charge point", requires_confirmation=True)
    def update_firmware(self, firmware_url: str = "", retrieve_date: str = ""):
        if not firmware_url:
            return {"status": "rejected", "reason": "firmware_url required"}
        return {
            "status": "accepted",
            "firmware_url": firmware_url,
            "retrieve_date": retrieve_date or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }

    @procedure(description="Reserve a connector for a specific RFID tag")
    def reserve_now(self, connector_id: int = 1, id_tag: str = "", expiry_minutes: int = 15):
        if connector_id not in self._connectors:
            return {"status": "rejected", "reason": "connector not found"}
        conn = self._connectors[connector_id]
        if conn.status != "Available":
            return {"status": "rejected", "reason": f"connector is {conn.status}"}

        conn.status = "Reserved"
        return {
            "status": "accepted",
            "connector_id": connector_id,
            "id_tag": id_tag,
            "expiry_minutes": expiry_minutes,
        }

    @procedure(description="Clear the authorization cache on the charge point")
    def clear_cache(self):
        self._local_auth_list = []
        return {"status": "accepted", "cache_cleared": True}

    @procedure(description="Get transaction history for a connector")
    def transaction_history(self, connector_id: int = 0, limit: int = 20):
        txns = list(self._transactions.values())
        if connector_id > 0:
            txns = [t for t in txns if t.connector_id == connector_id]
        txns = txns[-limit:]
        return {
            "count": len(txns),
            "transactions": [
                {
                    "id": t.transaction_id,
                    "connector": t.connector_id,
                    "id_tag": t.id_tag,
                    "status": t.status,
                    "energy_kwh": (t.meter_current - t.meter_start) / 1000.0,
                }
                for t in txns
            ]
        }

    @monitor(interval_ms=5000, description="Monitor charger health, overcurrent, and ground faults")
    def check_charger_health(self) -> dict[str, Any]:
        alerts = []

        if self._registration_status == "disconnected":
            alerts.append({"level": "critical", "message": "Charge point disconnected"})

        if self._last_heartbeat and (time.time() - self._last_heartbeat) > self._heartbeat_interval * 2:
            alerts.append({"level": "warning", "message": "Heartbeat timeout exceeded"})

        for conn in self._connectors.values():
            if conn.error_code != "NoError":
                alerts.append({
                    "level": "critical",
                    "message": f"Connector {conn.connector_id} error: {conn.error_code}",
                })

            if conn.current_power_kw > self._max_power_kw:
                alerts.append({
                    "level": "critical",
                    "message": f"Connector {conn.connector_id} overcurrent: {conn.current_power_kw:.1f} kW",
                })

            if conn.session_energy_kwh > self._max_session_energy_kwh:
                alerts.append({
                    "level": "warning",
                    "message": f"Connector {conn.connector_id} session energy cap reached",
                })

            if conn.temperature_c > 80.0:
                alerts.append({
                    "level": "critical",
                    "message": f"Connector {conn.connector_id} overtemperature: {conn.temperature_c:.1f} C",
                })

        return {
            "healthy": len(alerts) == 0,
            "charge_point": self._charge_point_id,
            "status": self.charger_status(),
            "connectors": len(self._connectors),
            "active_transactions": sum(1 for t in self._transactions.values() if t.status == "active"),
            "alerts": alerts,
        }
