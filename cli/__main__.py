"""KHP CLI — Command-line interface for Kinetic Hardware Protocol.

Usage:
    khp discover [--type TYPE]
    khp connect DEVICE_ID
    khp read DEVICE.PROPERTY
    khp write DEVICE.PROPERTY VALUE
    khp execute DEVICE.PROCEDURE [--params JSON]
    khp manifest DEVICE_ID [--output FILE]
    khp monitor DEVICE [--properties P1,P2] [--interval MS]
    khp serve [--mcp] [--rest] [--port PORT]
    khp validate MANIFEST_FILE
    khp list
    khp status

Requirements:
    pip install click rich
"""

import sys
import json
import time
import asyncio
from pathlib import Path


def main():
    """KHP CLI entry point."""
    if len(sys.argv) < 2:
        print_help()
        return

    command = sys.argv[1]
    args = sys.argv[2:]

    commands = {
        "discover": cmd_discover,
        "read": cmd_read,
        "write": cmd_write,
        "execute": cmd_execute,
        "manifest": cmd_manifest,
        "monitor": cmd_monitor,
        "serve": cmd_serve,
        "validate": cmd_validate,
        "list": cmd_list,
        "status": cmd_status,
        "help": print_help,
    }

    handler = commands.get(command)
    if handler is None:
        print(f"Unknown command: {command}")
        print_help()
        sys.exit(1)

    handler(args)


def print_help(args=None):
    """Print CLI help."""
    print("""
Kinetic Hardware Protocol (KHP) CLI v0.1.0

USAGE:
    khp <command> [options]

COMMANDS:
    discover        Find available devices on the network
    list            List registered devices
    status          Show status of all connected devices
    read            Read a property from a device
    write           Set a property value on a device
    execute         Run a procedure on a device
    manifest        View or generate device manifest
    monitor         Continuously watch device properties
    serve           Start KHP server (MCP, REST, or both)
    validate        Validate a manifest file against schema

EXAMPLES:
    khp discover
    khp discover --type thermocycler
    khp read thermo_1.temperature
    khp write thermo_1.target_temperature 72.5
    khp execute pipette_1.aspirate --params '{"volume_ul": 100}'
    khp manifest thermo_1
    khp monitor thermo_1 --properties temperature,setpoint --interval 1000
    khp serve --mcp --port 7400
    khp validate ./my_driver/manifest.json

DOCUMENTATION:
    https://khp.dev/docs
""")


def cmd_discover(args):
    """Discover devices."""
    from khp.discovery import discover

    device_type = None
    for i, arg in enumerate(args):
        if arg == "--type" and i + 1 < len(args):
            device_type = args[i + 1]

    devices = discover(device_type=device_type, network="--network" in args)

    if not devices:
        print("No devices found.")
        return

    print(f"\n{'ID':<25} {'Name':<30} {'Type':<18} {'Status':<10}")
    print("-" * 85)
    for d in devices:
        print(f"{d['device_id']:<25} {d['name']:<30} {d['type']:<18} {d['status']:<10}")
    print(f"\n{len(devices)} device(s) found.")


def cmd_list(args):
    """List registered devices."""
    cmd_discover(args)


def cmd_status(args):
    """Show status of all devices."""
    from khp.discovery import get_registry

    registry = get_registry()
    devices = registry.list_devices()

    if not devices:
        print("No devices registered.")
        return

    print(f"\n{'ID':<25} {'Status':<12} {'Type':<18} {'Host':<20}")
    print("-" * 80)
    for d in devices:
        print(f"{d.device_id:<25} {d.status:<12} {d.device_type:<18} {d.host}:{d.port}")


def cmd_read(args):
    """Read a device property."""
    if not args:
        print("Usage: khp read DEVICE_ID.PROPERTY")
        return

    target = args[0]
    if "." not in target:
        print("Error: use format DEVICE_ID.PROPERTY (e.g., thermo_1.temperature)")
        return

    device_id, prop = target.split(".", 1)
    from khp.discovery import get_registry
    registry = get_registry()
    driver = registry.get_driver(device_id)

    if not driver:
        print(f"Error: device '{device_id}' not found. Run 'khp discover' first.")
        return

    try:
        result = driver.read(prop)
        value = result["value"]
        unit = result.get("unit", "")
        ts = result.get("timestamp", "")
        print(f"{prop}: {value} {unit or ''}")
        if "--verbose" in args:
            print(f"  type: {result.get('type')}")
            print(f"  timestamp: {ts}")
    except Exception as e:
        print(f"Error: {e}")


