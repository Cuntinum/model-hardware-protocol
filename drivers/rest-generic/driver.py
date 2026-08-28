"""KHP Driver — Generic REST API devices.

Supports any device with an HTTP/HTTPS API endpoint.
Covers: smart lab equipment, IoT hubs, cloud-connected instruments,
network-attached sensors, web-controlled power supplies, etc.

Requirements:
    pip install httpx (async) or requests (sync)
"""

from khp import Driver, readable, writable, procedure, safety
from khp.core import ConnectionType
from typing import Any, Dict, Optional
import json


class RESTDevice(Driver):
    """Generic REST API device driver — wraps any HTTP-based device."""

    name = "Generic REST Device"
    version = "1.0.0"
    device_type = "custom"
    description = "HTTP/REST API connected device with configurable endpoints"
    connection_type = ConnectionType.REST

    def __init__(self, device_id: str = None, base_url: str = "http://localhost:8080",
                 api_key: str = None, headers: dict = None, timeout: float = 10.0,
                 read_endpoints: dict = None, write_endpoints: dict = None,
                 procedure_endpoints: dict = None, **config):
        super().__init__(device_id=device_id, endpoint=base_url, **config)
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._headers = headers or {}
        self._timeout = timeout
        self._read_endpoints = read_endpoints or {}
        self._write_endpoints = write_endpoints or {}
        self._procedure_endpoints = procedure_endpoints or {}
        self._session = None

        if api_key:
            self._headers["Authorization"] = f"Bearer {api_key}"
        self._headers.setdefault("Content-Type", "application/json")

    async def connect(self):
        try:
            import httpx
            self._session = httpx.AsyncClient(
                base_url=self._base_url,
                headers=self._headers,
                timeout=self._timeout,
            )
            response = await self._session.get("/")
            if response.status_code < 500:
                await super().connect()
        except ImportError:
            import requests
            self._session = requests.Session()
            self._session.headers.update(self._headers)
            await super().connect()

    async def disconnect(self):
        if self._session:
            if hasattr(self._session, "aclose"):
                await self._session.aclose()
        self._session = None
        await super().disconnect()

    def _get_sync(self, path: str, params: dict = None) -> Any:
        """Synchronous GET request."""
        import requests
        url = f"{self._base_url}{path}"
        resp = requests.get(url, headers=self._headers, params=params, timeout=self._timeout)
        resp.raise_for_status()
        return resp.json()

    def _post_sync(self, path: str, data: dict = None) -> Any:
        """Synchronous POST request."""
        import requests
        url = f"{self._base_url}{path}"
        resp = requests.post(url, headers=self._headers, json=data, timeout=self._timeout)
        resp.raise_for_status()
        return resp.json()

    def _put_sync(self, path: str, data: dict = None) -> Any:
        """Synchronous PUT request."""
        import requests
        url = f"{self._base_url}{path}"
        resp = requests.put(url, headers=self._headers, json=data, timeout=self._timeout)
        resp.raise_for_status()
        return resp.json()

    @readable(type="object", description="Device status via GET /status")
    def device_status(self) -> dict:
        try:
            return self._get_sync("/status")
        except Exception as e:
            return {"error": str(e)}

    @readable(type="object", description="Device info via GET /info")
    def device_info(self) -> dict:
        try:
            return self._get_sync("/info")
        except Exception as e:
            return {"error": str(e)}

    @writable(type="object", description="Send key-value config via PUT /config")
    def config_value(self, value: dict):
        try:
            self._put_sync("/config", value)
        except Exception:
            pass

    @procedure(description="Send GET request to a custom endpoint",
               estimated_duration_s=2.0)
    def get(self, path: str, params: dict = None) -> Any:
        """Make a GET request to any path on the device."""
        return self._get_sync(path, params)

    @procedure(description="Send POST request to a custom endpoint",
               estimated_duration_s=2.0)
    def post(self, path: str, data: dict = None) -> Any:
        """Make a POST request to any path on the device."""
        return self._post_sync(path, data)

    @procedure(description="Send PUT request to a custom endpoint",
               estimated_duration_s=2.0)
    def put(self, path: str, data: dict = None) -> Any:
        """Make a PUT request to any path on the device."""
        return self._put_sync(path, data)

    @procedure(description="Poll an endpoint repeatedly until condition met",
               estimated_duration_s=60.0)
    def poll_until(self, path: str, field: str, expected_value: Any,
                   interval_s: float = 1.0, max_attempts: int = 60) -> dict:
        """Poll GET endpoint until response[field] == expected_value."""
        import time
        for i in range(max_attempts):
            response = self._get_sync(path)
            if isinstance(response, dict) and response.get(field) == expected_value:
                return {"success": True, "attempts": i + 1, "response": response}
            time.sleep(interval_s)
        return {"success": False, "attempts": max_attempts, "last_response": response}


class SmartPlug(RESTDevice):
    """Smart plug/relay via REST API (Tasmota, Shelly, TP-Link Kasa)."""

    name = "Smart Plug"
    version = "1.0.0"
    device_type = "gpio"
    description = "WiFi smart plug/relay with power monitoring"

    def __init__(self, device_id: str = None, base_url: str = "http://192.168.1.100",
                 protocol: str = "tasmota", **config):
        super().__init__(device_id=device_id, base_url=base_url, **config)
        self._protocol = protocol
        self._state = False
        self._power_w = 0.0

    @readable(type="bool", description="Current relay state (on/off)")
    def relay_state(self) -> bool:
        try:
            if self._protocol == "tasmota":
                resp = self._get_sync("/cm?cmnd=Status%200")
                self._state = resp.get("Status", {}).get("Power", 0) == 1
            elif self._protocol == "shelly":
                resp = self._get_sync("/relay/0")
                self._state = resp.get("ison", False)
            return self._state
        except Exception:
            return self._state

    @readable(type="float", description="Current power draw", unit="watts")
    def power(self) -> float:
        try:
            if self._protocol == "tasmota":
                resp = self._get_sync("/cm?cmnd=Status%208")
                self._power_w = resp.get("StatusSNS", {}).get("ENERGY", {}).get("Power", 0.0)
            elif self._protocol == "shelly":
                resp = self._get_sync("/meter/0")
                self._power_w = resp.get("power", 0.0)
            return self._power_w
        except Exception:
            return self._power_w

    @safety(max=3600, reason="Maximum safe power draw for connected equipment")
    @writable(type="bool", description="Turn relay on (True) or off (False)")
    def relay(self, value: bool):
        cmd = "on" if value else "off"
        try:
            if self._protocol == "tasmota":
                self._get_sync(f"/cm?cmnd=Power%20{cmd}")
            elif self._protocol == "shelly":
                self._get_sync(f"/relay/0?turn={cmd}")
            self._state = value
        except Exception:
            pass

    @procedure(description="Toggle relay state", estimated_duration_s=0.5)
    def toggle(self):
        """Toggle the relay."""
        new_state = not self._state
        self.write("relay", new_state)
        return {"state": new_state}

    @procedure(description="Turn on for duration then off",
               estimated_duration_s=300.0)
    def timed_on(self, duration_s: int = 60):
        """Turn relay on for a specified duration, then off."""
        import time
        self.write("relay", True)
        time.sleep(duration_s)
        self.write("relay", False)
        return {"duration_s": duration_s, "state": "off"}
