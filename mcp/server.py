"""KHP MCP Server — Exposes all KHP hardware devices as MCP tools.

Makes any KHP connected device controllable by AI agents through the Model
Context Protocol. Each device's @readable, @writable, and @procedure decorated
methods become individual MCP tools that Claude, GPT, or any MCP compatible
agent can invoke.

Architecture:
    MCP Host (Claude Desktop, Claude Code, etc.)
        ↕ JSON RPC over stdio or SSE
    KHP MCP Server (this file)
        ↕ KHP Driver API
    Physical Hardware (robots, sensors, PLCs, lights, medical, energy, etc.)

Usage:
    # stdio mode (for Claude Desktop, Claude Code, Cursor, etc.)
    python -m mcp.server

    # Or configure in Claude Desktop's config:
    {
      "mcpServers": {
        "khp": {
          "command": "python",
          "args": ["-m", "mcp.server"],
          "cwd": "/path/to/model-hardware-protocol"
        }
      }
    }

    # Or run with specific drivers:
    python -m mcp.server --drivers ethercat,universal_robots,knx

Tools exposed:
    khp_discover            → Scan for available hardware devices
    khp_read                → Read a sensor/property value from a device
    khp_write               → Set a property on a device (safety enforced)
    khp_execute             → Run a procedure (move robot, start pump, etc.)
    khp_manifest            → Get full device capabilities and safety limits
    khp_batch_read          → Read multiple properties in one call
    khp_emergency_stop      → Immediate halt of one or all devices
    khp_health              → Get health/status of all connected devices

Resources exposed:
    khp://devices            → List of all connected devices
    khp://device/{id}        → Full state of a specific device
    khp://safety/{id}        → Safety limits and current violations

Requirements:
    pip install mcp khp
"""
from __future__ import annotations

import sys
import json
import asyncio
import logging
import importlib
import traceback
from typing import Any
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("khp.mcp")


DRIVER_MODULES = {
    "ethercat": "drivers.ethercat",
    "profinet": "drivers.profinet",
    "iolink": "drivers.iolink",
    "universal_robots": "drivers.universal_robots",
    "mavlink": "drivers.mavlink",
    "gcode": "drivers.gcode",
    "hl7fhir": "drivers.hl7fhir",
    "dicom": "drivers.dicom",
    "matter": "drivers.matter",
    "knx": "drivers.knx",
    "ocpp": "drivers.ocpp",
    "dmx": "drivers.dmx",
    "iec61850": "drivers.iec61850",
    "sunspec": "drivers.sunspec",
    "dnp3": "drivers.dnp3",
    "sila2": "drivers.sila2",
    "midi": "drivers.midi",
    "ndi": "drivers.ndi",
}