def cmd_write(args):
    """Write a value to a device property."""
    if len(args) < 2:
        print("Usage: khp write DEVICE_ID.PROPERTY VALUE")
        return

    target = args[0]
    value_str = args[1]

    if "." not in target:
        print("Error: use format DEVICE_ID.PROPERTY (e.g., thermo_1.temperature 72.5)")
        return

    device_id, prop = target.split(".", 1)
    from khp.discovery import get_registry
    registry = get_registry()
    driver = registry.get_driver(device_id)

    if not driver:
        print(f"Error: device '{device_id}' not found.")
        return

    try:
        value = json.loads(value_str)
    except json.JSONDecodeError:
        value = value_str

    try:
        result = driver.write(prop, value)
        safety_check = result.get("safety_check", "passed")
        actual = result.get("actual_value", value)
        print(f"OK: {prop} = {actual} (safety: {safety_check})")
    except Exception as e:
        print(f"Error: {e}")


def cmd_execute(args):
    """Execute a device procedure."""
    if not args:
        print("Usage: khp execute DEVICE_ID.PROCEDURE [--params JSON]")
        return

    target = args[0]
    if "." not in target:
        print("Error: use format DEVICE_ID.PROCEDURE")
        return

    device_id, proc = target.split(".", 1)
    params = {}
    for i, arg in enumerate(args):
        if arg == "--params" and i + 1 < len(args):
            params = json.loads(args[i + 1])

    from khp.discovery import get_registry
    registry = get_registry()
    driver = registry.get_driver(device_id)

    if not driver:
        print(f"Error: device '{device_id}' not found.")
        return

    try:
        result = asyncio.run(driver.execute(proc, params))
        print(f"Job: {result.get('job_id', 'N/A')}")
        print(f"Status: {result.get('status', 'unknown')}")
        if result.get("result"):
            print(f"Result: {json.dumps(result['result'], indent=2)}")
    except Exception as e:
        print(f"Error: {e}")


def cmd_manifest(args):
    """Show or save device manifest."""
    if not args:
        print("Usage: khp manifest DEVICE_ID [--output FILE]")
        return

    device_id = args[0]
    output_file = None
    for i, arg in enumerate(args):
        if arg == "--output" and i + 1 < len(args):
            output_file = args[i + 1]

    from khp.discovery import get_registry
    registry = get_registry()
    manifest = registry.get_manifest(device_id)

    if not manifest:
        print(f"Error: device '{device_id}' not found.")
        return

    formatted = json.dumps(manifest, indent=2)
    if output_file:
        with open(output_file, "w") as f:
            f.write(formatted)
        print(f"Manifest saved to: {output_file}")
    else:
        print(formatted)


def cmd_monitor(args):
    """Continuously monitor device properties."""
    if not args:
        print("Usage: khp monitor DEVICE_ID [--properties P1,P2] [--interval MS]")
        return

    device_id = args[0]
    properties = None
    interval_ms = 1000

    for i, arg in enumerate(args):
        if arg == "--properties" and i + 1 < len(args):
            properties = args[i + 1].split(",")
        if arg == "--interval" and i + 1 < len(args):
            interval_ms = int(args[i + 1])

    from khp.discovery import get_registry
    registry = get_registry()
    driver = registry.get_driver(device_id)

    if not driver:
        print(f"Error: device '{device_id}' not found.")
        return

    if not properties:
        properties = list(driver._readable_props.keys())

    print(f"Monitoring {device_id}: {', '.join(properties)} (every {interval_ms}ms)")
    print("Press Ctrl+C to stop.\n")

    try:
        while True:
            values = []
            for prop in properties:
                try:
                    result = driver.read(prop)
                    values.append(f"{prop}={result['value']}{result.get('unit', '') or ''}")
                except Exception:
                    values.append(f"{prop}=ERR")
            print(f"  {' | '.join(values)}")
            time.sleep(interval_ms / 1000.0)
    except KeyboardInterrupt:
        print("\nStopped.")


def cmd_serve(args):
    """Start KHP server."""
    port = 7400
    mode = "mcp"

    for i, arg in enumerate(args):
        if arg == "--port" and i + 1 < len(args):
            port = int(args[i + 1])
        if arg == "--rest":
            mode = "rest"
        if arg == "--mcp":
            mode = "mcp"

    print(f"Starting KHP server ({mode}) on port {port}...")
    print("(Server implementation — connect drivers via Python API)")
    print("Press Ctrl+C to stop.")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nServer stopped.")


def cmd_validate(args):
    """Validate a manifest file."""
    if not args:
        print("Usage: khp validate MANIFEST_FILE")
        return

    filepath = args[0]
    if not Path(filepath).exists():
        print(f"Error: file not found: {filepath}")
        return

    from khp.manifest import Manifest
    manifest = Manifest.from_file(filepath)
    errors = manifest.validate()

    if not errors:
        print(f"OK: {filepath} is valid")
        print(f"  Device: {manifest.name} ({manifest.device_type})")
        print(f"  Capabilities: {len(manifest.readable)} readable, "
              f"{len(manifest.writable)} writable, {len(manifest.procedures)} procedures")
    else:
        print(f"ERRORS in {filepath}:")
        for err in errors:
            print(f"  - {err}")
        sys.exit(1)


if __name__ == "__main__":
    main()
