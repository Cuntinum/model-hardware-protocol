"""Integration tests — full workflow with simulated drivers."""

import sys
import os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "drivers", "docker-sim"))

from driver import SimulatedThermocycler, SimulatedLiquidHandler, SimulatedRoboticArm
from khp.state_bus import StateBus, Transform
from khp.discovery import DeviceRegistry


@pytest.fixture
def thermocycler():
    return SimulatedThermocycler(device_id="tc_integration")


@pytest.fixture
def liquid_handler():
    return SimulatedLiquidHandler(device_id="lh_integration")


@pytest.fixture
def robotic_arm():
    return SimulatedRoboticArm(device_id="arm_integration")


class TestThermocyclerWorkflow:
    @pytest.mark.asyncio
    async def test_full_workflow(self, thermocycler):
        await thermocycler.connect()

        temp = thermocycler.read("block_temperature")
        assert temp["type"] == "float"
        assert temp["unit"] == "celsius"
        assert 15.0 < temp["value"] < 30.0

        thermocycler.write("set_temperature", 95.0)
        target = thermocycler.read("target")
        assert target["value"] == 95.0

        await thermocycler.disconnect()

    def test_manifest_completeness(self, thermocycler):
        m = thermocycler.get_manifest()
        assert m["type"] == "thermocycler"
        assert "block_temperature" in m["readable"]
        assert "set_temperature" in m["writable"]
        assert "hard_limits" in m["safety"]

    def test_safety_blocks_extreme_temp(self, thermocycler):
        from khp.errors import SafetyBlockedError
        with pytest.raises(SafetyBlockedError):
            thermocycler.write("set_temperature", 200.0)


class TestLiquidHandlerWorkflow:
    @pytest.mark.asyncio
    async def test_aspirate_dispense(self, liquid_handler):
        await liquid_handler.connect()

        result = await liquid_handler.execute("aspirate", {"volume_ul": 100.0})
        assert result["status"] == "completed"

        result = await liquid_handler.execute("dispense", {"volume_ul": 50.0})
        assert result["status"] == "completed"

        await liquid_handler.disconnect()

    def test_manifest_has_procedures(self, liquid_handler):
        m = liquid_handler.get_manifest()
        assert "aspirate" in m["procedures"]
        assert "dispense" in m["procedures"]


class TestRoboticArmWorkflow:
    @pytest.mark.asyncio
    async def test_move_and_grip(self, robotic_arm):
        await robotic_arm.connect()

        result = await robotic_arm.execute("move_to", {"x": 100.0, "y": 50.0, "z": 30.0})
        assert result["status"] == "completed"

        pos = robotic_arm.read("position")
        assert pos["value"]["x"] == 100.0

        await robotic_arm.disconnect()


class TestMultiDeviceStateBus:
    def test_device_to_device_via_bus(self, thermocycler, liquid_handler):
        bus = StateBus()
        bus.create_slot("tc_temp", type="float", unit="celsius")
        bus.create_slot("alert_active", type="bool")

        bus.add_transform(Transform(
            transform_id="temp_alert",
            input_slot="tc_temp",
            operation="threshold",
            params={"above": 95.0},
            output_event="overheat",
        ))

        events = []
        bus.on("overheat", lambda e: events.append(e))

        bus.write_slot("tc_temp", 80.0)
        assert len(events) == 0

        bus.write_slot("tc_temp", 100.0)
        assert len(events) == 1


class TestRegistryIntegration:
    def test_multi_device_registry(self, tmp_path, thermocycler, liquid_handler):
        reg = DeviceRegistry(config_dir=tmp_path)
        reg.register(thermocycler)
        reg.register(liquid_handler)

        devices = reg.list_devices()
        assert len(devices) == 2

        tc_devices = reg.list_devices(device_type="thermocycler")
        assert len(tc_devices) == 1
        assert tc_devices[0].device_id == "tc_integration"
