"""KHP Driver: LabVIEW Bridge.

Connects to LabVIEW VIs via the LabVIEW Web Service REST API or
network shared variables. Enables AI agents to control test and
measurement systems, data acquisition hardware, and custom instruments
built in LabVIEW.

Requirements:
    pip install httpx
    (Requires LabVIEW with Web Services enabled on the target machine)
"""

from khp import Driver, readable, writable, procedure, safety, monitor
from khp.core import ConnectionType
from typing import Dict, List, Optional, Any
import time


class LabVIEWDevice(Driver):
    """LabVIEW bridge driver: control VIs, read indicators, write controls via REST."""

    name = "LabVIEW Instrument"
    version = "1.0.0"
    device_type = "instrument"
    description = "Bridge to LabVIEW VIs via Web Service API. Controls DAQ, instruments, and test systems."
    connection_type = ConnectionType.REST

    def __init__(self, device_id: str = None, host: str = "localhost",
                 port: int = 8080, vi_name: str = "Main.vi",
                 use_ssl: bool = False, api_key: str = None,
                 timeout_s: float = 10.0, control_limits: Dict = None, **config):
        super().__init__(device_id=device_id, host=host, port=port, **config)
        self._host = host
        self._port = port
        self._vi_name = vi_name
        self._use_ssl = use_ssl
        self._api_key = api_key
        self._timeout_s = timeout_s
        self._control_limits = control_limits or {}

        self._client = None
        self._base_url = ""
        self._indicator_cache = {}
        self._vi_status = "idle"
        self._acquisition_running = False
        self._last_response_ms = 0.0

    async def connect(self):
        import httpx

        protocol = "https" if self._use_ssl else "http"
        self._base_url = f"{protocol}://{self._host}:{self._port}"

        headers = {}
        if self._api_key:
            headers["X-API-Key"] = self._api_key

        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            headers=headers,
            timeout=self._timeout_s,
        )

        try:
            response = await self._client.get("/api/v1/status")
            if response.status_code == 200:
                data = response.json()
                self._vi_status = data.get("state", "idle")
            else:
                response = await self._client.get("/")
                if response.status_code != 200:
                    raise Exception(f"HTTP {response.status_code}")
        except Exception as e:
            from khp.errors import ConnectionFailedError
            raise ConnectionFailedError(
                f"Cannot connect to LabVIEW at {self._base_url}: {e}",
                device_id=self.device_id,
            )

        await super().connect()

    async def disconnect(self):
        if self._client:
            await self._client.aclose()
        self._client = None
        await super().disconnect()

    @readable(type="string", description="Current VI execution state: idle, running, paused, or error")
    def vi_status(self) -> str:
        return self._vi_status

    @readable(type="bool", description="Whether a data acquisition session is currently active")
    def acquisition_running(self) -> bool:
        return self._acquisition_running

    @readable(type="float", description="Last HTTP response time to LabVIEW server", unit="ms")
    def response_latency(self) -> float:
        return self._last_response_ms

    @readable(type="object", description="Cached values of all front panel indicators read from the VI")
    def indicators(self) -> Dict:
        return self._indicator_cache

    @monitor(interval_s=2.0, alert_above=5000.0)
    def latency_monitor(self) -> float:
        return self._last_response_ms

    @safety(max=1000000.0, min=1.0, reason="Sample rate must be within DAQ hardware capabilities", hard=True)
    @writable(type="float", description="Data acquisition sample rate", unit="Hz")
    def sample_rate(self, value: float):
        self._set_control("sample_rate", value)

    @safety(max=10.0, min=-10.0, reason="Analog output voltage limited to DAQ card range", hard=True)
    @writable(type="float", description="Analog output voltage on configured channel", unit="volts")
    def analog_output(self, value: float):
        self._set_control("analog_output", value)

    @writable(type="bool", description="Digital output state (True=high, False=low)")
    def digital_output(self, value: bool):
        self._set_control("digital_output", value)

    def _set_control(self, control_name: str, value: Any):
        if control_name in self._control_limits:
            limits = self._control_limits[control_name]
            if isinstance(value, (int, float)):
                if "max" in limits and value > limits["max"]:
                    from khp.errors import SafetyBlockedError
                    raise SafetyBlockedError(
                        f"Value {value} exceeds limit for control '{control_name}'",
                        device_id=self.device_id,
                        property_name=control_name,
                        attempted_value=value,
                        limit=limits,
                    )

    @procedure(description="Read a specific indicator value from the LabVIEW VI front panel")
    async def read_indicator(self, indicator_name: str):
        if not self._client:
            from khp.errors import DeviceOfflineError
            raise DeviceOfflineError("Not connected to LabVIEW", device_id=self.device_id)

        t0 = time.time()
        try:
            response = await self._client.get(
                f"/api/v1/vi/{self._vi_name}/indicators/{indicator_name}"
            )
            self._last_response_ms = (time.time() - t0) * 1000

            if response.status_code == 200:
                data = response.json()
                self._indicator_cache[indicator_name] = data.get("value")
                return {
                    "status": "completed",
                    "indicator": indicator_name,
                    "value": data.get("value"),
                    "data_type": data.get("type", "unknown"),
                    "latency_ms": self._last_response_ms,
                }
            else:
                return {"status": "failed", "indicator": indicator_name, "error": f"HTTP {response.status_code}"}
        except Exception as e:
            return {"status": "failed", "indicator": indicator_name, "error": str(e)}

    @procedure(description="Write a value to a LabVIEW VI front panel control")
    async def write_control(self, control_name: str, value: Any):
        if not self._client:
            from khp.errors import DeviceOfflineError
            raise DeviceOfflineError("Not connected to LabVIEW", device_id=self.device_id)

        if control_name in self._control_limits:
            limits = self._control_limits[control_name]
            if isinstance(value, (int, float)):
                if "max" in limits and value > limits["max"]:
                    from khp.errors import SafetyBlockedError
                    raise SafetyBlockedError(
                        f"Value {value} exceeds limit for control '{control_name}'",
                        device_id=self.device_id,
                        property_name=control_name,
                        attempted_value=value,
                        limit=limits,
                    )
                if "min" in limits and value < limits["min"]:
                    from khp.errors import SafetyBlockedError
                    raise SafetyBlockedError(
                        f"Value {value} below minimum for control '{control_name}'",
                        device_id=self.device_id,
                        property_name=control_name,
                        attempted_value=value,
                        limit=limits,
                    )

        t0 = time.time()
        try:
            response = await self._client.put(
                f"/api/v1/vi/{self._vi_name}/controls/{control_name}",
                json={"value": value},
            )
            self._last_response_ms = (time.time() - t0) * 1000

            if response.status_code == 200:
                return {"status": "completed", "control": control_name, "value_set": value}
            else:
                return {"status": "failed", "control": control_name, "error": f"HTTP {response.status_code}"}
        except Exception as e:
            return {"status": "failed", "control": control_name, "error": str(e)}

    @procedure(description="Read all indicators from the VI front panel in one batch request")
    async def read_all_indicators(self):
        if not self._client:
            from khp.errors import DeviceOfflineError
            raise DeviceOfflineError("Not connected to LabVIEW", device_id=self.device_id)

        t0 = time.time()
        try:
            response = await self._client.get(f"/api/v1/vi/{self._vi_name}/indicators")
            self._last_response_ms = (time.time() - t0) * 1000

            if response.status_code == 200:
                data = response.json()
                indicators = data.get("indicators", {})
                self._indicator_cache.update(indicators)
                return {
                    "status": "completed",
                    "indicators": indicators,
                    "count": len(indicators),
                    "latency_ms": self._last_response_ms,
                }
            else:
                return {"status": "failed", "error": f"HTTP {response.status_code}"}
        except Exception as e:
            return {"status": "failed", "error": str(e)}

    @procedure(description="Start the LabVIEW VI execution (runs the block diagram)")
    async def run_vi(self):
        if not self._client:
            from khp.errors import DeviceOfflineError
            raise DeviceOfflineError("Not connected to LabVIEW", device_id=self.device_id)

        try:
            response = await self._client.post(f"/api/v1/vi/{self._vi_name}/run")
            if response.status_code == 200:
                self._vi_status = "running"
                return {"status": "completed", "action": "vi_started", "vi": self._vi_name}
            else:
                return {"status": "failed", "error": f"HTTP {response.status_code}"}
        except Exception as e:
            return {"status": "failed", "error": str(e)}

    @procedure(description="Stop the LabVIEW VI execution gracefully")
    async def stop_vi(self):
        if not self._client:
            from khp.errors import DeviceOfflineError
            raise DeviceOfflineError("Not connected to LabVIEW", device_id=self.device_id)

        try:
            response = await self._client.post(f"/api/v1/vi/{self._vi_name}/stop")
            if response.status_code == 200:
                self._vi_status = "idle"
                self._acquisition_running = False
                return {"status": "completed", "action": "vi_stopped"}
            else:
                return {"status": "failed", "error": f"HTTP {response.status_code}"}
        except Exception as e:
            return {"status": "failed", "error": str(e)}

    @procedure(description="Start a data acquisition session with configured channels and rate")
    async def start_acquisition(self, channels: List[str] = None, sample_rate: float = 1000.0,
                                duration_s: float = None):
        if not self._client:
            from khp.errors import DeviceOfflineError
            raise DeviceOfflineError("Not connected to LabVIEW", device_id=self.device_id)

        if sample_rate > 1000000 or sample_rate < 1:
            from khp.errors import SafetyBlockedError
            raise SafetyBlockedError(
                f"Sample rate {sample_rate} Hz outside valid range (1 to 1000000)",
                device_id=self.device_id,
                property_name="sample_rate",
                attempted_value=sample_rate,
                limit={"min": 1, "max": 1000000},
            )

        payload = {
            "channels": channels or ["ai0"],
            "sample_rate": sample_rate,
        }
        if duration_s:
            payload["duration_s"] = duration_s

        try:
            response = await self._client.post("/api/v1/acquisition/start", json=payload)
            if response.status_code == 200:
                self._acquisition_running = True
                return {
                    "status": "completed",
                    "action": "acquisition_started",
                    "channels": payload["channels"],
                    "sample_rate": sample_rate,
                }
            else:
                return {"status": "failed", "error": f"HTTP {response.status_code}"}
        except Exception as e:
            return {"status": "failed", "error": str(e)}

    @procedure(description="Stop the current data acquisition session and retrieve buffered data")
    async def stop_acquisition(self):
        if not self._client:
            from khp.errors import DeviceOfflineError
            raise DeviceOfflineError("Not connected to LabVIEW", device_id=self.device_id)

        try:
            response = await self._client.post("/api/v1/acquisition/stop")
            if response.status_code == 200:
                self._acquisition_running = False
                data = response.json()
                return {
                    "status": "completed",
                    "action": "acquisition_stopped",
                    "samples_collected": data.get("samples_collected", 0),
                    "file_path": data.get("file_path"),
                }
            else:
                return {"status": "failed", "error": f"HTTP {response.status_code}"}
        except Exception as e:
            return {"status": "failed", "error": str(e)}

    @procedure(description="Trigger a single measurement and return the result immediately")
    async def trigger_measurement(self, measurement_type: str = "voltage", channel: str = "ai0"):
        if not self._client:
            from khp.errors import DeviceOfflineError
            raise DeviceOfflineError("Not connected to LabVIEW", device_id=self.device_id)

        t0 = time.time()
        try:
            response = await self._client.post(
                "/api/v1/measurement/trigger",
                json={"type": measurement_type, "channel": channel},
            )
            elapsed_ms = (time.time() - t0) * 1000

            if response.status_code == 200:
                data = response.json()
                return {
                    "status": "completed",
                    "measurement_type": measurement_type,
                    "channel": channel,
                    "value": data.get("value"),
                    "unit": data.get("unit", "V"),
                    "timestamp": data.get("timestamp"),
                    "latency_ms": elapsed_ms,
                }
            else:
                return {"status": "failed", "error": f"HTTP {response.status_code}"}
        except Exception as e:
            return {"status": "failed", "error": str(e)}

    @procedure(description="List all available VIs on the connected LabVIEW server")
    async def list_vis(self):
        if not self._client:
            from khp.errors import DeviceOfflineError
            raise DeviceOfflineError("Not connected to LabVIEW", device_id=self.device_id)

        try:
            response = await self._client.get("/api/v1/vis")
            if response.status_code == 200:
                data = response.json()
                return {
                    "status": "completed",
                    "vis": data.get("vis", []),
                    "count": len(data.get("vis", [])),
                }
            else:
                return {"status": "failed", "error": f"HTTP {response.status_code}"}
        except Exception as e:
            return {"status": "failed", "error": str(e)}

    @procedure(description="Switch the active VI to a different one on the same LabVIEW server")
    async def switch_vi(self, vi_name: str):
        self._vi_name = vi_name
        self._indicator_cache = {}
        self._vi_status = "idle"
        self._acquisition_running = False
        return {"status": "completed", "active_vi": vi_name}