class DeviceRegistry:
    """In memory registry of active device driver instances."""

    def __init__(self):
        self._devices: dict[str, Any] = {}
        self._manifests: dict[str, dict] = {}

    def register(self, device_id: str, driver: Any):
        self._devices[device_id] = driver
        self._manifests[device_id] = self._build_manifest(device_id, driver)

    def unregister(self, device_id: str):
        self._devices.pop(device_id, None)
        self._manifests.pop(device_id, None)

    def get(self, device_id: str) -> Any | None:
        return self._devices.get(device_id)

    def list_all(self) -> list[dict]:
        results = []
        for dev_id, driver in self._devices.items():
            results.append({
                "device_id": dev_id,
                "name": getattr(driver, "name", dev_id),
                "type": getattr(driver, "device_type", "unknown"),
                "version": getattr(driver, "version", "0.0.0"),
                "description": getattr(driver, "description", ""),
                "connection_type": str(getattr(driver, "connection_type", "unknown")),
            })
        return results

    def get_manifest(self, device_id: str) -> dict | None:
        return self._manifests.get(device_id)

    def _build_manifest(self, device_id: str, driver: Any) -> dict:
        readable = []
        writable = []
        procedures = []
        safety_limits = []

        for attr_name in dir(driver):
            if attr_name.startswith("_"):
                continue
            attr = getattr(type(driver), attr_name, None)
            if attr is None:
                continue

            if hasattr(attr, "_khp_readable"):
                meta = attr._khp_readable
                readable.append({
                    "name": attr_name,
                    "type": meta.get("type", "any"),
                    "description": meta.get("description", ""),
                    "unit": meta.get("unit", None),
                })
            elif hasattr(attr, "_khp_writable"):
                meta = attr._khp_writable
                entry = {
                    "name": attr_name,
                    "type": meta.get("type", "any"),
                    "description": meta.get("description", ""),
                    "unit": meta.get("unit", None),
                }
                writable.append(entry)
            elif hasattr(attr, "_khp_procedure"):
                meta = attr._khp_procedure
                procedures.append({
                    "name": attr_name,
                    "description": meta.get("description", ""),
                    "params": self._extract_params(attr),
                })

            if hasattr(attr, "_khp_safety"):
                safety_meta = attr._khp_safety
                safety_limits.append({
                    "property": attr_name,
                    "min": safety_meta.get("min"),
                    "max": safety_meta.get("max"),
                    "reason": safety_meta.get("reason", ""),
                    "hard": safety_meta.get("hard", False),
                })

        return {
            "device_id": device_id,
            "name": getattr(driver, "name", device_id),
            "type": getattr(driver, "device_type", "unknown"),
            "version": getattr(driver, "version", "0.0.0"),
            "description": getattr(driver, "description", ""),
            "readable": readable,
            "writable": writable,
            "procedures": procedures,
            "safety_limits": safety_limits,
        }

    def _extract_params(self, method) -> list[dict]:
        import inspect
        params = []
        sig = inspect.signature(method)
        for name, param in sig.parameters.items():
            if name == "self":
                continue
            entry = {"name": name}
            if param.annotation != inspect.Parameter.empty:
                entry["type"] = param.annotation.__name__ if hasattr(param.annotation, "__name__") else str(param.annotation)
            if param.default != inspect.Parameter.empty:
                entry["default"] = param.default
            params.append(entry)
        return params


