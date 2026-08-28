"""KHP Driver base class — the foundation for all hardware drivers."""

import asyncio
import json
import uuid
import time
from abc import ABC
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set
from datetime import datetime, timezone


class DeviceStatus(Enum):
    ONLINE = "online"
    OFFLINE = "offline"
    BUSY = "busy"
    MAINTENANCE = "maintenance"
    ERROR = "error"


class ConnectionType(Enum):
    REST = "REST"
    SERIAL = "serial"
    USB = "USB"
    TCP = "TCP"
    FILE_DROP = "file_drop"
    COM = "COM"
    SDK = "SDK"
    GUI = "GUI"
    MQTT = "MQTT"
    MODBUS = "modbus"
    GPIO = "GPIO"


@dataclass
class SafetyLimit:
    property_name: str
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    reason: str = ""
    hard: bool = True


@dataclass
class PropertyMeta:
    name: str
    type: str
    description: str = ""
    unit: Optional[str] = None
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    step: Optional[float] = None
    enum_values: Optional[List[str]] = None
    pattern: Optional[str] = None
    default: Any = None
    poll_interval_ms: Optional[int] = None
    requires_confirmation: bool = False


@dataclass
class ProcedureMeta:
    name: str
    description: str = ""
    params: Dict[str, dict] = field(default_factory=dict)
    preconditions: List[str] = field(default_factory=list)
    postconditions: List[str] = field(default_factory=list)
    estimated_duration_s: Optional[float] = None
    requires_confirmation: bool = False
    idempotent: bool = False
    reversible: bool = False


@dataclass
class Job:
    job_id: str
    device_id: str
    procedure: str
    params: dict
    status: str = "running"
    started_at: str = ""
    completed_at: Optional[str] = None
    result: Any = None
    error: Optional[str] = None


