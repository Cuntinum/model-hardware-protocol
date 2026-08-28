"""Shared fixtures for KHP test suite."""

import sys
import os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "sdk", "python"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "drivers", "docker-sim"))

from khp import Driver, readable, writable, procedure, safety
from khp.core import ConnectionType, SafetyLimit
from khp.state_bus import StateBus, Slot, Transform
from khp.errors import (
    KHPError, DeviceNotFoundError, PropertyNotFoundError,
    SafetyBlockedError, ConfirmationRequiredError, PreconditionFailedError,
)


class MockHeater(Driver):
    """A minimal test driver simulating a heater."""

    name = "Mock Heater"
    version = "0.1.0"
    device_type = "heater"
    description = "Test heater for unit tests"
    connection_type = ConnectionType.REST

    def __init__(self, **kwargs):
        super().__init__(device_id="test_heater_1", **kwargs)
        self._temp = 22.0
        self._target = 22.0
        self._power = False

    @readable(type="float", description="Current temperature", unit="celsius")
    def temperature(self) -> float:
        return self._temp

    @readable(type="bool", description="Power state")
    def power(self) -> bool:
        return self._power

    @safety(max=120.0, min=0.0, reason="Prevent overheating", hard=True)
    @writable(type="float", description="Target temperature", unit="celsius", min_value=0, max_value=120)
    def target_temperature(self, value: float):
        self._target = value

    @writable(type="float", description="Dangerous setting", requires_confirmation=True)
    def dangerous_setting(self, value: float):
        pass

    @procedure(description="Turn power on")
    def power_on(self):
        self._power = True
        return {"power": True}

    @procedure(description="Turn power off")
    def power_off(self):
        self._power = False
        return {"power": False}

    @procedure(description="Run calibration", preconditions=["power_is_on"])
    def calibrate(self, offset: float = 0.0):
        self._temp += offset
        return {"calibrated": True, "offset": offset}

    def _check_power_is_on(self) -> bool:
        return self._power


@pytest.fixture
def heater():
    return MockHeater()


@pytest.fixture
def bus():
    return StateBus()
