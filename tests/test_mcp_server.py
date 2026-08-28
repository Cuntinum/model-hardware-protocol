"""Tests for KHP MCP Server — tool dispatch and error handling."""

import sys
import os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "mcp"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "sdk", "python"))

from khp.state_bus import StateBus
from khp.discovery import DeviceRegistry
from khp.errors import DeviceNotFoundError


class MockMCPServer:
    """Simplified MCP server for testing without full import chain."""

    def __init__(self, registry, bus=None):
        self._registry = registry
        self._bus = bus or StateBus()

    async def handle_read(self, device_id: str, prop: str):
        driver = self._registry.get_driver(device_id)
        if not driver:
            raise DeviceNotFoundError(f"Device {device_id} not found", device_id=device_id)
        return driver.read(prop)

    async def handle_write(self, device_id: str, prop: str, value):
        driver = self._registry.get_driver(device_id)
        if not driver:
            raise DeviceNotFoundError(f"Device {device_id} not found", device_id=device_id)
        return driver.write(prop, value)

    async def handle_execute(self, device_id: str, procedure: str, params=None):
        driver = self._registry.get_driver(device_id)
        if not driver:
            raise DeviceNotFoundError(f"Device {device_id} not found", device_id=device_id)
        return await driver.execute(procedure, params or {})

    async def handle_emergency_stop(self, device_id=None):
        stopped = []
        if device_id:
            driver = self._registry.get_driver(device_id)
            if driver:
                await driver.emergency_stop()
                stopped.append(device_id)
        else:
            for dev in self._registry.list_devices():
                driver = self._registry.get_driver(dev.device_id)
                if driver:
                    await driver.emergency_stop()
                    stopped.append(dev.device_id)
        return {"stopped": stopped}


@pytest.fixture
def mcp_server(tmp_path, heater):
    reg = DeviceRegistry(config_dir=tmp_path)
    reg.register(heater)
    return MockMCPServer(reg)


class TestMCPRead:
    @pytest.mark.asyncio
    async def test_read_property(self, mcp_server):
        result = await mcp_server.handle_read("test_heater_1", "temperature")
        assert result["value"] == 22.0

    @pytest.mark.asyncio
    async def test_read_unknown_device(self, mcp_server):
        with pytest.raises(DeviceNotFoundError):
            await mcp_server.handle_read("ghost", "temperature")


class TestMCPWrite:
    @pytest.mark.asyncio
    async def test_write_property(self, mcp_server):
        result = await mcp_server.handle_write("test_heater_1", "target_temperature", 60.0)
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_write_unknown_device(self, mcp_server):
        with pytest.raises(DeviceNotFoundError):
            await mcp_server.handle_write("ghost", "temp", 50.0)


class TestMCPExecute:
    @pytest.mark.asyncio
    async def test_execute_procedure(self, mcp_server):
        result = await mcp_server.handle_execute("test_heater_1", "power_on")
        assert result["status"] == "completed"

    @pytest.mark.asyncio
    async def test_execute_unknown_device(self, mcp_server):
        with pytest.raises(DeviceNotFoundError):
            await mcp_server.handle_execute("ghost", "start")


class TestMCPEmergencyStop:
    @pytest.mark.asyncio
    async def test_stop_specific_device(self, mcp_server):
        result = await mcp_server.handle_emergency_stop("test_heater_1")
        assert "test_heater_1" in result["stopped"]

    @pytest.mark.asyncio
    async def test_stop_all(self, mcp_server):
        result = await mcp_server.handle_emergency_stop()
        assert len(result["stopped"]) >= 1
