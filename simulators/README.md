# KHP Device Simulators

Virtual hardware endpoints for development and testing. Each simulator speaks the same protocol as real hardware so your KHP drivers work identically against simulators and real devices.

## Quick Start

```bash
# Run all simulators at once
docker compose -f simulators/docker-compose.yml up

# Or run individually (no Docker needed)
python -m simulators.universal_robots    # UR5e on ports 30004/29999
python -m simulators.modbus_sunspec      # Solar inverter on port 5020
python -m simulators.knx_gateway         # KNXnet/IP on port 3671
python -m simulators.artnet_node         # Art Net on port 6454
```

## Available Simulators

| Simulator | Protocol | Ports | Simulates |
|-----------|----------|-------|-----------|
| Universal Robots | RTDE + Dashboard | 30004, 29999 | UR5e cobot with 6 axis motion |
| SunSpec Inverter | Modbus TCP | 5020 | 10kW solar inverter + battery |
| KNX Gateway | KNXnet/IP (UDP) | 3671 | 3 room building (lights, HVAC, blinds) |
| Art Net Node | Art Net (UDP) | 6454 | 4 universe DMX receiver |
| Modbus PLC | Modbus TCP | 5021 | Generic PLC (community image) |
| MQTT Broker | MQTT + WS | 1883, 9001 | Mosquitto (community image) |
| OPC UA Server | OPC UA | 4840 | open62541 CTT server |

## How It Works

Each simulator implements the actual protocol at the byte level. The UR simulator, for example, responds to RTDE binary packets with properly formatted joint positions, TCP forces, and safety states. The SunSpec simulator maintains a full register map with model discovery, scale factors, and time varying solar production curves.

## Writing Tests Against Simulators

```python
import asyncio
from drivers.universal_robots import UniversalRobotsDevice

async def test_ur_movement():
    robot = UniversalRobotsDevice(host="localhost")
    await robot.connect()
    
    positions = robot.joint_positions()
    assert len(positions) == 6
    
    result = robot.move_joint(target=[0, -1.57, 1.57, -1.57, -1.57, 0])
    assert result["status"] == "complete"
    
    await robot.disconnect()
```

## Docker Compose Services

The `docker-compose.yml` brings up all simulators in isolated containers with health checks. Use this for CI integration testing:

```bash
docker compose -f simulators/docker-compose.yml up -d
pytest tests/ --integration
docker compose -f simulators/docker-compose.yml down
```
