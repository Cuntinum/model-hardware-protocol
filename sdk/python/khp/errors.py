"""KHP error types — maps to protocol error codes."""

from dataclasses import dataclass, field
from typing import Any, Optional


class KHPError(Exception):
    code: str = "KHP_ERROR"

    def __init__(self, message: str, device_id: str = "", **details):
        self.message = message
        self.device_id = device_id
        self.details = details
        super().__init__(message)

    def to_dict(self) -> dict:
        return {
            "success": False,
            "error": {
                "code": self.code,
                "message": self.message,
                "device_id": self.device_id,
                **self.details,
            }
        }


class DeviceNotFoundError(KHPError):
    code = "DEVICE_NOT_FOUND"


class PropertyNotFoundError(KHPError):
    code = "PROPERTY_NOT_FOUND"


class SafetyBlockedError(KHPError):
    code = "SAFETY_BLOCKED"

    def __init__(self, message: str, device_id: str = "",
                 property: str = "", requested_value: Any = None,
                 limit_value: Any = None, **details):
        self.property = property
        self.requested_value = requested_value
        self.limit_value = limit_value
        super().__init__(
            message, device_id,
            property=property,
            requested_value=requested_value,
            limit_value=limit_value,
            **details,
        )


class SafetyClampedError(KHPError):
    code = "SAFETY_CLAMPED"

    def __init__(self, message: str, device_id: str = "",
                 property: str = "", requested_value: Any = None,
                 actual_value: Any = None, **details):
        self.property = property
        self.requested_value = requested_value
        self.actual_value = actual_value
        super().__init__(
            message, device_id,
            property=property,
            requested_value=requested_value,
            actual_value=actual_value,
            **details,
        )


class PreconditionFailedError(KHPError):
    code = "PRECONDITION_FAILED"


class ConfirmationRequiredError(KHPError):
    code = "CONFIRMATION_REQUIRED"

    def __init__(self, message: str, device_id: str = "",
                 procedure: str = "", confirmation_id: str = "", **details):
        self.procedure = procedure
        self.confirmation_id = confirmation_id
        super().__init__(
            message, device_id,
            procedure=procedure,
            confirmation_id=confirmation_id,
            **details,
        )


class DeviceBusyError(KHPError):
    code = "DEVICE_BUSY"


class DeviceOfflineError(KHPError):
    code = "DEVICE_OFFLINE"


class TimeoutError(KHPError):
    code = "TIMEOUT"


class HardwareError(KHPError):
    code = "HARDWARE_ERROR"
