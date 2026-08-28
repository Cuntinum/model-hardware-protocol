"""Tests for KHP error types and error serialization."""

import pytest
from khp.errors import (
    KHPError, DeviceNotFoundError, PropertyNotFoundError,
    SafetyBlockedError, SafetyClampedError, ConfirmationRequiredError,
    PreconditionFailedError, DeviceBusyError, DeviceOfflineError,
    TimeoutError, HardwareError,
)


class TestErrorBase:
    def test_khp_error_message(self):
        e = KHPError("Something broke", device_id="dev_1")
        assert str(e) == "Something broke"
        assert e.device_id == "dev_1"

    def test_to_dict(self):
        e = KHPError("Failure", device_id="dev_2")
        d = e.to_dict()
        assert d["success"] is False
        assert d["error"]["code"] == "KHP_ERROR"
        assert d["error"]["message"] == "Failure"
        assert d["error"]["device_id"] == "dev_2"


class TestSpecificErrors:
    def test_device_not_found(self):
        e = DeviceNotFoundError("No such device", device_id="ghost_1")
        assert e.code == "DEVICE_NOT_FOUND"
        assert e.to_dict()["error"]["code"] == "DEVICE_NOT_FOUND"

    def test_property_not_found(self):
        e = PropertyNotFoundError("Bad prop", device_id="dev_1")
        assert e.code == "PROPERTY_NOT_FOUND"

    def test_safety_blocked(self):
        e = SafetyBlockedError(
            "Too hot", device_id="heater_1",
            property="temp", requested_value=300, limit_value=120,
        )
        assert e.code == "SAFETY_BLOCKED"
        assert e.property == "temp"
        assert e.requested_value == 300
        d = e.to_dict()
        assert d["error"]["requested_value"] == 300

    def test_safety_clamped(self):
        e = SafetyClampedError(
            "Clamped", device_id="dev_1",
            property="power", requested_value=150, actual_value=100,
        )
        assert e.code == "SAFETY_CLAMPED"
        assert e.actual_value == 100

    def test_confirmation_required(self):
        e = ConfirmationRequiredError(
            "Needs approval", device_id="laser_1",
            procedure="fire", confirmation_id="abc123",
        )
        assert e.code == "CONFIRMATION_REQUIRED"
        assert e.confirmation_id == "abc123"

    def test_precondition_failed(self):
        e = PreconditionFailedError("Not ready", device_id="arm_1")
        assert e.code == "PRECONDITION_FAILED"

    def test_device_busy(self):
        e = DeviceBusyError("In use", device_id="printer_1")
        assert e.code == "DEVICE_BUSY"

    def test_device_offline(self):
        e = DeviceOfflineError("Disconnected", device_id="sensor_1")
        assert e.code == "DEVICE_OFFLINE"

    def test_timeout(self):
        e = TimeoutError("Took too long", device_id="slow_1")
        assert e.code == "TIMEOUT"

    def test_hardware_error(self):
        e = HardwareError("Motor stalled", device_id="motor_1")
        assert e.code == "HARDWARE_ERROR"


class TestErrorInheritance:
    def test_all_inherit_from_khp_error(self):
        errors = [
            DeviceNotFoundError, PropertyNotFoundError, SafetyBlockedError,
            SafetyClampedError, ConfirmationRequiredError, PreconditionFailedError,
            DeviceBusyError, DeviceOfflineError, TimeoutError, HardwareError,
        ]
        for cls in errors:
            assert issubclass(cls, KHPError)
            assert issubclass(cls, Exception)
