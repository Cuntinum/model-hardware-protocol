"""KHP Device Discovery — find hardware on the network or local system."""

import json
import os
import socket
import struct
from pathlib import Path
from typing import Dict, List, Optional
from dataclasses import dataclass

from khp.core import Driver, DeviceStatus


KHP_CONFIG_DIR = Path.home() / ".khp"
KHP_DEVICES_DIR = KHP_CONFIG_DIR / "devices"
KHP_MDNS_SERVICE = "_khp._tcp.local."
KHP_DEFAULT_PORT = 7400


@dataclass
class DiscoveredDevice:
    device_id: str
    name: str
    device_type: str
    host: str
    port: int
    status: str
    driver: str = ""
    manifest_url: str = ""
    tags: Dict[str, str] = None

    def to_dict(self) -> dict:
        return {
            "device_id": self.device_id,
            "name": self.name,
            "type": self.device_type,
            "host": self.host,
            "port": self.port,
            "status": self.status,
            "driver": self.driver,
            "manifest_url": self.manifest_url,
            "tags": self.tags or {},
        }


class DeviceRegistry:
    """Local device registry — manages known devices."""

    def __init__(self, config_dir: Path = None):
        self.config_dir = config_dir or KHP_DEVICES_DIR
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self._drivers: Dict[str, Driver] = {}

    def register(self, driver: Driver, host: str = "localhost",
                 port: int = KHP_DEFAULT_PORT):
        """Register a driver in the local registry."""
        self._drivers[driver.device_id] = driver
        manifest = driver.get_manifest()
        manifest["_connection"] = {"host": host, "port": port}
        path = self.config_dir / f"{driver.device_id}.json"
        with open(path, "w") as f:
            json.dump(manifest, f, indent=2)

    def deregister(self, device_id: str):
        """Remove a device from the local registry."""
        self._drivers.pop(device_id, None)
        path = self.config_dir / f"{device_id}.json"
        if path.exists():
            path.unlink()

    def list_devices(self, device_type: str = None,
                     status: str = None) -> List[DiscoveredDevice]:
        """List all registered devices with optional filtering."""
        devices = []
        for path in self.config_dir.glob("*.json"):
            try:
                with open(path) as f:
                    manifest = json.load(f)
                conn = manifest.get("_connection", {})
                device = DiscoveredDevice(
                    device_id=manifest.get("device_id", path.stem),
                    name=manifest.get("name", "Unknown"),
                    device_type=manifest.get("type", "custom"),
                    host=conn.get("host", "localhost"),
                    port=conn.get("port", KHP_DEFAULT_PORT),
                    status="online" if manifest.get("device_id") in self._drivers else "registered",
                    driver=manifest.get("driver", ""),
                    tags=manifest.get("metadata", {}).get("tags", {}),
                )
                if device_type and device.device_type != device_type:
                    continue
                if status and device.status != status:
                    continue
                devices.append(device)
            except (json.JSONDecodeError, KeyError):
                continue
        return devices

    def get_manifest(self, device_id: str) -> Optional[dict]:
        """Get full manifest for a device."""
        path = self.config_dir / f"{device_id}.json"
        if path.exists():
            with open(path) as f:
                return json.load(f)
        driver = self._drivers.get(device_id)
        if driver:
            return driver.get_manifest()
        return None

    def get_driver(self, device_id: str) -> Optional[Driver]:
        """Get the live driver instance for a device."""
        return self._drivers.get(device_id)


_registry = DeviceRegistry()


def discover(device_type: str = None, capability: str = None,
             network: bool = False) -> List[dict]:
    """Discover available devices.

    Args:
        device_type: Filter by device type (e.g., "thermocycler")
        capability: Filter by capability (searches procedures/readable/writable)
        network: If True, also scan local network via mDNS
    """
    devices = _registry.list_devices(device_type=device_type)

    if capability:
        filtered = []
        for device in devices:
            manifest = _registry.get_manifest(device.device_id)
            if manifest is None:
                continue
            procs = manifest.get("procedures", {})
            readable = manifest.get("readable", {})
            writable = manifest.get("writable", {})
            all_caps = set(procs.keys()) | set(readable.keys()) | set(writable.keys())
            if capability in all_caps:
                filtered.append(device)
        devices = filtered

    if network:
        network_devices = _scan_mdns()
        seen_ids = {d.device_id for d in devices}
        for nd in network_devices:
            if nd.device_id not in seen_ids:
                devices.append(nd)

    return [d.to_dict() for d in devices]


def register(driver: Driver, host: str = "localhost", port: int = KHP_DEFAULT_PORT):
    """Register a driver with the discovery system."""
    _registry.register(driver, host, port)


def deregister(device_id: str):
    """Remove a device from the registry."""
    _registry.deregister(device_id)


def get_registry() -> DeviceRegistry:
    """Get the global device registry instance."""
    return _registry


def _scan_mdns(timeout_s: float = 2.0) -> List[DiscoveredDevice]:
    """Scan local network for KHP devices via mDNS. Returns found devices."""
    devices = []
    try:
        import zeroconf
        from zeroconf import ServiceBrowser, Zeroconf

        class Listener:
            def __init__(self):
                self.found = []

            def add_service(self, zc, type_, name):
                info = zc.get_service_info(type_, name)
                if info:
                    self.found.append(DiscoveredDevice(
                        device_id=name.split(".")[0],
                        name=info.properties.get(b"name", b"Unknown").decode(),
                        device_type=info.properties.get(b"type", b"custom").decode(),
                        host=socket.inet_ntoa(info.addresses[0]) if info.addresses else "unknown",
                        port=info.port,
                        status=info.properties.get(b"status", b"online").decode(),
                        manifest_url=info.properties.get(b"manifest", b"").decode(),
                    ))

            def remove_service(self, zc, type_, name):
                pass

            def update_service(self, zc, type_, name):
                pass

        zc = Zeroconf()
        listener = Listener()
        browser = ServiceBrowser(zc, KHP_MDNS_SERVICE, listener)

        import time
        time.sleep(timeout_s)
        zc.close()
        devices = listener.found

    except ImportError:
        pass
    return devices
