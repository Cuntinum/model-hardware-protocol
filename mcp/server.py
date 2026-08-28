"""KHP MCP Server — Exposes all connected devices as MCP tools.

This turns any KHP-connected device into a tool that AI agents can call
via the Model Context Protocol. Each device's readable/writable/procedure
methods become individual MCP tools.

Usage:
    python -m khp.mcp.server
    # or
    khp serve --mcp --port 7400

The server registers with the MCP host and exposes tools like:
    khp_discover         → Find available devices
    khp_read             → Read a property from a device
    khp_write            → Write a value to a device (with safety checks)
    khp_execute          → Run a procedure on a device
    khp_manifest         → Get full capabilities of a device
    khp_bus_read         → Read from the state bus
    khp_bus_write        → Write to the state bus
    khp_emergency_stop   → Emergency stop all devices

Requirements:
    pip install mcp
"""

import json
import asyncio
from typing import Any, Dict, List, Optional

from khp.core import Driver
from khp.discovery import get_registry, discover
from khp.state_bus import StateBus


class KHPMCPServer:
    """MCP server that exposes KHP devices as tools."""

    def __init__(self, state_bus: StateBus = None):
        self._registry = get_registry()
        self._bus = state_bus or StateBus()
        self._tools = self._build_tools()

    def _build_tools(self) -> List[dict]:
        """Build MCP tool definitions for all KHP primitives."""
        return [
            {
                "name": "khp_discover",
                "description": "Discover available hardware devices on the network. "
                               "Returns a list of devices with their type, status, and capabilities.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "device_type": {
                            "type": "string",
                            "description": "Filter by device type (e.g., 'thermocycler', 'liquid_handler', 'sensor')"
                        },
                        "capability": {
                            "type": "string",
                            "description": "Filter by capability name (e.g., 'temperature', 'aspirate')"
                        },
                    },
                },
            },
            {
                "name": "khp_read",
                "description": "Read the current value of a property from a connected device. "
                               "Returns the value with type, unit, and timestamp.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "device_id": {
                            "type": "string",
                            "description": "The ID of the device to read from"
                        },
                        "property": {
                            "type": "string",
                            "description": "The property name to read (e.g., 'temperature', 'position')"
                        },
                    },
                    "required": ["device_id", "property"],
                },
            },
            {
                "name": "khp_write",
                "description": "Set a property value on a connected device. "
                               "Subject to safety limits — values exceeding hard limits are blocked, "
                               "values exceeding soft limits are clamped.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "device_id": {
                            "type": "string",
                            "description": "The ID of the device to write to"
                        },
                        "property": {
                            "type": "string",
                            "description": "The property name to set"
                        },
                        "value": {
                            "description": "The value to set (type depends on property)"
                        },
                    },
                    "required": ["device_id", "property", "value"],
                },
            },
            {
                "name": "khp_execute",
                "description": "Execute a procedure (multi-step operation) on a device. "
                               "Some procedures may require human confirmation before running.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "device_id": {
                            "type": "string",
                            "description": "The ID of the device"
                        },
                        "procedure": {
                            "type": "string",
                            "description": "The procedure name to execute"
                        },
                        "params": {
                            "type": "object",
                            "description": "Parameters for the procedure"
                        },
                    },
                    "required": ["device_id", "procedure"],
                },
            },
            {
                "name": "khp_manifest",
                "description": "Get the full capabilities manifest for a device. "
                               "Shows all readable properties, writable properties, "
                               "procedures, safety limits, and metadata.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "device_id": {
                            "type": "string",
                            "description": "The ID of the device"
                        },
                    },
                    "required": ["device_id"],
                },
            },
            {
                "name": "khp_bus_read",
                "description": "Read a value from the KHP state bus (shared data layer).",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "slot_id": {
                            "type": "string",
                            "description": "The slot ID to read from"
                        },
                    },
                    "required": ["slot_id"],
                },
            },
            {
                "name": "khp_bus_write",
                "description": "Write a value to the KHP state bus.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "slot_id": {
                            "type": "string",
                            "description": "The slot ID to write to"
                        },
                        "value": {
                            "description": "The value to write"
                        },
                    },
                    "required": ["slot_id", "value"],
                },
            },
            {
                "name": "khp_emergency_stop",
                "description": "EMERGENCY STOP — immediately halt all connected devices. "
                               "Use only in emergency situations.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "device_id": {
                            "type": "string",
                            "description": "Stop a specific device (omit for ALL devices)"
                        },
                    },
                },
            },
        ]

    async def handle_tool_call(self, tool_name: str, arguments: dict) -> dict:
        """Route an MCP tool call to the appropriate KHP handler."""
        handlers = {
            "khp_discover": self._handle_discover,
            "khp_read": self._handle_read,
            "khp_write": self._handle_write,
            "khp_execute": self._handle_execute,
            "khp_manifest": self._handle_manifest,
            "khp_bus_read": self._handle_bus_read,
            "khp_bus_write": self._handle_bus_write,
            "khp_emergency_stop": self._handle_emergency_stop,
        }
        handler = handlers.get(tool_name)
        if not handler:
            return {"error": f"Unknown tool: {tool_name}"}
        try:
            return await handler(arguments)
        except Exception as e:
            if hasattr(e, "to_dict"):
                return e.to_dict()
            return {"error": str(e), "type": type(e).__name__}

    async def _handle_discover(self, args: dict) -> dict:
        devices = discover(
            device_type=args.get("device_type"),
            capability=args.get("capability"),
        )
        return {"devices": devices, "count": len(devices)}

    async def _handle_read(self, args: dict) -> dict:
        device_id = args["device_id"]
        property_name = args["property"]
        driver = self._registry.get_driver(device_id)
        if not driver:
            from khp.errors import DeviceNotFoundError
            raise DeviceNotFoundError(f"Device '{device_id}' not found", device_id=device_id)
        return driver.read(property_name)

    async def _handle_write(self, args: dict) -> dict:
        device_id = args["device_id"]
        property_name = args["property"]
        value = args["value"]
        driver = self._registry.get_driver(device_id)
        if not driver:
            from khp.errors import DeviceNotFoundError
            raise DeviceNotFoundError(f"Device '{device_id}' not found", device_id=device_id)
        return driver.write(property_name, value)

    async def _handle_execute(self, args: dict) -> dict:
        device_id = args["device_id"]
        procedure_name = args["procedure"]
        params = args.get("params", {})
        driver = self._registry.get_driver(device_id)
        if not driver:
            from khp.errors import DeviceNotFoundError
            raise DeviceNotFoundError(f"Device '{device_id}' not found", device_id=device_id)
        return await driver.execute(procedure_name, params)

    async def _handle_manifest(self, args: dict) -> dict:
        device_id = args["device_id"]
        manifest = self._registry.get_manifest(device_id)
        if not manifest:
            from khp.errors import DeviceNotFoundError
            raise DeviceNotFoundError(f"Device '{device_id}' not found", device_id=device_id)
        return manifest

    async def _handle_bus_read(self, args: dict) -> dict:
        slot_id = args["slot_id"]
        result = self._bus.read_slot(slot_id)
        if result is None:
            return {"error": f"Slot '{slot_id}' not found"}
        return result

    async def _handle_bus_write(self, args: dict) -> dict:
        slot_id = args["slot_id"]
        value = args["value"]
        self._bus.write_slot(slot_id, value)
        return {"success": True, "slot_id": slot_id}

    async def _handle_emergency_stop(self, args: dict) -> dict:
        device_id = args.get("device_id")
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
        return {"emergency_stop": True, "devices_stopped": stopped}

    def get_tools(self) -> List[dict]:
        """Return MCP tool definitions for registration."""
        return self._tools


def create_mcp_server(drivers: List[Driver] = None, state_bus: StateBus = None):
    """Create a KHP MCP server with optional pre-registered drivers."""
    from khp.discovery import register
    server = KHPMCPServer(state_bus=state_bus)
    if drivers:
        for driver in drivers:
            register(driver)
    return server
