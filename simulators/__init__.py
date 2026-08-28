"""KHP Device Simulators.

Virtual hardware endpoints that mimic real protocol behavior for development
and testing. Each simulator responds to the same protocol its real counterpart
uses so that KHP drivers work identically against simulators and real hardware.

Usage:
    python -m simulators.universal_robots  # UR RTDE on port 30004
    python -m simulators.knx              # KNXnet/IP on port 3671
    python -m simulators.dmx              # Art-Net on port 6454
    python -m simulators.modbus           # Modbus TCP on port 502

Or via Docker:
    docker compose -f simulators/docker-compose.yml up
"""
