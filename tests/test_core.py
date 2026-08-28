"""Tests for KHP core Driver class — read, write, execute, safety, manifest."""

import pytest
from khp.errors import (
    PropertyNotFoundError, SafetyBlockedError,
    ConfirmationRequiredError, PreconditionFailedError,
)


class TestDriverInit:
    def test_device_id_generated(self, heater):
        assert heater.device_id == "test_heater_1"

    def test_status_offline_initially(self, heater):
        from khp.core import DeviceStatus
        assert heater.status == DeviceStatus.OFFLINE

    def test_readable_props_collected(self, heater):
        assert "temperature" in heater._readable_props
        assert "power" in heater._readable_props

    def test_writable_props_collected(self, heater):
        assert "target_temperature" in heater._writable_props

    def test_procedures_collected(self, heater):
        assert "power_on" in heater._procedures
        assert "power_off" in heater._procedures
        assert "calibrate" in heater._procedures


class TestRead:
    def test_read_returns_value(self, heater):
        result = heater.read("temperature")
        assert result["value"] == 22.0
        assert result["type"] == "float"
        assert result["unit"] == "celsius"
        assert "timestamp" in result

    def test_read_bool_property(self, heater):
        result = heater.read("power")
        assert result["value"] is False
        assert result["type"] == "bool"

    def test_read_unknown_property_raises(self, heater):
        with pytest.raises(PropertyNotFoundError) as exc_info:
            heater.read("nonexistent")
        assert "nonexistent" in str(exc_info.value)


class TestWrite:
    def test_write_sets_value(self, heater):
        result = heater.write("target_temperature", 50.0)
        assert result["success"] is True
        assert result["actual_value"] == 50.0
        assert heater._target == 50.0

    def test_write_unknown_property_raises(self, heater):
        with pytest.raises(PropertyNotFoundError):
            heater.write("nonexistent", 42)

    def test_write_confirmation_required(self, heater):
        with pytest.raises(ConfirmationRequiredError) as exc_info:
            heater.write("dangerous_setting", 99.0)
        assert exc_info.value.confirmation_id

    def test_write_safety_blocked(self, heater):
        with pytest.raises(SafetyBlockedError):
            heater.write("target_temperature", 200.0)

    def test_write_safety_blocked_below_min(self, heater):
        with pytest.raises(SafetyBlockedError):
            heater.write("target_temperature", -10.0)

    def test_write_within_limits_passes(self, heater):
        result = heater.write("target_temperature", 80.0)
        assert result["safety_check"] == "passed"


class TestExecute:
    @pytest.mark.asyncio
    async def test_execute_procedure(self, heater):
        result = await heater.execute("power_on")
        assert result["status"] == "completed"
        assert result["result"] == {"power": True}
        assert heater._power is True

    @pytest.mark.asyncio
    async def test_execute_with_params(self, heater):
        heater._power = True
        result = await heater.execute("calibrate", {"offset": 1.5})
        assert result["status"] == "completed"
        assert result["result"]["offset"] == 1.5

    @pytest.mark.asyncio
    async def test_execute_unknown_procedure(self, heater):
        with pytest.raises(PropertyNotFoundError):
            await heater.execute("nonexistent")

    @pytest.mark.asyncio
    async def test_execute_precondition_fails(self, heater):
        heater._power = False
        with pytest.raises(PreconditionFailedError):
            await heater.execute("calibrate")

    @pytest.mark.asyncio
    async def test_execute_precondition_passes(self, heater):
        heater._power = True
        result = await heater.execute("calibrate", {"offset": 0.0})
        assert result["status"] == "completed"


class TestManifest:
    def test_manifest_structure(self, heater):
        m = heater.get_manifest()
        assert m["device_id"] == "test_heater_1"
        assert m["name"] == "Mock Heater"
        assert m["type"] == "heater"
        assert "temperature" in m["readable"]
        assert "target_temperature" in m["writable"]
        assert "power_on" in m["procedures"]
        assert "hard_limits" in m["safety"]

    def test_manifest_safety_limits(self, heater):
        m = heater.get_manifest()
        hard = m["safety"]["hard_limits"]
        assert len(hard) > 0
        limit = list(hard.values())[0]
        assert limit["property"] == "target_temperature"
        assert limit["max"] == 120.0

    def test_manifest_schema_field(self, heater):
        m = heater.get_manifest()
        assert m["$schema"] == "https://khp.dev/schema/manifest/v1"


class TestConnect:
    @pytest.mark.asyncio
    async def test_connect_sets_online(self, heater):
        from khp.core import DeviceStatus
        await heater.connect()
        assert heater.status == DeviceStatus.ONLINE

    @pytest.mark.asyncio
    async def test_disconnect_sets_offline(self, heater):
        await heater.connect()
        await heater.disconnect()
        from khp.core import DeviceStatus
        assert heater.status == DeviceStatus.OFFLINE

    @pytest.mark.asyncio
    async def test_health_check(self, heater):
        assert await heater.health_check() is False
        await heater.connect()
        assert await heater.health_check() is True


class TestEmergencyStop:
    @pytest.mark.asyncio
    async def test_emergency_stop_aborts_jobs(self, heater):
        from khp.core import DeviceStatus, Job
        from datetime import datetime, timezone
        job = Job(
            job_id="j_test", device_id=heater.device_id,
            procedure="power_on", params={}, status="running",
            started_at=datetime.now(timezone.utc).isoformat(),
        )
        heater._jobs["j_test"] = job
        await heater.emergency_stop()
        assert job.status == "aborted"
        assert heater.status == DeviceStatus.ERROR


class TestAuditLog:
    def test_read_logged(self, heater):
        heater.read("temperature")
        assert len(heater._audit_log) == 1
        assert heater._audit_log[0]["operation"] == "READ"

    def test_write_logged(self, heater):
        heater.write("target_temperature", 50.0)
        assert len(heater._audit_log) == 1
        assert heater._audit_log[0]["operation"] == "WRITE"

    @pytest.mark.asyncio
    async def test_execute_logged(self, heater):
        await heater.execute("power_on")
        assert any(e["operation"] == "EXECUTE" for e in heater._audit_log)
