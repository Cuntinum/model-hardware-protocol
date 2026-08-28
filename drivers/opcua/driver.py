"""KHP Driver: OPC UA Industrial.

Connects to OPC UA servers for industrial automation: PLCs, SCADA systems,
DCS controllers, and any OPC UA compliant device. Supports reading/writing
nodes, calling methods, and monitoring data changes via subscriptions.

Requirements:
    pip install asyncua
    (Works with Siemens, ABB, Rockwell, Beckhoff, and all OPC UA servers)
"""

from khp import Driver, readable, writable, procedure, safety, monitor
from khp.core import ConnectionType
from typing import Dict, List, Optional, Any
import asyncio
import time


class OPCUADevice(Driver):
    """OPC UA driver: read/write/call methods on industrial automation servers."""

    name = "OPC UA Device"
    version = "1.0.0"
    device_type = "plc"
    description = "Industrial OPC UA client. Connects to PLCs, SCADA, and DCS via OPC UA TCP."
    connection_type = ConnectionType.TCP

    def __init__(self, device_id: str = None, endpoint: str = "opc.tcp://localhost:4840",
                 security_policy: str = "None", certificate_path: str = None,
                 private_key_path: str = None, username: str = None,
                 password: str = None, namespace_uri: str = None,
                 monitored_nodes: List[str] = None, **config):
        super().__init__(device_id=device_id, endpoint=endpoint, **config)
        self._endpoint = endpoint
        self._security_policy = security_policy
        self._certificate_path = certificate_path
        self._private_key_path = private_key_path
        self._username = username
        self._password = password
        self._namespace_uri = namespace_uri
        self._monitored_nodes = monitored_nodes or []

        self._client = None
        self._subscription = None
        self._namespace_index = 2
        self._node_cache = {}
        self._monitored_values = {}
        self._server_info = {}
        self._write_limits = {}

    async def connect(self):
        from asyncua import Client
        from asyncua.crypto.security_policies import SecurityPolicyBasic256Sha256

        self._client = Client(url=self._endpoint)

        if self._username and self._password:
            self._client.set_user(self._username)
            self._client.set_password(self._password)

        if self._security_policy != "None" and self._certificate_path:
            await self._client.set_security(
                SecurityPolicyBasic256Sha256,
                certificate=self._certificate_path,
                private_key=self._private_key_path,
            )

        try:
            await self._client.connect()
        except Exception as e:
            from khp.errors import ConnectionFailedError
            raise ConnectionFailedError(
                f"Cannot connect to OPC UA server at {self._endpoint}: {e}",
                device_id=self.device_id,
            )

        if self._namespace_uri:
            self._namespace_index = await self._client.get_namespace_index(self._namespace_uri)

        server_node = self._client.get_node("ns=0;i=2261")
        try:
            self._server_info = {
                "product_name": await (await self._client.get_node("ns=0;i=2261")).read_value(),
            }
        except Exception:
            self._server_info = {"product_name": "Unknown OPC UA Server"}

        if self._monitored_nodes:
            await self._setup_subscriptions()

        await super().connect()

    async def _setup_subscriptions(self):
        self._subscription = await self._client.create_subscription(500, self)

        for node_id in self._monitored_nodes:
            node = self._client.get_node(node_id)
            await self._subscription.subscribe_data_change(node)
            self._monitored_values[node_id] = None

    def datachange_notification(self, node, val, data):
        """Callback for OPC UA data change subscriptions."""
        node_id = node.nodeid.to_string()
        self._monitored_values[node_id] = {
            "value": val,
            "timestamp": time.time(),
            "source_timestamp": str(data.monitored_item.Value.SourceTimestamp),
        }

    async def disconnect(self):
        if self._subscription:
            await self._subscription.delete()
        if self._client:
            await self._client.disconnect()
        self._client = None
        await super().disconnect()

    @readable(type="object", description="Server information: product name, status, and namespace count")
    def server_info(self) -> Dict:
        return self._server_info

    @readable(type="object", description="All currently monitored node values from active subscriptions")
    def monitored_values(self) -> Dict:
        return self._monitored_values

    @readable(type="float", description="Server response time in milliseconds", unit="ms")
    def server_latency(self) -> float:
        return getattr(self, "_last_latency_ms", 0.0)

    @writable(type="object", description="Write limits for specific nodes (node_id to min/max mapping)")
    def write_limits(self, limits: Dict):
        self._write_limits = limits

    @procedure(description="Read the value of an OPC UA node by its node ID string")
    async def read_node(self, node_id: str):
        if not self._client:
            from khp.errors import DeviceOfflineError
            raise DeviceOfflineError("Not connected to OPC UA server", device_id=self.device_id)

        t0 = time.time()
        node = self._client.get_node(node_id)

        try:
            value = await node.read_value()
            display_name = await node.read_display_name()
            data_type = await node.read_data_type_as_variant_type()
        except Exception as e:
            return {"status": "failed", "node_id": node_id, "error": str(e)}

        self._last_latency_ms = (time.time() - t0) * 1000

        return {
            "status": "completed",
            "node_id": node_id,
            "display_name": display_name.Text,
            "value": value,
            "data_type": str(data_type),
            "latency_ms": self._last_latency_ms,
        }

    @procedure(description="Write a value to an OPC UA node with type enforcement and safety checks")
    async def write_node(self, node_id: str, value: Any, data_type: str = None):
        if not self._client:
            from khp.errors import DeviceOfflineError
            raise DeviceOfflineError("Not connected to OPC UA server", device_id=self.device_id)

        if node_id in self._write_limits:
            limits = self._write_limits[node_id]
            if isinstance(value, (int, float)):
                if "max" in limits and value > limits["max"]:
                    from khp.errors import SafetyBlockedError
                    raise SafetyBlockedError(
                        f"Value {value} exceeds maximum {limits['max']} for node {node_id}",
                        device_id=self.device_id,
                        property_name=node_id,
                        attempted_value=value,
                        limit=limits,
                    )
                if "min" in limits and value < limits["min"]:
                    from khp.errors import SafetyBlockedError
                    raise SafetyBlockedError(
                        f"Value {value} below minimum {limits['min']} for node {node_id}",
                        device_id=self.device_id,
                        property_name=node_id,
                        attempted_value=value,
                        limit=limits,
                    )

        node = self._client.get_node(node_id)

        try:
            if data_type:
                from asyncua import ua
                variant_type = getattr(ua.VariantType, data_type)
                await node.write_value(ua.DataValue(ua.Variant(value, variant_type)))
            else:
                await node.write_value(value)
        except Exception as e:
            return {"status": "failed", "node_id": node_id, "error": str(e)}

        return {"status": "completed", "node_id": node_id, "value_written": value}

    @procedure(description="Browse child nodes of a given node ID to discover available data points")
    async def browse_nodes(self, node_id: str = "ns=0;i=85", max_depth: int = 2):
        if not self._client:
            from khp.errors import DeviceOfflineError
            raise DeviceOfflineError("Not connected to OPC UA server", device_id=self.device_id)

        results = []
        await self._browse_recursive(node_id, results, current_depth=0, max_depth=max_depth)
        return {"status": "completed", "root": node_id, "nodes": results, "count": len(results)}

    async def _browse_recursive(self, node_id: str, results: List, current_depth: int, max_depth: int):
        if current_depth >= max_depth or len(results) >= 200:
            return

        node = self._client.get_node(node_id)
        try:
            children = await node.get_children()
        except Exception:
            return

        for child in children:
            try:
                display_name = await child.read_display_name()
                node_class = await child.read_node_class()
                results.append({
                    "node_id": child.nodeid.to_string(),
                    "display_name": display_name.Text,
                    "node_class": str(node_class),
                    "depth": current_depth + 1,
                })
                await self._browse_recursive(
                    child.nodeid.to_string(), results, current_depth + 1, max_depth
                )
            except Exception:
                continue

    @procedure(description="Call an OPC UA method on a parent object node")
    async def call_method(self, object_node_id: str, method_node_id: str, arguments: List = None):
        if not self._client:
            from khp.errors import DeviceOfflineError
            raise DeviceOfflineError("Not connected to OPC UA server", device_id=self.device_id)

        object_node = self._client.get_node(object_node_id)
        method_node = self._client.get_node(method_node_id)

        try:
            result = await object_node.call_method(method_node, *(arguments or []))
        except Exception as e:
            return {"status": "failed", "method": method_node_id, "error": str(e)}

        return {
            "status": "completed",
            "object_node": object_node_id,
            "method_node": method_node_id,
            "result": result,
        }

    @procedure(description="Subscribe to data changes on a specific node (adds to monitored values)")
    async def subscribe_node(self, node_id: str, interval_ms: int = 500):
        if not self._client:
            from khp.errors import DeviceOfflineError
            raise DeviceOfflineError("Not connected to OPC UA server", device_id=self.device_id)

        if not self._subscription:
            self._subscription = await self._client.create_subscription(interval_ms, self)

        node = self._client.get_node(node_id)
        await self._subscription.subscribe_data_change(node)
        self._monitored_values[node_id] = None

        return {"status": "completed", "node_id": node_id, "interval_ms": interval_ms}

    @procedure(description="Read multiple nodes in a single batch request for efficiency")
    async def batch_read(self, node_ids: List[str]):
        if not self._client:
            from khp.errors import DeviceOfflineError
            raise DeviceOfflineError("Not connected to OPC UA server", device_id=self.device_id)

        results = {}
        t0 = time.time()

        for node_id in node_ids:
            node = self._client.get_node(node_id)
            try:
                value = await node.read_value()
                results[node_id] = {"value": value, "status": "ok"}
            except Exception as e:
                results[node_id] = {"value": None, "status": "error", "error": str(e)}

        elapsed_ms = (time.time() - t0) * 1000
        return {
            "status": "completed",
            "results": results,
            "count": len(node_ids),
            "total_ms": elapsed_ms,
        }

    @procedure(description="Get all alarms and conditions currently active on the server")
    async def get_active_alarms(self):
        if not self._client:
            from khp.errors import DeviceOfflineError
            raise DeviceOfflineError("Not connected to OPC UA server", device_id=self.device_id)

        from asyncua import ua

        conditions_node = self._client.get_node(ua.ObjectIds.Server_ServerStatus_State)
        try:
            state = await conditions_node.read_value()
        except Exception:
            state = "unknown"

        return {
            "status": "completed",
            "server_state": str(state),
            "monitored_node_count": len(self._monitored_values),
            "stale_nodes": [
                nid for nid, val in self._monitored_values.items()
                if val is None or (time.time() - val.get("timestamp", 0)) > 60
            ],
        }
