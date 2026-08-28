"""Tests for KHP decorators — @readable, @writable, @procedure, @safety, @monitor."""

import pytest
from khp import Driver, readable, writable, procedure, safety
from khp.decorators import monitor
from khp.core import ConnectionType


class TestReadableDecorator:
    def test_readable_attaches_metadata(self, heater):
        meta = heater._readable_props["temperature"]
        assert meta.type == "float"
        assert meta.unit == "celsius"
        assert meta.description == "Current temperature"

    def test_readable_method_still_callable(self, heater):
        assert heater.temperature() == 22.0


class TestWritableDecorator:
    def test_writable_attaches_metadata(self, heater):
        meta = heater._writable_props["target_temperature"]
        assert meta.type == "float"
        assert meta.unit == "celsius"
        assert meta.min_value == 0
        assert meta.max_value == 120

    def test_writable_method_still_callable(self, heater):
        heater.target_temperature(50.0)
        assert heater._target == 50.0


class TestProcedureDecorator:
    def test_procedure_attaches_metadata(self, heater):
        meta = heater._procedures["calibrate"]
        assert meta.description == "Run calibration"
        assert "power_is_on" in meta.preconditions

    def test_procedure_auto_params(self, heater):
        meta = heater._procedures["calibrate"]
        assert "offset" in meta.params
        assert meta.params["offset"]["type"] == "float"


class TestSafetyDecorator:
    def test_safety_limits_collected(self, heater):
        assert len(heater._safety_limits) > 0
        limit = heater._safety_limits[0]
        assert limit.property_name == "target_temperature"
        assert limit.max_value == 120.0
        assert limit.min_value == 0.0
        assert limit.hard is True


class TestMonitorDecorator:
    def test_monitor_attached(self):
        class MonitoredDevice(Driver):
            name = "Monitored"
            device_type = "sensor"
            connection_type = ConnectionType.REST

            @monitor(interval_ms=500, alert_above=95.0, action="emergency_stop")
            @readable(type="float", unit="celsius")
            def temperature(self) -> float:
                return 50.0

        d = MonitoredDevice(device_id="mon_1")
        assert "temperature" in d._monitors
        assert d._monitors["temperature"]["interval_ms"] == 500
        assert d._monitors["temperature"]["alert_above"] == 95.0
        assert d._monitors["temperature"]["action"] == "emergency_stop"


class TestDecoratorStacking:
    def test_safety_and_writable_stack(self):
        class StackedDevice(Driver):
            name = "Stacked"
            device_type = "laser"
            connection_type = ConnectionType.REST

            @safety(max=200.0, reason="Prevents sample bleaching", hard=True)
            @writable(type="float", unit="mW")
            def laser_power(self, value: float):
                self._power = value

        d = StackedDevice(device_id="stack_1")
        assert "laser_power" in d._writable_props
        assert len(d._safety_limits) == 1
        assert d._safety_limits[0].max_value == 200.0
