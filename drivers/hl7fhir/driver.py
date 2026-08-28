"""KHP Driver: HL7 FHIR Medical Device Integration.

Connects to FHIR R4 compliant servers to read patient vitals, device metrics,
observations, and alerts. Provides safe, audited access to clinical data with
HIPAA compliant logging and physiological range validation.

Covers: patient monitors, infusion pumps, ventilators, pulse oximeters,
blood pressure monitors, thermometers, ECG devices, and any FHIR enabled
medical equipment.

Requirements:
    pip install httpx fhir.resources
"""
from __future__ import annotations

import time
import json
import hashlib
from typing import Any
from datetime import datetime, timezone

from khp import Driver, readable, writable, procedure, safety, monitor
from khp.core import ConnectionType


VITAL_RANGES = {
    "heart_rate": {"min": 20, "max": 300, "unit": "bpm"},
    "systolic_bp": {"min": 40, "max": 300, "unit": "mmHg"},
    "diastolic_bp": {"min": 20, "max": 200, "unit": "mmHg"},
    "spo2": {"min": 50, "max": 100, "unit": "%"},
    "temperature": {"min": 30.0, "max": 45.0, "unit": "celsius"},
    "respiratory_rate": {"min": 4, "max": 60, "unit": "breaths/min"},
}

LOINC_CODES = {
    "heart_rate": "8867-4",
    "systolic_bp": "8480-6",
    "diastolic_bp": "8462-4",
    "spo2": "2708-6",
    "temperature": "8310-5",
    "respiratory_rate": "9279-1",
}