class KHPMCPServer:
    """Full MCP protocol server exposing KHP devices as tools and resources."""

    def __init__(self):
        self.registry = DeviceRegistry()
        self._request_id = 0

    def register_driver(self, device_id: str, driver: Any):
        self.registry.register(device_id, driver)

    async def handle_tool_call(self, tool_name: str, arguments: dict) -> dict:
        """Route an MCP tool call to the appropriate KHP handler."""
        handlers = {
            "khp_discover": self._handle_discover,
            "khp_read": self._handle_read,
            "khp_write": self._handle_write,
            "khp_execute": self._handle_execute,
            "khp_manifest": self._handle_manifest,
            "khp_batch_read": self._handle_batch_read,
            "khp_emergency_stop": self._handle_emergency_stop,
            "khp_health": self._handle_health,
        }
        handler = handlers.get(tool_name)
        if not handler:
            return {"error": f"Unknown tool: {tool_name}", "available_tools": list(handlers.keys())}
        try:
            return await handler(arguments)
        except Exception as e:
            return {
                "error": str(e),
                "type": type(e).__name__,
                "tool": tool_name,
            }

    async def _handle_discover(self, args: dict) -> dict:
        devices = self.registry.list_all()
        device_type = args.get("device_type")
        if device_type:
            devices = [d for d in devices if d["type"] == device_type]
        return {"devices": devices, "count": len(devices)}

    async def _handle_read(self, args: dict) -> dict:
        device_id = args.get("device_id", "")
        prop = args.get("property", "")
        if not device_id or not prop:
            return {"error": "Both device_id and property are required"}

        driver = self.registry.get(device_id)
        if not driver:
            return {"error": f"Device '{device_id}' not found", "available": [d["device_id"] for d in self.registry.list_all()]}

        method = getattr(driver, prop, None)
        if method is None or not hasattr(method, "_khp_readable"):
            return {"error": f"Property '{prop}' not readable on device '{device_id}'"}

        try:
            value = method()
            meta = method._khp_readable
            return {
                "device_id": device_id,
                "property": prop,
                "value": value,
                "type": meta.get("type", "any"),
                "unit": meta.get("unit"),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        except Exception as e:
            return {"error": f"Read failed: {e}", "device_id": device_id, "property": prop}

    async def _handle_write(self, args: dict) -> dict:
        device_id = args.get("device_id", "")
        prop = args.get("property", "")
        value = args.get("value")
        if not device_id or not prop:
            return {"error": "device_id, property, and value are required"}

        driver = self.registry.get(device_id)
        if not driver:
            return {"error": f"Device '{device_id}' not found"}

        method = getattr(driver, prop, None)
        if method is None or not hasattr(method, "_khp_writable"):
            return {"error": f"Property '{prop}' not writable on device '{device_id}'"}

        try:
            method(value)
            return {
                "device_id": device_id,
                "property": prop,
                "value": value,
                "status": "written",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        except Exception as e:
            error_type = type(e).__name__
            if "SafetyBlocked" in error_type:
                return {
                    "error": str(e),
                    "type": "safety_blocked",
                    "device_id": device_id,
                    "property": prop,
                    "attempted_value": value,
                }
            return {"error": f"Write failed: {e}", "device_id": device_id}

    async def _handle_execute(self, args: dict) -> dict:
        device_id = args.get("device_id", "")
        procedure = args.get("procedure", "")
        params = args.get("params", {})
        if not device_id or not procedure:
            return {"error": "device_id and procedure are required"}

        driver = self.registry.get(device_id)
        if not driver:
            return {"error": f"Device '{device_id}' not found"}

        method = getattr(driver, procedure, None)
        if method is None or not hasattr(method, "_khp_procedure"):
            available = [
                name for name in dir(driver)
                if not name.startswith("_") and hasattr(getattr(type(driver), name, None), "_khp_procedure")
            ]
            return {"error": f"Procedure '{procedure}' not found on '{device_id}'", "available_procedures": available}

        try:
            if asyncio.iscoroutinefunction(method):
                result = await method(**params)
            else:
                result = method(**params)
            return {
                "device_id": device_id,
                "procedure": procedure,
                "result": result,
                "status": "completed",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        except Exception as e:
            return {
                "error": f"Procedure failed: {e}",
                "type": type(e).__name__,
                "device_id": device_id,
                "procedure": procedure,
            }

    async def _handle_manifest(self, args: dict) -> dict:
        device_id = args.get("device_id", "")
        if not device_id:
            return {"error": "device_id is required"}
        manifest = self.registry.get_manifest(device_id)
        if not manifest:
            return {"error": f"Device '{device_id}' not found"}
        return manifest

    async def _handle_batch_read(self, args: dict) -> dict:
        reads = args.get("reads", [])
        if not reads:
            return {"error": "reads array required: [{device_id, property}, ...]"}

        results = []
        for read_spec in reads:
            result = await self._handle_read(read_spec)
            results.append(result)
        return {"results": results, "count": len(results)}

    async def _handle_emergency_stop(self, args: dict) -> dict:
        device_id = args.get("device_id")
        stopped = []
        errors = []

        if device_id:
            driver = self.registry.get(device_id)
            if driver and hasattr(driver, "emergency_stop"):
                try:
                    if asyncio.iscoroutinefunction(driver.emergency_stop):
                        await driver.emergency_stop()
                    else:
                        driver.emergency_stop()
                    stopped.append(device_id)
                except Exception as e:
                    errors.append({"device_id": device_id, "error": str(e)})
        else:
            for dev_info in self.registry.list_all():
                did = dev_info["device_id"]
                driver = self.registry.get(did)
                if driver and hasattr(driver, "emergency_stop"):
                    try:
                        if asyncio.iscoroutinefunction(driver.emergency_stop):
                            await driver.emergency_stop()
                        else:
                            driver.emergency_stop()
                        stopped.append(did)
                    except Exception as e:
                        errors.append({"device_id": did, "error": str(e)})

        return {
            "emergency_stop": True,
            "devices_stopped": stopped,
            "errors": errors,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    async def _handle_health(self, args: dict) -> dict:
        health_reports = []
        for dev_info in self.registry.list_all():
            did = dev_info["device_id"]
            driver = self.registry.get(did)
            report = {"device_id": did, "name": dev_info["name"], "type": dev_info["type"]}

            monitor_method = None
            for attr_name in dir(driver):
                attr = getattr(type(driver), attr_name, None)
                if attr and hasattr(attr, "_khp_monitor"):
                    monitor_method = getattr(driver, attr_name)
                    break

            if monitor_method:
                try:
                    report["health"] = monitor_method()
                except Exception as e:
                    report["health"] = {"error": str(e)}
            else:
                report["health"] = {"status": "no_monitor"}

            health_reports.append(report)

        return {"devices": health_reports, "count": len(health_reports)}

    def get_tool_definitions(self) -> list[dict]:
        """Return MCP compliant tool definitions."""
        return [
            {
                "name": "khp_discover",
                "description": (
                    "Discover available hardware devices connected via KHP. "
                    "Returns device IDs, names, types, and connection info. "
                    "Optionally filter by device_type."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "device_type": {
                            "type": "string",
                            "description": "Filter by device type (e.g., 'collaborative_robot', 'building_automation', 'lighting_controller')",
                        },
                    },
                },
            },
            {
                "name": "khp_read",
                "description": (
                    "Read a sensor value or property from a hardware device. "
                    "Returns the current value with type, unit, and timestamp. "
                    "Use khp_manifest first to see available readable properties."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "device_id": {"type": "string", "description": "Device identifier"},
                        "property": {"type": "string", "description": "Property name to read (e.g., 'temperature', 'joint_positions', 'ac_power')"},
                    },
                    "required": ["device_id", "property"],
                },
            },
            {
                "name": "khp_write",
                "description": (
                    "Set a property on a hardware device. Subject to safety limits: "
                    "hard limits block the write entirely, soft limits clamp the value. "
                    "Use khp_manifest to see writable properties and their safety bounds."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "device_id": {"type": "string", "description": "Device identifier"},
                        "property": {"type": "string", "description": "Property to set"},
                        "value": {"description": "Value to write (type depends on property)"},
                    },
                    "required": ["device_id", "property", "value"],
                },
            },
            {
                "name": "khp_execute",
                "description": (
                    "Execute a procedure on a hardware device. Procedures are multi step "
                    "operations like moving a robot, starting a pump, commissioning a device, "
                    "or running a diagnostic. Parameters vary per procedure."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "device_id": {"type": "string", "description": "Device identifier"},
                        "procedure": {"type": "string", "description": "Procedure name (e.g., 'move_linear', 'commission_device', 'find_studies')"},
                        "params": {"type": "object", "description": "Procedure parameters as key/value pairs"},
                    },
                    "required": ["device_id", "procedure"],
                },
            },
            {
                "name": "khp_manifest",
                "description": (
                    "Get the full capabilities manifest for a device. Shows all readable "
                    "properties, writable properties with safety limits, available procedures "
                    "with their parameters, and device metadata. Call this first to understand "
                    "what a device can do."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "device_id": {"type": "string", "description": "Device identifier"},
                    },
                    "required": ["device_id"],
                },
            },
            {
                "name": "khp_batch_read",
                "description": (
                    "Read multiple properties from one or more devices in a single call. "
                    "More efficient than multiple individual reads."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "reads": {
                            "type": "array",
                            "description": "Array of {device_id, property} objects to read",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "device_id": {"type": "string"},
                                    "property": {"type": "string"},
                                },
                                "required": ["device_id", "property"],
                            },
                        },
                    },
                    "required": ["reads"],
                },
            },
            {
                "name": "khp_emergency_stop",
                "description": (
                    "EMERGENCY STOP: Immediately halt one or all connected devices. "
                    "Use only when safety requires immediate cessation of all motion/output. "
                    "Omit device_id to stop ALL devices simultaneously."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "device_id": {"type": "string", "description": "Specific device to stop (omit for ALL)"},
                    },
                },
            },
            {
                "name": "khp_health",
                "description": (
                    "Get health status of all connected devices. Returns alerts, "
                    "connectivity status, and diagnostic information from each device's "
                    "monitor function."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {},
                },
            },
        ]

    def get_resource_definitions(self) -> list[dict]:
        """Return MCP resource definitions."""
        resources = [
            {
                "uri": "khp://devices",
                "name": "Connected Devices",
                "description": "List of all hardware devices currently connected via KHP",
                "mimeType": "application/json",
            },
        ]
        for dev_info in self.registry.list_all():
            resources.append({
                "uri": f"khp://device/{dev_info['device_id']}",
                "name": f"Device: {dev_info['name']}",
                "description": f"{dev_info['description']} ({dev_info['type']})",
                "mimeType": "application/json",
            })
            resources.append({
                "uri": f"khp://safety/{dev_info['device_id']}",
                "name": f"Safety: {dev_info['name']}",
                "description": f"Safety limits and current state for {dev_info['name']}",
                "mimeType": "application/json",
            })
        return resources

    async def handle_resource_read(self, uri: str) -> dict:
        """Read an MCP resource."""
        if uri == "khp://devices":
            return {"contents": [{"uri": uri, "mimeType": "application/json", "text": json.dumps(self.registry.list_all(), indent=2)}]}

        if uri.startswith("khp://device/"):
            device_id = uri.replace("khp://device/", "")
            manifest = self.registry.get_manifest(device_id)
            if manifest:
                return {"contents": [{"uri": uri, "mimeType": "application/json", "text": json.dumps(manifest, indent=2)}]}
            return {"error": f"Device '{device_id}' not found"}

        if uri.startswith("khp://safety/"):
            device_id = uri.replace("khp://safety/", "")
            manifest = self.registry.get_manifest(device_id)
            if manifest:
                safety_info = {
                    "device_id": device_id,
                    "safety_limits": manifest.get("safety_limits", []),
                }
                return {"contents": [{"uri": uri, "mimeType": "application/json", "text": json.dumps(safety_info, indent=2)}]}
            return {"error": f"Device '{device_id}' not found"}

        return {"error": f"Unknown resource: {uri}"}


