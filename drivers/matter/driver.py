"""KHP Driver: Matter (Thread/WiFi) Smart Home Device Integration.

Communicates with Matter compliant devices over Thread or WiFi networks.
Supports the full Matter device type taxonomy: lighting, switches, sensors,
thermostats, locks, blinds, fans, media players, and more.

Uses the chip tool controller interface for commissioning, cluster attribute
read/write, and command invocation. Supports group addressing, scenes,
binding, and OTA updates.

Requirements:
    pip install chip-tool (or system installed chip-tool binary)
"""
from __future__ import annotations

import json
import time
import subprocess
import threading
from typing import Any
from datetime import datetime, timezone

from khp import Driver, readable, writable, procedure, safety, monitor
from khp.core import ConnectionType


CLUSTER_IDS = {
    "on_off": 0x0006,
    "level_control": 0x0008,
    "color_control": 0x0300,
    "thermostat": 0x0201,
    "door_lock": 0x0101,
    "window_covering": 0x0102,
    "fan_control": 0x0202,
    "temperature_measurement": 0x0402,
    "humidity_measurement": 0x0405,
    "occupancy_sensing": 0x0406,
    "illuminance_measurement": 0x0400,
    "pressure_measurement": 0x0403,
}

DEVICE_TYPES = {
    0x0100: "On/Off Light",
    0x0101: "Dimmable Light",
    0x010C: "Color Temperature Light",
    0x010D: "Extended Color Light",
    0x0103: "On/Off Light Switch",
    0x0104: "Dimmer Switch",
    0x0302: "Temperature Sensor",
    0x0305: "Pressure Sensor",
    0x0307: "Humidity Sensor",
    0x0301: "Thermostat",
    0x000A: "Door Lock",
    0x0202: "Window Covering",
    0x002B: "Fan",
    0x0028: "Basic Video Player",
}


