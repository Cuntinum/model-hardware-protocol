"""KHP Quick Start — Run a simulated lab with AI agent control.

This example creates a virtual lab with:
- A thermocycler (simulated PCR machine)
- A liquid handler (simulated 8-channel pipette)
- A robotic arm (simulated pick-and-place)
- A camera (monitors the setup)

All devices are registered and exposed via KHP, ready for AI agent control.

Run:
    python examples/quickstart.py
"""

import asyncio
import sys
sys.path.insert(0, "sdk/python")

from khp import discover, register, StateBus, Slot, Transform
from khp.discovery import get_registry
from khp.mcp.server import create_mcp_server


async def main():
    print("=" * 60)
    print("KHP Quick Start — Virtual Lab")
    print("=" * 60)

    # Import simulated devices
    sys.path.insert(0, "drivers/docker-sim")
    from driver import SimulatedThermocycler, SimulatedLiquidHandler, SimulatedRoboticArm

    # Create devices
    thermo = SimulatedThermocycler(device_id="pcr_machine_1")
    pipette = SimulatedLiquidHandler(device_id="pipette_8ch_1")
    robot = SimulatedRoboticArm(device_id="robot_arm_1")

    # Add natural language tags (for AI agent context)
    thermo.set_tags(
        location="Lab 204, Bench 2, left side",
        notes="Used for qPCR. Lid should always be at 105°C during runs.",
        protocols="Standard PCR: 95°C/30s → 55°C/30s → 72°C/60s × 30 cycles",
    )
    pipette.set_tags(
        location="Lab 204, Bench 2, center",
        notes="8-channel, 1-1000 uL range. Tips in rack position A.",
        quirks="Channel 7 runs slightly fast. Use 'slow' speed for viscous liquids.",
    )
    robot.set_tags(
        location="Lab 204, between bench 2 and plate reader",
        notes="Doosan A0509. Moves plates between pipette and thermocycler.",
        safety="Never move while pipette is dispensing. Clear zone first.",
    )

    # Connect all devices
    print("\n[1] Connecting devices...")
    await thermo.connect()
    await pipette.connect()
    await robot.connect()
    print(f"  ✓ {thermo.name} ({thermo.device_id})")
    print(f"  ✓ {pipette.name} ({pipette.device_id})")
    print(f"  ✓ {robot.name} ({robot.device_id})")

    # Register with discovery system
    register(thermo)
    register(pipette)
    register(robot)

    # Set up state bus
    bus = StateBus()
    temp_slot = bus.create_slot("pcr_temperature", type="float", unit="celsius")
    bus.add_transform(Transform(
        transform_id="temp_alert",
        input_slot="pcr_temperature",
        operation="threshold",
        params={"above": 98.0},
        output_event="temperature_warning",
    ))

    print("\n[2] Discovering devices...")
    devices = discover()
    for d in devices:
        print(f"  Found: {d['name']} ({d['device_id']}) — {d['type']}")

    # Demo: Read properties
    print("\n[3] Reading device state...")
    temp = thermo.read("block_temperature")
    print(f"  Thermocycler temp: {temp['value']}°C")

    tips = pipette.read("tip_status")
    print(f"  Pipette tips: {tips['value']}")

    pos = robot.read("end_effector_position")
    print(f"  Robot position: {pos['value']}")

    # Demo: Execute procedures
    print("\n[4] Running lab workflow...")
    print("  → Picking up tips...")
    result = await pipette.execute("pick_up_tips", {"channels": [0, 1, 2, 3]})
    print(f"    {result['result']}")

    print("  → Aspirating 100 uL from A1...")
    result = await pipette.execute("aspirate", {"volume_ul": 100, "well": "A1"})
    print(f"    {result['result']}")

    print("  → Dispensing to B1...")
    result = await pipette.execute("dispense", {"volume_ul": 100, "well": "B1"})
    print(f"    {result['result']}")

    print("  → Setting PCR temperature to 95°C...")
    thermo.write("temperature_setpoint", 95.0)
    print(f"    Target set. Current: {thermo.read('block_temperature')['value']}°C")

    print("  → Robot picking plate...")
    result = await robot.execute("pick_and_place", {
        "pick_x": 100, "pick_y": 0, "pick_z": 10,
        "place_x": 300, "place_y": 0, "place_z": 10,
        "object_name": "96_well_plate",
    })
    print(f"    {result['result']}")

    # Demo: Safety limits
    print("\n[5] Safety demonstration...")
    print("  → Attempting to set thermocycler to 150°C (hard limit: 100°C)...")
    try:
        thermo.write("temperature_setpoint", 150.0)
    except Exception as e:
        print(f"    BLOCKED: {e.message}")

    print("  → Attempting to set robot speed to 5.0 (hard limit: 1.0)...")
    try:
        robot.write("speed", 5.0)
    except Exception as e:
        print(f"    BLOCKED: {e.message}")

    # Demo: Get manifest
    print("\n[6] Device manifest (thermocycler)...")
    manifest = thermo.get_manifest()
    print(f"  Readable: {list(manifest['readable'].keys())}")
    print(f"  Writable: {list(manifest['writable'].keys())}")
    print(f"  Procedures: {list(manifest['procedures'].keys())}")
    print(f"  Safety: {list(manifest['safety']['hard_limits'].keys())}")

    # Demo: MCP server
    print("\n[7] MCP Server ready...")
    mcp_server = create_mcp_server(drivers=[thermo, pipette, robot], state_bus=bus)
    tools = mcp_server.get_tools()
    print(f"  {len(tools)} MCP tools registered:")
    for tool in tools:
        print(f"    • {tool['name']}: {tool['description'][:60]}...")

    # Clean up
    print("\n[8] Disconnecting...")
    await thermo.disconnect()
    await pipette.disconnect()
    await robot.disconnect()
    print("  All devices disconnected.")

    print("\n" + "=" * 60)
    print("KHP Quick Start complete!")
    print("Next: Connect real hardware by writing a driver (see drivers/ folder)")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