def run_stdio_server(server: KHPMCPServer):
    """Run the MCP server over stdio using JSON RPC protocol.

    This is the standard transport for Claude Desktop, Claude Code, Cursor,
    and other MCP hosts that launch the server as a subprocess.
    """

    async def _run():
        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)
        await asyncio.get_event_loop().connect_read_pipe(lambda: protocol, sys.stdin.buffer)

        writer_transport, writer_protocol = await asyncio.get_event_loop().connect_write_pipe(
            asyncio.streams.FlowControlMixin, sys.stdout.buffer
        )
        writer = asyncio.StreamWriter(writer_transport, writer_protocol, None, asyncio.get_event_loop())

        async def send_response(response: dict):
            data = json.dumps(response)
            message = f"Content-Length: {len(data)}\r\n\r\n{data}"
            writer.write(message.encode())
            await writer.drain()

        async def read_message() -> dict | None:
            header = b""
            while True:
                line = await reader.readline()
                if not line:
                    return None
                header += line
                if line == b"\r\n":
                    break

            content_length = 0
            for h_line in header.decode().split("\r\n"):
                if h_line.lower().startswith("content-length:"):
                    content_length = int(h_line.split(":")[1].strip())

            if content_length == 0:
                return None

            body = await reader.readexactly(content_length)
            return json.loads(body.decode())

        # Send server info on initialize
        while True:
            message = await read_message()
            if message is None:
                break

            method = message.get("method", "")
            msg_id = message.get("id")
            params = message.get("params", {})

            if method == "initialize":
                response = {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {
                            "tools": {"listChanged": True},
                            "resources": {"subscribe": False, "listChanged": True},
                        },
                        "serverInfo": {
                            "name": "khp",
                            "version": "1.0.0",
                        },
                    },
                }
                await send_response(response)

            elif method == "notifications/initialized":
                pass

            elif method == "tools/list":
                tools = server.get_tool_definitions()
                response = {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {"tools": tools},
                }
                await send_response(response)

            elif method == "tools/call":
                tool_name = params.get("name", "")
                arguments = params.get("arguments", {})
                result = await server.handle_tool_call(tool_name, arguments)
                is_error = "error" in result

                response = {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "content": [
                            {
                                "type": "text",
                                "text": json.dumps(result, indent=2, default=str),
                            }
                        ],
                        "isError": is_error,
                    },
                }
                await send_response(response)

            elif method == "resources/list":
                resources = server.get_resource_definitions()
                response = {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {"resources": resources},
                }
                await send_response(response)

            elif method == "resources/read":
                uri = params.get("uri", "")
                result = await server.handle_resource_read(uri)
                response = {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": result,
                }
                await send_response(response)

            elif method == "ping":
                response = {"jsonrpc": "2.0", "id": msg_id, "result": {}}
                await send_response(response)

            else:
                response = {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "error": {"code": -32601, "message": f"Method not found: {method}"},
                }
                await send_response(response)

    asyncio.run(_run())