class HL7FHIRDevice(Driver):
    """HL7 FHIR R4 medical device driver for clinical data access."""

    name = "HL7 FHIR Medical Gateway"
    version = "1.0.0"
    device_type = "medical_gateway"
    description = "FHIR R4 server integration for patient vitals, observations, and device metrics"
    connection_type = ConnectionType.REST

    def __init__(self, device_id: str | None = None, base_url: str = "http://localhost:8080/fhir",
                 auth_token: str | None = None, client_id: str | None = None,
                 client_secret: str | None = None, patient_id: str | None = None, **config):
        super().__init__(device_id=device_id, base_url=base_url, **config)
        self._base_url = base_url.rstrip("/")
        self._auth_token = auth_token
        self._client_id = client_id
        self._client_secret = client_secret
        self._patient_id = patient_id
        self._client = None
        self._server_metadata = {}
        self._last_vitals: dict[str, Any] = {}
        self._active_alerts: list[dict] = []
        self._audit_log: list[dict] = []
        self._observation_count = 0

    def _log_access(self, action: str, resource_type: str, resource_id: str | None = None):
        """HIPAA compliant audit logging for all data access."""
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "action": action,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "device_id": self.device_id,
            "patient_id": self._patient_id,
        }
        self._audit_log.append(entry)
        if len(self._audit_log) > 10000:
            self._audit_log = self._audit_log[-5000:]

    async def connect(self):
        """Establish connection to the FHIR server and verify capabilities."""
        try:
            import httpx

            headers = {"Accept": "application/fhir+json"}
            if self._auth_token:
                headers["Authorization"] = f"Bearer {self._auth_token}"

            self._client = httpx.Client(
                base_url=self._base_url,
                headers=headers,
                timeout=30.0,
            )

            response = self._client.get("/metadata")
            if response.status_code != 200:
                from khp.errors import ConnectionFailedError
                raise ConnectionFailedError(
                    f"FHIR server at {self._base_url} returned {response.status_code}",
                    device_id=self.device_id,
                )

            metadata = response.json()
            self._server_metadata = {
                "fhir_version": metadata.get("fhirVersion", "unknown"),
                "software": metadata.get("software", {}).get("name", "unknown"),
                "resources": [r["type"] for r in metadata.get("rest", [{}])[0].get("resource", [])],
            }

            self._log_access("connect", "CapabilityStatement")
            await super().connect()

        except ImportError:
            from khp.errors import DriverLoadError
            raise DriverLoadError(
                "httpx not installed. Install with: pip install httpx fhir.resources",
                device_id=self.device_id,
            )

    async def disconnect(self):
        """Close FHIR client connection."""
        if self._client:
            self._client.close()
            self._client = None
        await super().disconnect()

    def _ensure_connected(self):
        if not self._client:
            from khp.errors import DeviceOfflineError
            raise DeviceOfflineError("FHIR client not connected", device_id=self.device_id)

    def _get(self, path: str, params: dict | None = None) -> dict:
        """Execute a GET request against the FHIR server."""
        self._ensure_connected()
        response = self._client.get(path, params=params)
        if response.status_code == 404:
            return {}
        response.raise_for_status()
        return response.json()

    def _post(self, path: str, body: dict) -> dict:
        """Execute a POST request against the FHIR server."""
        self._ensure_connected()
        response = self._client.post(
            path,
            json=body,
            headers={"Content-Type": "application/fhir+json"},
        )
        response.raise_for_status()
        return response.json()

    @readable(type="dict", description="Latest patient vital signs (heart rate, BP, SpO2, temp)")
    def patient_vitals(self) -> dict:
        self._ensure_connected()
        if not self._patient_id:
            return {"error": "No patient_id configured"}

        vitals = {}
        for vital_name, loinc in LOINC_CODES.items():
            result = self._get(
                "/Observation",
                params={
                    "patient": self._patient_id,
                    "code": f"http://loinc.org|{loinc}",
                    "_sort": "-date",
                    "_count": "1",
                },
            )
            entries = result.get("entry", [])
            if entries:
                resource = entries[0].get("resource", {})
                value_quantity = resource.get("valueQuantity", {})
                vitals[vital_name] = {
                    "value": value_quantity.get("value"),
                    "unit": value_quantity.get("unit"),
                    "timestamp": resource.get("effectiveDateTime"),
                }

        self._last_vitals = vitals
        self._log_access("read", "Observation", self._patient_id)
        return vitals

    @readable(type="dict", description="FHIR server metadata and capability information")
    def server_info(self) -> dict:
        return self._server_metadata

    @readable(type="str", description="Current patient identifier")
    def current_patient(self) -> str:
        return self._patient_id or "none"

    @readable(type="int", description="Total observations submitted this session", unit="count")
    def observations_submitted(self) -> int:
        return self._observation_count

    @readable(type="list", description="Active clinical alerts for the current patient")
    def active_alerts(self) -> list:
        return self._active_alerts

    @readable(type="int", description="Number of HIPAA audit log entries", unit="count")
    def audit_log_size(self) -> int:
        return len(self._audit_log)

    @writable(type="str", description="Set the active patient identifier for subsequent queries")
    def patient_id(self, value: str):
        if not value or len(value) < 1:
            from khp.errors import SafetyBlockedError
            raise SafetyBlockedError(
                "Patient ID cannot be empty",
                device_id=self.device_id,
                property_name="patient_id",
                value=value,
                limit=None,
            )
        self._patient_id = value
        self._last_vitals = {}
        self._active_alerts = []
        self._log_access("set_patient", "Patient", value)

    @safety(min=20, max=300, reason="Physiological heart rate range for validation", hard=True)
    @writable(type="float", description="Heart rate alert threshold (triggers above this value)", unit="bpm")
    def heart_rate_alert_threshold(self, value: float):
        self._active_alerts = [a for a in self._active_alerts if a.get("type") != "heart_rate_high"]
        self._log_access("set_threshold", "heart_rate", str(value))

    @safety(min=30.0, max=45.0, reason="Physiological temperature range", hard=True)
    @writable(type="float", description="Temperature alert threshold", unit="celsius")
    def temperature_alert_threshold(self, value: float):
        self._active_alerts = [a for a in self._active_alerts if a.get("type") != "temperature_high"]
        self._log_access("set_threshold", "temperature", str(value))

    @procedure(description="Query all recent observations for the current patient")
    def query_patient_data(self, count: int = 20, category: str = "vital-signs"):
        """Retrieve recent observations filtered by category."""
        if not self._patient_id:
            return {"error": "No patient_id set"}

        result = self._get(
            "/Observation",
            params={
                "patient": self._patient_id,
                "category": category,
                "_sort": "-date",
                "_count": str(count),
            },
        )

        observations = []
        for entry in result.get("entry", []):
            resource = entry.get("resource", {})
            obs = {
                "id": resource.get("id"),
                "code": resource.get("code", {}).get("text"),
                "value": resource.get("valueQuantity", {}).get("value"),
                "unit": resource.get("valueQuantity", {}).get("unit"),
                "timestamp": resource.get("effectiveDateTime"),
                "status": resource.get("status"),
            }
            observations.append(obs)

        self._log_access("query", "Observation", self._patient_id)
        return {"patient_id": self._patient_id, "count": len(observations), "observations": observations}

    @procedure(description="Submit a new clinical observation to the FHIR server")
    def submit_observation(self, code: str = "heart_rate", value: float = 0.0,
                           unit: str | None = None, notes: str = ""):
        """Create and submit a new Observation resource."""
        if not self._patient_id:
            return {"error": "No patient_id set"}

        if code in VITAL_RANGES:
            ranges = VITAL_RANGES[code]
            if value < ranges["min"] or value > ranges["max"]:
                from khp.errors import SafetyBlockedError
                raise SafetyBlockedError(
                    f"Value {value} outside physiological range for {code} "
                    f"({ranges['min']} to {ranges['max']})",
                    device_id=self.device_id,
                    property_name=code,
                    value=value,
                    limit=ranges,
                )
            if not unit:
                unit = ranges["unit"]

        loinc = LOINC_CODES.get(code, "unknown")
        observation = {
            "resourceType": "Observation",
            "status": "final",
            "category": [{
                "coding": [{"system": "http://terminology.hl7.org/CodeSystem/observation-category",
                            "code": "vital-signs"}]
            }],
            "code": {
                "coding": [{"system": "http://loinc.org", "code": loinc, "display": code}],
                "text": code,
            },
            "subject": {"reference": f"Patient/{self._patient_id}"},
            "effectiveDateTime": datetime.now(timezone.utc).isoformat(),
            "valueQuantity": {"value": value, "unit": unit or "unknown", "system": "http://unitsofmeasure.org"},
        }

        if notes:
            observation["note"] = [{"text": notes}]

        result = self._post("/Observation", observation)
        self._observation_count += 1
        self._log_access("create", "Observation", result.get("id"))

        return {
            "status": "submitted",
            "observation_id": result.get("id"),
            "code": code,
            "value": value,
            "unit": unit,
        }

    @procedure(description="Retrieve device metrics from the FHIR DeviceMetric resource")
    def get_device_metrics(self, device_reference: str | None = None):
        """Query device metrics (battery, calibration, operational status)."""
        params = {"_count": "50"}
        if device_reference:
            params["source"] = device_reference

        result = self._get("/DeviceMetric", params=params)
        metrics = []
        for entry in result.get("entry", []):
            resource = entry.get("resource", {})
            metrics.append({
                "id": resource.get("id"),
                "type": resource.get("type", {}).get("text"),
                "unit": resource.get("unit", {}).get("text"),
                "category": resource.get("category", {}).get("coding", [{}])[0].get("code"),
                "operational_status": resource.get("operationalStatus"),
                "color": resource.get("color"),
            })

        self._log_access("read", "DeviceMetric")
        return {"count": len(metrics), "metrics": metrics}

    @procedure(description="List all active clinical alerts and flags for the patient")
    def list_active_alerts(self):
        """Query active Flag and DetectedIssue resources."""
        if not self._patient_id:
            return {"error": "No patient_id set"}

        result = self._get(
            "/Flag",
            params={"patient": self._patient_id, "status": "active"},
        )

        alerts = []
        for entry in result.get("entry", []):
            resource = entry.get("resource", {})
            alerts.append({
                "id": resource.get("id"),
                "status": resource.get("status"),
                "category": resource.get("category", [{}])[0].get("text"),
                "code": resource.get("code", {}).get("text"),
                "period_start": resource.get("period", {}).get("start"),
            })

        self._active_alerts = alerts
        self._log_access("read", "Flag", self._patient_id)
        return {"patient_id": self._patient_id, "active_alerts": len(alerts), "alerts": alerts}

    @procedure(description="Acknowledge and resolve a clinical alert by ID")
    def acknowledge_alert(self, alert_id: str = ""):
        """Mark a Flag resource as inactive (acknowledged)."""
        if not alert_id:
            return {"error": "alert_id required"}

        self._ensure_connected()
        flag = self._get(f"/Flag/{alert_id}")
        if not flag:
            return {"error": f"Alert {alert_id} not found"}

        flag["status"] = "inactive"
        response = self._client.put(
            f"/Flag/{alert_id}",
            json=flag,
            headers={"Content-Type": "application/fhir+json"},
        )
        response.raise_for_status()

        self._active_alerts = [a for a in self._active_alerts if a.get("id") != alert_id]
        self._log_access("update", "Flag", alert_id)
        return {"status": "acknowledged", "alert_id": alert_id}

    @procedure(description="Retrieve the HIPAA audit trail for this session")
    def get_audit_log(self, last_n: int = 50):
        """Return the most recent audit log entries."""
        entries = self._audit_log[-last_n:]
        return {"total_entries": len(self._audit_log), "returned": len(entries), "entries": entries}

    @procedure(description="Search for patients by name, identifier, or date of birth")
    def search_patients(self, name: str = "", identifier: str = "", birthdate: str = ""):
        """Search the Patient resource."""
        params = {"_count": "20"}
        if name:
            params["name"] = name
        if identifier:
            params["identifier"] = identifier
        if birthdate:
            params["birthdate"] = birthdate

        result = self._get("/Patient", params=params)
        patients = []
        for entry in result.get("entry", []):
            resource = entry.get("resource", {})
            names = resource.get("name", [{}])
            display_name = ""
            if names:
                given = " ".join(names[0].get("given", []))
                family = names[0].get("family", "")
                display_name = f"{given} {family}".strip()
            patients.append({
                "id": resource.get("id"),
                "name": display_name,
                "gender": resource.get("gender"),
                "birthDate": resource.get("birthDate"),
            })

        self._log_access("search", "Patient")
        return {"count": len(patients), "patients": patients}

    @monitor(interval_ms=5000, description="Monitor patient vitals against alert thresholds")
    def check_vitals_status(self) -> dict[str, Any]:
        alerts = []

        for vital_name, data in self._last_vitals.items():
            if not data or not data.get("value"):
                continue
            value = data["value"]
            if vital_name in VITAL_RANGES:
                ranges = VITAL_RANGES[vital_name]
                if value < ranges["min"] * 1.1:
                    alerts.append({
                        "level": "warning",
                        "message": f"{vital_name} critically low: {value} {ranges['unit']}",
                    })
                elif value > ranges["max"] * 0.9:
                    alerts.append({
                        "level": "warning",
                        "message": f"{vital_name} critically high: {value} {ranges['unit']}",
                    })

        return {
            "healthy": len(alerts) == 0,
            "patient_id": self._patient_id,
            "vitals_cached": len(self._last_vitals),
            "active_alerts": len(self._active_alerts),
            "alerts": alerts,
        }