class Driver(ABC):
    """Base class for all KHP hardware drivers.

    Subclass this and use @readable, @writable, @procedure decorators
    to expose device capabilities.
    """

    name: str = "Unknown Device"
    version: str = "0.1.0"
    device_type: str = "custom"
    description: str = ""
    connection_type: ConnectionType = ConnectionType.REST

    def __init__(self, device_id: Optional[str] = None, **config):
        self.device_id = device_id or f"{self.device_type}_{uuid.uuid4().hex[:8]}"
        self.config = config
        self.status = DeviceStatus.OFFLINE
        self._jobs: Dict[str, Job] = {}
        self._locks: Dict[str, str] = {}
        self._event_handlers: Dict[str, List[Callable]] = {}
        self._safety_limits: List[SafetyLimit] = []
        self._readable_props: Dict[str, PropertyMeta] = {}
        self._writable_props: Dict[str, PropertyMeta] = {}
        self._procedures: Dict[str, ProcedureMeta] = {}
        self._monitors: Dict[str, dict] = {}
        self._tags: Dict[str, str] = {}
        self._audit_log: List[dict] = []
        self._collect_metadata()

    def _collect_metadata(self):
        """Collect decorated methods into property/procedure registries."""
        for attr_name in dir(self.__class__):
            attr = getattr(self.__class__, attr_name, None)
            if attr is None:
                continue
            if hasattr(attr, "_khp_readable"):
                meta = attr._khp_readable
                self._readable_props[meta.name] = meta
            if hasattr(attr, "_khp_writable"):
                meta = attr._khp_writable
                self._writable_props[meta.name] = meta
            if hasattr(attr, "_khp_procedure"):
                meta = attr._khp_procedure
                self._procedures[meta.name] = meta
            if hasattr(attr, "_khp_safety"):
                self._safety_limits.extend(attr._khp_safety)
            if hasattr(attr, "_khp_monitor"):
                self._monitors[attr_name] = attr._khp_monitor

    async def connect(self):
        """Establish connection to the physical device. Override in subclass."""
        self.status = DeviceStatus.ONLINE

    async def disconnect(self):
        """Cleanly disconnect from the device. Override in subclass."""
        self.status = DeviceStatus.OFFLINE

    async def health_check(self) -> bool:
        """Check if device is responding. Override for custom health checks."""
        return self.status == DeviceStatus.ONLINE

    async def emergency_stop(self):
        """Emergency stop — halt all operations immediately. Override in subclass."""
        for job_id, job in self._jobs.items():
            if job.status == "running":
                job.status = "aborted"
        self.status = DeviceStatus.ERROR
        self._emit_event("emergency_stop", {"device_id": self.device_id})

    def read(self, property_name: str) -> dict:
        """READ primitive — get current value of a device property."""
        if property_name not in self._readable_props:
            from khp.errors import PropertyNotFoundError
            raise PropertyNotFoundError(
                f"Property '{property_name}' not readable on {self.device_id}",
                device_id=self.device_id,
            )

        method = getattr(self, property_name)
        value = method()
        meta = self._readable_props[property_name]

        result = {
            "value": value,
            "type": meta.type,
            "unit": meta.unit,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self._log_operation("READ", property_name, result=result)
        return result

    def write(self, property_name: str, value: Any) -> dict:
        """WRITE primitive — set a device property value with safety checks."""
        if property_name not in self._writable_props:
            from khp.errors import PropertyNotFoundError
            raise PropertyNotFoundError(
                f"Property '{property_name}' not writable on {self.device_id}",
                device_id=self.device_id,
            )

        meta = self._writable_props[property_name]

        if meta.requires_confirmation:
            from khp.errors import ConfirmationRequiredError
            conf_id = uuid.uuid4().hex[:12]
            raise ConfirmationRequiredError(
                f"Writing '{property_name}' requires human confirmation",
                device_id=self.device_id,
                procedure=property_name,
                confirmation_id=conf_id,
            )

        safety_result = self._check_safety(property_name, value)

        if safety_result == "blocked":
            from khp.errors import SafetyBlockedError
            limit = self._get_limit(property_name)
            raise SafetyBlockedError(
                f"Value {value} exceeds hard limit for '{property_name}'",
                device_id=self.device_id,
                property=property_name,
                requested_value=value,
                limit_value=limit,
            )

        if safety_result == "clamped":
            value = self._clamp_value(property_name, value)

        method = getattr(self, property_name)
        method(value)

        result = {
            "success": True,
            "actual_value": value,
            "safety_check": safety_result,
        }
        self._log_operation("WRITE", property_name, value=value, result=result)
        return result

    async def execute(self, procedure_name: str, params: dict = None) -> dict:
        """EXECUTE primitive — run a named procedure."""
        if procedure_name not in self._procedures:
            from khp.errors import PropertyNotFoundError
            raise PropertyNotFoundError(
                f"Procedure '{procedure_name}' not found on {self.device_id}",
                device_id=self.device_id,
            )

        meta = self._procedures[procedure_name]
        params = params or {}

        if meta.requires_confirmation:
            from khp.errors import ConfirmationRequiredError
            conf_id = uuid.uuid4().hex[:12]
            raise ConfirmationRequiredError(
                f"Procedure '{procedure_name}' requires human confirmation",
                device_id=self.device_id,
                procedure=procedure_name,
                confirmation_id=conf_id,
            )

        for precondition in meta.preconditions:
            if not self._check_precondition(precondition):
                from khp.errors import PreconditionFailedError
                raise PreconditionFailedError(
                    f"Precondition '{precondition}' not met for {procedure_name}",
                    device_id=self.device_id,
                )

        job = Job(
            job_id=f"j_{uuid.uuid4().hex[:12]}",
            device_id=self.device_id,
            procedure=procedure_name,
            params=params,
            status="running",
            started_at=datetime.now(timezone.utc).isoformat(),
        )
        self._jobs[job.job_id] = job

        try:
            method = getattr(self, procedure_name)
            if asyncio.iscoroutinefunction(method):
                result = await method(**params)
            else:
                result = method(**params)
            job.status = "completed"
            job.result = result
            job.completed_at = datetime.now(timezone.utc).isoformat()
        except Exception as e:
            job.status = "failed"
            job.error = str(e)
            job.completed_at = datetime.now(timezone.utc).isoformat()

        self._log_operation("EXECUTE", procedure_name, params=params,
                           result={"job_id": job.job_id, "status": job.status})
        return {"job_id": job.job_id, "status": job.status, "result": job.result}

    def _check_safety(self, property_name: str, value: Any) -> str:
        """Check value against safety limits. Returns: passed/clamped/blocked."""
        for limit in self._safety_limits:
            if limit.property_name != property_name:
                continue
            if limit.hard:
                if limit.max_value is not None and value > limit.max_value:
                    return "blocked"
                if limit.min_value is not None and value < limit.min_value:
                    return "blocked"
            else:
                if limit.max_value is not None and value > limit.max_value:
                    return "clamped"
                if limit.min_value is not None and value < limit.min_value:
                    return "clamped"
        return "passed"

    def _clamp_value(self, property_name: str, value: Any) -> Any:
        """Clamp value to soft limits."""
        for limit in self._safety_limits:
            if limit.property_name != property_name or limit.hard:
                continue
            if limit.max_value is not None and value > limit.max_value:
                value = limit.max_value
            if limit.min_value is not None and value < limit.min_value:
                value = limit.min_value
        return value

    def _get_limit(self, property_name: str) -> Any:
        """Get the relevant limit value for error reporting."""
        for limit in self._safety_limits:
            if limit.property_name == property_name and limit.hard:
                return {"min": limit.min_value, "max": limit.max_value}
        return None

    def _check_precondition(self, precondition: str) -> bool:
        """Check a precondition. Override for custom logic."""
        check_method = getattr(self, f"_check_{precondition}", None)
        if check_method:
            return check_method()
        return True

    def _emit_event(self, event_type: str, data: dict):
        """Emit an event to all subscribers."""
        event = {
            "event": event_type,
            "device_id": self.device_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **data,
        }
        for handler in self._event_handlers.get(event_type, []):
            handler(event)
        for handler in self._event_handlers.get("*", []):
            handler(event)

    def on_event(self, event_type: str, handler: Callable):
        """Register an event handler."""
        if event_type not in self._event_handlers:
            self._event_handlers[event_type] = []
        self._event_handlers[event_type].append(handler)

    def _log_operation(self, op: str, target: str, **details):
        """Append to audit log."""
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "operation": op,
            "device_id": self.device_id,
            "target": target,
            **details,
        }
        self._audit_log.append(entry)
        if len(self._audit_log) > 10000:
            self._audit_log = self._audit_log[-5000:]

    def get_manifest(self) -> dict:
        """Generate the capabilities manifest for this driver."""
        return {
            "$schema": "https://khp.dev/schema/manifest/v1",
            "device_id": self.device_id,
            "name": self.name,
            "type": self.device_type,
            "driver": self.__class__.__module__ + "." + self.__class__.__name__,
            "version": self.version,
            "description": self.description,
            "readable": {
                name: {
                    "type": meta.type,
                    "description": meta.description,
                    "unit": meta.unit,
                    "range": {"min": meta.min_value, "max": meta.max_value},
                    "poll_interval_ms": meta.poll_interval_ms,
                }
                for name, meta in self._readable_props.items()
            },
            "writable": {
                name: {
                    "type": meta.type,
                    "description": meta.description,
                    "unit": meta.unit,
                    "min": meta.min_value,
                    "max": meta.max_value,
                    "step": meta.step,
                    "enum": meta.enum_values,
                    "default": meta.default,
                    "requires_confirmation": meta.requires_confirmation,
                }
                for name, meta in self._writable_props.items()
            },
            "procedures": {
                name: {
                    "description": meta.description,
                    "params": meta.params,
                    "preconditions": meta.preconditions,
                    "estimated_duration_s": meta.estimated_duration_s,
                    "requires_confirmation": meta.requires_confirmation,
                    "idempotent": meta.idempotent,
                }
                for name, meta in self._procedures.items()
            },
            "safety": {
                "hard_limits": {
                    f"{l.property_name}_limit": {
                        "property": l.property_name,
                        "max": l.max_value,
                        "min": l.min_value,
                        "reason": l.reason,
                    }
                    for l in self._safety_limits if l.hard
                },
                "soft_limits": {
                    f"{l.property_name}_soft": {
                        "property": l.property_name,
                        "recommended_max": l.max_value,
                        "recommended_min": l.min_value,
                        "reason": l.reason,
                    }
                    for l in self._safety_limits if not l.hard
                },
                "emergency_stop": {"supported": True},
            },
            "metadata": {
                "connection": {
                    "type": self.connection_type.value,
                    **{k: v for k, v in self.config.items()
                       if k in ("endpoint", "host", "port", "baud_rate", "timeout_ms")},
                },
                "tags": self._tags,
            },
        }

    def set_tags(self, **tags: str):
        """Set natural-language tags for this device."""
        self._tags.update(tags)

    def __repr__(self):
        return f"<{self.__class__.__name__} id={self.device_id} status={self.status.value}>"