def load_drivers_from_config(server: KHPMCPServer, driver_names: list[str] | None = None, config_path: str | None = None):
    """Load and register drivers from a config file or explicit list."""
    if config_path:
        config_file = Path(config_path)
        if config_file.exists():
            config = json.loads(config_file.read_text())
            for device_config in config.get("devices", []):
                driver_type = device_config["driver"]
                device_id = device_config.get("device_id", driver_type)
                params = device_config.get("params", {})
                _instantiate_driver(server, driver_type, device_id, params)
            return

    if driver_names:
        for name in driver_names:
            _instantiate_driver(server, name, name, {})


def _instantiate_driver(server: KHPMCPServer, driver_type: str, device_id: str, params: dict):
    """Import and instantiate a driver by type name."""
    module_path = DRIVER_MODULES.get(driver_type)
    if not module_path:
        logger.warning(f"Unknown driver type: {driver_type}")
        return

    try:
        module = importlib.import_module(module_path)
        driver_classes = [
            v for v in vars(module).values()
            if isinstance(v, type) and hasattr(v, "name") and v.__module__ == module.__name__
        ]
        if driver_classes:
            driver_class = driver_classes[0]
            instance = driver_class(device_id=device_id, **params)
            server.register_driver(device_id, instance)
            logger.info(f"Registered: {device_id} ({driver_class.name})")
    except Exception as e:
        logger.warning(f"Failed to load driver '{driver_type}': {e}")


def main():
    """Entry point for the KHP MCP server."""
    import argparse

    parser = argparse.ArgumentParser(description="KHP MCP Server: AI hardware control via Model Context Protocol")
    parser.add_argument("--drivers", type=str, help="Comma separated list of drivers to load (e.g., universal_robots,knx,dmx)")
    parser.add_argument("--config", type=str, help="Path to device configuration JSON file")
    parser.add_argument("--log-level", type=str, default="WARNING", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    args = parser.parse_args()

    logging.basicConfig(level=getattr(logging, args.log_level), stream=sys.stderr)

    server = KHPMCPServer()

    driver_names = args.drivers.split(",") if args.drivers else None
    load_drivers_from_config(server, driver_names=driver_names, config_path=args.config)

    logger.info(f"KHP MCP Server starting with {len(server.registry.list_all())} devices")
    run_stdio_server(server)


if __name__ == "__main__":
    main()
