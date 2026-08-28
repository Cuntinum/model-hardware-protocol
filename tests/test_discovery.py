"""Tests for KHP device discovery and registry."""

import os
import tempfile
import pytest
from khp.discovery import DeviceRegistry


@pytest.fixture
def temp_registry(tmp_path):
    return DeviceRegistry(config_dir=tmp_path)


@pytest.fixture
def registered_heater(temp_registry, heater):
    temp_registry.register(heater, host="192.168.1.10", port=7400)
    return temp_registry


class TestDeviceRegistry:
    def test_register_creates_file(self, registered_heater, heater, tmp_path):
        path = tmp_path / f"{heater.device_id}.json"
        assert path.exists()

    def test_list_devices_returns_registered(self, registered_heater, heater):
        devices = registered_heater.list_devices()
        assert len(devices) == 1
        assert devices[0].device_id == heater.device_id
        assert devices[0].name == "Mock Heater"

    def test_list_filter_by_type(self, registered_heater):
        devices = registered_heater.list_devices(device_type="heater")
        assert len(devices) == 1
        devices = registered_heater.list_devices(device_type="pump")
        assert len(devices) == 0

    def test_deregister_removes(self, registered_heater, heater, tmp_path):
        registered_heater.deregister(heater.device_id)
        devices = registered_heater.list_devices()
        assert len(devices) == 0
        path = tmp_path / f"{heater.device_id}.json"
        assert not path.exists()

    def test_get_manifest_from_driver(self, registered_heater, heater):
        m = registered_heater.get_manifest(heater.device_id)
        assert m is not None
        assert m["name"] == "Mock Heater"

    def test_get_manifest_nonexistent_returns_none(self, temp_registry):
        assert temp_registry.get_manifest("ghost_device") is None

    def test_get_driver(self, registered_heater, heater):
        d = registered_heater.get_driver(heater.device_id)
        assert d is heater

    def test_get_driver_nonexistent(self, temp_registry):
        assert temp_registry.get_driver("ghost") is None