class MatterDevice(Driver):
    """Matter protocol driver for Thread/WiFi smart home devices."""

    name = "Matter Smart Home Controller"
    version = "1.0.0"
    device_type = "smart_home_controller"
    description = "Matter/Thread/WiFi device control including lighting, sensors, locks, and HVAC"
    connection_type = ConnectionType.WIRELESS

    def __init__(self, device_id: str | None = None, chip_tool_path: str = "chip-tool",
                 node_id: int = 1, fabric_id: int = 1, storage_path: str = "/tmp/chip_kvs",
                 **config):
        super().__init__(device_id=device_id, **config)
        self._chip_tool = chip_tool_path
        self._node_id = node_id
        self._fabric_id = fabric_id
        self._storage_path = storage_path
        self._commissioned_nodes: dict[int, dict] = {}
        self._last_values: dict[str, Any] = {}
        self._endpoint_map: dict[int, dict] = {}
        self._groups: dict[int, list[int]] = {}
        self._event_log: list[dict] = []
        self._connected = False

    def _run_chip_tool(self, *args, timeout: float = 30.0) -> dict:
        """Execute a chip tool command and parse the JSON output."""
        cmd = [self._chip_tool, "--storage-directory", self._storage_path] + list(args)
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            output = result.stdout.strip()

            if result.returncode != 0:
                return {"error": result.stderr.strip() or f"Exit code {result.returncode}"}

            try:
                return json.loads(output) if output.startswith("{") or output.startswith("[") else {"raw": output}
            except json.JSONDecodeError:
                return {"raw": output}

        except subprocess.TimeoutExpired:
            return {"error": "Command timed out"}
        except FileNotFoundError:
            return {"error": f"chip-tool not found at {self._chip_tool}"}

    async def connect(self):
        """Verify chip tool availability and fabric status."""
        result = self._run_chip_tool("--version")
        if "error" in result and "not found" in result["error"]:
            from khp.errors import DriverLoadError
            raise DriverLoadError(
                "chip-tool binary not found. Install Matter SDK or set chip_tool_path.",
                device_id=self.device_id,
            )
        self._connected = True
        await super().connect()

    async def disconnect(self):
        """Release Matter controller resources."""
        self._connected = False
        await super().disconnect()

    def _ensure_connected(self):
        if not self._connected:
            from khp.errors import DeviceOfflineError
            raise DeviceOfflineError("Matter controller not initialized", device_id=self.device_id)

    @readable(type="dict", description="Map of commissioned node IDs to their device info")
    def commissioned_nodes(self) -> dict:
        return self._commissioned_nodes

    @readable(type="int", description="Number of devices in the Matter fabric", unit="count")
    def node_count(self) -> int:
        return len(self._commissioned_nodes)

    @readable(type="dict", description="Last read attribute values cached per node/endpoint")
    def cached_values(self) -> dict:
        return self._last_values

    @readable(type="dict", description="Group memberships for multi device control")
    def group_memberships(self) -> dict:
        return self._groups

    @readable(type="int", description="Current controller node ID")
    def controller_node_id(self) -> int:
        return self._node_id

    @writable(type="int", description="Set the target node ID for operations")
    def target_node(self, value: int):
        if value < 1 or value > 0xFFFFFFFF:
            from khp.errors import SafetyBlockedError
            raise SafetyBlockedError(
                "Node ID must be between 1 and 4294967295",
                device_id=self.device_id,
                property_name="target_node",
                value=value,
                limit=0xFFFFFFFF,
            )
        self._node_id = value

    @procedure(description="Commission a new device into the Matter fabric using a setup code")
    def commission_device(self, setup_code: str = "", node_id: int = 0,
                          discriminator: int = 3840):
        """Commission over BLE or IP using the manual pairing code or QR payload."""
        self._ensure_connected()
        if not setup_code:
            return {"error": "setup_code required (manual code or QR payload)"}
        if node_id == 0:
            node_id = max(list(self._commissioned_nodes.keys()) or [0]) + 1

        if setup_code.startswith("MT:"):
            result = self._run_chip_tool(
                "pairing", "code", str(node_id), setup_code,
                timeout=120.0,
            )
        else:
            result = self._run_chip_tool(
                "pairing", "code", str(node_id), setup_code,
                "--discriminator", str(discriminator),
                timeout=120.0,
            )

        if "error" not in result:
            self._commissioned_nodes[node_id] = {
                "node_id": node_id,
                "commissioned_at": datetime.now(timezone.utc).isoformat(),
                "status": "active",
            }

        return {"node_id": node_id, "result": result}

    @procedure(description="Remove a device from the Matter fabric")
    def decommission_device(self, node_id: int = 0):
        """Unpair a device and remove it from the fabric."""
        if node_id == 0:
            return {"error": "node_id required"}

        result = self._run_chip_tool("pairing", "unpair", str(node_id))
        self._commissioned_nodes.pop(node_id, None)
        return {"node_id": node_id, "result": result}

    @procedure(description="Read a cluster attribute from a specific endpoint on a node")
    def read_attribute(self, node_id: int = 0, endpoint: int = 1,
                       cluster: str = "on_off", attribute: str = "on-off"):
        """Read a single attribute value from a Matter device."""
        self._ensure_connected()
        nid = node_id or self._node_id

        result = self._run_chip_tool(
            cluster.replace("_", ""), "read", attribute,
            str(nid), str(endpoint),
        )

        cache_key = f"{nid}/{endpoint}/{cluster}/{attribute}"
        self._last_values[cache_key] = {
            "value": result,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        return {"node_id": nid, "endpoint": endpoint, "cluster": cluster,
                "attribute": attribute, "result": result}

    @procedure(description="Write an attribute value to a cluster on a node endpoint")
    def write_attribute(self, node_id: int = 0, endpoint: int = 1,
                        cluster: str = "on_off", attribute: str = "on-time",
                        value: str = "0"):
        """Write a value to a Matter device attribute."""
        self._ensure_connected()
        nid = node_id or self._node_id

        result = self._run_chip_tool(
            cluster.replace("_", ""), "write", attribute, value,
            str(nid), str(endpoint),
        )

        self._event_log.append({
            "action": "write",
            "node_id": nid,
            "endpoint": endpoint,
            "cluster": cluster,
            "attribute": attribute,
            "value": value,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        return {"node_id": nid, "endpoint": endpoint, "result": result}

    @procedure(description="Send a command to a cluster (e.g., toggle, move-to-level)")
    def send_command(self, node_id: int = 0, endpoint: int = 1,
                     cluster: str = "on_off", command: str = "toggle",
                     args: str = ""):
        """Invoke a cluster command on the target device."""
        self._ensure_connected()
        nid = node_id or self._node_id

        cmd_args = [cluster.replace("_", ""), command, str(nid), str(endpoint)]
        if args:
            cmd_args.extend(args.split())

        result = self._run_chip_tool(*cmd_args)

        self._event_log.append({
            "action": "command",
            "node_id": nid,
            "endpoint": endpoint,
            "cluster": cluster,
            "command": command,
            "args": args,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        return {"node_id": nid, "endpoint": endpoint, "cluster": cluster,
                "command": command, "result": result}

    @procedure(description="Turn on a light or switch on a node")
    def turn_on(self, node_id: int = 0, endpoint: int = 1):
        """Send OnOff cluster On command."""
        return self.send_command(node_id=node_id, endpoint=endpoint,
                                cluster="on_off", command="on")

    @procedure(description="Turn off a light or switch on a node")
    def turn_off(self, node_id: int = 0, endpoint: int = 1):
        """Send OnOff cluster Off command."""
        return self.send_command(node_id=node_id, endpoint=endpoint,
                                cluster="on_off", command="off")

    @safety(min=0, max=254, reason="Level control valid range 0 to 254", hard=True)
    @procedure(description="Set brightness level on a dimmable device (0 to 254)")
    def set_level(self, node_id: int = 0, endpoint: int = 1, level: int = 128,
                  transition_time: int = 10):
        """Move to level with transition time (in tenths of seconds)."""
        return self.send_command(
            node_id=node_id, endpoint=endpoint,
            cluster="level_control", command="move-to-level",
            args=f"{level} {transition_time} 0 0",
        )

    @safety(min=0, max=65279, reason="Color temperature mireds valid range", hard=True)
    @procedure(description="Set color temperature in mireds on a color temp capable light")
    def set_color_temperature(self, node_id: int = 0, endpoint: int = 1,
                              mireds: int = 370, transition_time: int = 10):
        """Move to color temperature (mireds) with transition."""
        return self.send_command(
            node_id=node_id, endpoint=endpoint,
            cluster="color_control", command="move-to-color-temperature",
            args=f"{mireds} {transition_time} 0 0",
        )

    @procedure(description="Set thermostat setpoint (heating or cooling)")
    def set_thermostat(self, node_id: int = 0, endpoint: int = 1,
                       mode: str = "heat", setpoint_celsius: float = 21.0):
        """Write thermostat occupied setpoint."""
        setpoint_100 = int(setpoint_celsius * 100)
        attr = "occupied-heating-setpoint" if mode == "heat" else "occupied-cooling-setpoint"
        return self.write_attribute(
            node_id=node_id, endpoint=endpoint,
            cluster="thermostat", attribute=attr,
            value=str(setpoint_100),
        )

    @procedure(description="Lock or unlock a door lock device")
    def control_lock(self, node_id: int = 0, endpoint: int = 1, action: str = "lock"):
        """Send lock/unlock command to a DoorLock cluster."""
        if action not in ("lock", "unlock"):
            return {"error": "action must be 'lock' or 'unlock'"}
        cmd = "lock-door" if action == "lock" else "unlock-door"
        return self.send_command(
            node_id=node_id, endpoint=endpoint,
            cluster="door_lock", command=cmd,
            args="0",  # timed invoke timeout
        )

    @procedure(description="Set window covering position (0=open, 100=closed)")
    def set_window_covering(self, node_id: int = 0, endpoint: int = 1, position: int = 0):
        """Move window covering to a percentage position."""
        if position < 0 or position > 100:
            from khp.errors import SafetyBlockedError
            raise SafetyBlockedError(
                "Position must be 0 (open) to 100 (closed)",
                device_id=self.device_id,
                property_name="position",
                value=position,
                limit=100,
            )
        percent_100 = position * 100
        return self.send_command(
            node_id=node_id, endpoint=endpoint,
            cluster="window_covering", command="go-to-lift-percentage",
            args=str(percent_100),
        )

    @procedure(description="Discover endpoints and clusters on a commissioned node")
    def discover_node(self, node_id: int = 0):
        """Read the descriptor cluster to find endpoints and device types."""
        self._ensure_connected()
        nid = node_id or self._node_id

        result = self._run_chip_tool(
            "descriptor", "read", "parts-list", str(nid), "0",
        )

        server_list = self._run_chip_tool(
            "descriptor", "read", "server-list", str(nid), "0",
        )

        device_type_result = self._run_chip_tool(
            "descriptor", "read", "device-type-list", str(nid), "0",
        )

        self._endpoint_map[nid] = {
            "parts_list": result,
            "server_clusters": server_list,
            "device_types": device_type_result,
            "discovered_at": datetime.now(timezone.utc).isoformat(),
        }

        return {"node_id": nid, "discovery": self._endpoint_map[nid]}

    @procedure(description="Add a node to a multicast group for simultaneous control")
    def add_to_group(self, node_id: int = 0, endpoint: int = 1, group_id: int = 1,
                     group_name: str = ""):
        """Add endpoint to a Matter group for multi device control."""
        nid = node_id or self._node_id

        result = self._run_chip_tool(
            "groups", "add-group", str(group_id), f'"{group_name or f"Group {group_id}"}"',
            str(nid), str(endpoint),
        )

        if group_id not in self._groups:
            self._groups[group_id] = []
        if nid not in self._groups[group_id]:
            self._groups[group_id].append(nid)

        return {"node_id": nid, "group_id": group_id, "result": result}

    @procedure(description="Get recent event log entries for auditing device interactions")
    def get_event_log(self, last_n: int = 30):
        """Return recent command and write events."""
        entries = self._event_log[-last_n:]
        return {"total_events": len(self._event_log), "returned": len(entries), "events": entries}

    @monitor(interval_ms=15000, description="Monitor Matter fabric health and node reachability")
    def check_fabric_health(self) -> dict[str, Any]:
        alerts = []

        if not self._connected:
            alerts.append({"level": "critical", "message": "Matter controller not connected"})

        if not self._commissioned_nodes:
            alerts.append({"level": "info", "message": "No nodes commissioned in fabric"})

        return {
            "healthy": len(alerts) == 0,
            "fabric_id": self._fabric_id,
            "commissioned_nodes": len(self._commissioned_nodes),
            "groups": len(self._groups),
            "events_logged": len(self._event_log),
            "alerts": alerts,
        }
