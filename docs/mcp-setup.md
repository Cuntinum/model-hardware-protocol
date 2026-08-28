# MCP Server Setup

KHP exposes hardware devices as MCP (Model Context Protocol) tools, allowing any MCP-compatible AI agent to discover and control physical hardware.

## Starting the Server

### Python

```python
from mcp.server import KHPMCPServer
from drivers.docker_sim.driver import SimulatedThermocycler

# Create devices
tc = SimulatedThermocycler(device_id="lab_tc_1")

# Start MCP server
server = KHPMCPServer()
server.register_driver(tc)
server.serve(host="0.0.0.0", port=7400)
```

### CLI

```bash
khp serve --host 0.0.0.0 --port 7400
```

## Available MCP Tools

The server exposes these tools to AI agents:

| Tool | Description |
|------|-------------|
| `khp_discover` | Find available devices (filter by type/capability) |
| `khp_read` | Read a device property |
| `khp_write` | Write a device property (safety-checked) |
| `khp_execute` | Run a procedure on a device |
| `khp_manifest` | Get full capabilities manifest |
| `khp_bus_read` | Read from State Bus slot |
| `khp_bus_write` | Write to State Bus slot |
| `khp_emergency_stop` | Emergency stop (one device or all) |

## Agent Workflow

A typical AI agent interaction:

```
Agent: khp_discover(device_type="thermocycler")
  → [{device_id: "lab_tc_1", name: "Lab Thermocycler", status: "online"}]

Agent: khp_manifest(device_id="lab_tc_1")
  → {readable: {block_temperature: ...}, writable: {set_temperature: ...}, safety: {...}}

Agent: khp_read(device_id="lab_tc_1", property="block_temperature")
  → {value: 22.3, unit: "celsius", timestamp: "..."}

Agent: khp_write(device_id="lab_tc_1", property="set_temperature", value=95.0)
  → {success: true, actual_value: 95.0, safety_check: "passed"}
```

## Connecting to Claude Desktop

Add to your Claude Desktop MCP config (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "khp": {
      "command": "khp",
      "args": ["serve", "--port", "7400"]
    }
  }
}
```

## Security

- The MCP server runs locally by default (bind to `127.0.0.1`)
- For remote access, use TLS and token authentication
- All operations go through the safety layer — the MCP server cannot bypass limits
- Audit logs capture every operation with timestamps

## State Bus via MCP

Agents can also interact with the shared State Bus:

```
Agent: khp_bus_write(slot_id="experiment_phase", value="heating")
Agent: khp_bus_read(slot_id="total_cycles_completed")
  → {value: 30, type: "int", last_updated: "..."}
```

This enables multi-device coordination where the agent manages experiment state.
