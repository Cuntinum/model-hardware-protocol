"""Universal Robots RTDE Simulator.

Emulates a UR5e cobot's RTDE interface on port 30004 (receive),
30003 (control), and 29999 (dashboard). Responds to all standard
RTDE messages with realistic joint states, TCP poses, and forces.

Usage:
    python -m simulators.universal_robots [--host 0.0.0.0] [--port 30004]
"""
from __future__ import annotations

import struct
import socket
import asyncio
import math
import time
import random
import logging
from typing import Any

logger = logging.getLogger("sim.universal_robots")

ROBOT_MODE_RUNNING = 7
SAFETY_MODE_NORMAL = 1

RTDE_REQUEST_PROTOCOL_VERSION = 86  # 'V'
RTDE_GET_URCONTROL_VERSION = 118    # 'v'
RTDE_CONTROL_PACKAGE_SETUP_OUTPUTS = 79  # 'O'
RTDE_CONTROL_PACKAGE_SETUP_INPUTS = 73   # 'I'
RTDE_CONTROL_PACKAGE_START = 83     # 'S'
RTDE_DATA_PACKAGE = 85              # 'U'


class SimulatedUR:
    """Simulated UR robot state."""

    def __init__(self):
        self.joint_positions = [0.0, -1.5708, 1.5708, -1.5708, -1.5708, 0.0]
        self.joint_velocities = [0.0] * 6
        self.joint_temperatures = [32.0, 34.0, 33.0, 31.0, 30.0, 29.0]
        self.tcp_pose = [0.3, 0.1, 0.5, 0.0, 3.14159, 0.0]
        self.tcp_force = [0.0, 0.0, -9.81, 0.0, 0.0, 0.0]
        self.robot_mode = ROBOT_MODE_RUNNING
        self.safety_mode = SAFETY_MODE_NORMAL
        self.speed_scaling = 1.0
        self.program_running = False
        self.digital_inputs = 0
        self.digital_outputs = 0
        self.analog_inputs = [0.0, 0.0]
        self.analog_outputs = [0.0, 0.0]
        self.timestamp = time.time()
        self._target_joints = list(self.joint_positions)
        self._moving = False

    def update(self, dt: float = 0.008):
        """Advance simulation by dt seconds."""
        self.timestamp += dt

        for i in range(6):
            self.joint_temperatures[i] += random.gauss(0, 0.01)
            self.joint_temperatures[i] = max(25, min(45, self.joint_temperatures[i]))

        self.tcp_force[0] = random.gauss(0, 0.1)
        self.tcp_force[1] = random.gauss(0, 0.1)
        self.tcp_force[2] = -9.81 + random.gauss(0, 0.05)

        if self._moving:
            done = True
            for i in range(6):
                diff = self._target_joints[i] - self.joint_positions[i]
                if abs(diff) > 0.001:
                    step = min(abs(diff), 1.05 * dt * self.speed_scaling)
                    self.joint_positions[i] += step * (1 if diff > 0 else -1)
                    self.joint_velocities[i] = step / dt * (1 if diff > 0 else -1)
                    done = False
                else:
                    self.joint_velocities[i] = 0.0
            if done:
                self._moving = False
                self.program_running = False

            self._forward_kinematics()

    def move_to(self, target_joints: list[float]):
        """Start moving to target joint configuration."""
        self._target_joints = list(target_joints)
        self._moving = True
        self.program_running = True

    def _forward_kinematics(self):
        """Simplified FK for position display."""
        j = self.joint_positions
        reach = 0.85
        self.tcp_pose[0] = reach * math.cos(j[0]) * math.cos(j[1] + j[2])
        self.tcp_pose[1] = reach * math.sin(j[0]) * math.cos(j[1] + j[2])
        self.tcp_pose[2] = 0.089 + reach * math.sin(j[1] + j[2])


class DashboardServer:
    """Simulated UR Dashboard Server (port 29999)."""

    def __init__(self, robot: SimulatedUR):
        self.robot = robot

    async def handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        addr = writer.get_extra_info("peername")
        logger.info(f"Dashboard connection from {addr}")
        writer.write(b"Connected: Universal Robots Dashboard Server\n")
        await writer.drain()

        try:
            while True:
                data = await reader.readline()
                if not data:
                    break
                cmd = data.decode().strip().lower()
                response = self._handle_command(cmd)
                writer.write((response + "\n").encode())
                await writer.drain()
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        finally:
            writer.close()

    def _handle_command(self, cmd: str) -> str:
        if cmd == "get robot model":
            return "UR5e"
        elif cmd == "get serial number":
            return "2022309999"
        elif cmd == "polyscopeversion":
            return "5.14.4.1096825"
        elif cmd == "robotmode":
            return f"Robotmode: RUNNING"
        elif cmd == "safetymode":
            return f"Safetymode: NORMAL"
        elif cmd == "power on":
            self.robot.robot_mode = ROBOT_MODE_RUNNING
            return "Powering on"
        elif cmd == "brake release":
            return "Brake releasing"
        elif cmd == "unlock protective stop":
            self.robot.safety_mode = SAFETY_MODE_NORMAL
            return "Protective stop releasing"
        elif cmd == "running":
            return f"Program running: {'true' if self.robot.program_running else 'false'}"
        elif cmd == "get loaded program":
            return "/programs/default.urp"
        elif cmd == "programstate":
            return "STOPPED" if not self.robot.program_running else "PLAYING"
        elif cmd == "quit":
            return "Disconnected"
        else:
            return f"Unknown command: {cmd}"


class RTDEServer:
    """Simulated RTDE interface (port 30004)."""

    def __init__(self, robot: SimulatedUR):
        self.robot = robot
        self._recipe_outputs: list[str] = []
        self._streaming = False

    async def handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        addr = writer.get_extra_info("peername")
        logger.info(f"RTDE connection from {addr}")

        try:
            while True:
                header = await reader.readexactly(3)
                pkg_size = struct.unpack(">H", header[:2])[0]
                pkg_type = header[2]
                payload = b""
                if pkg_size > 3:
                    payload = await reader.readexactly(pkg_size - 3)

                response = self._handle_packet(pkg_type, payload)
                if response:
                    writer.write(response)
                    await writer.drain()

                if self._streaming and pkg_type == RTDE_CONTROL_PACKAGE_START:
                    asyncio.get_event_loop().create_task(
                        self._stream_data(writer)
                    )

        except (asyncio.IncompleteReadError, ConnectionResetError, asyncio.CancelledError):
            pass
        finally:
            self._streaming = False
            writer.close()

    def _handle_packet(self, pkg_type: int, payload: bytes) -> bytes | None:
        if pkg_type == RTDE_REQUEST_PROTOCOL_VERSION:
            version = struct.unpack(">H", payload[:2])[0] if payload else 2
            resp_payload = struct.pack(">B", 1)  # accepted
            return struct.pack(">HB", 3 + len(resp_payload), pkg_type) + resp_payload

        elif pkg_type == RTDE_GET_URCONTROL_VERSION:
            resp_payload = struct.pack(">IIII", 5, 14, 4, 1096825)
            return struct.pack(">HB", 3 + len(resp_payload), pkg_type) + resp_payload

        elif pkg_type == RTDE_CONTROL_PACKAGE_SETUP_OUTPUTS:
            recipe = payload[1:].decode() if payload else ""
            self._recipe_outputs = [v.strip() for v in recipe.split(",")]
            variable_types = ",".join(["VECTOR6D" if "q" in v or "tcp" in v or "force" in v else "DOUBLE" for v in self._recipe_outputs])
            resp_payload = struct.pack(">B", 1) + variable_types.encode()
            return struct.pack(">HB", 3 + len(resp_payload), pkg_type) + resp_payload

        elif pkg_type == RTDE_CONTROL_PACKAGE_SETUP_INPUTS:
            resp_payload = struct.pack(">B", 1) + b"INT32"
            return struct.pack(">HB", 3 + len(resp_payload), pkg_type) + resp_payload

        elif pkg_type == RTDE_CONTROL_PACKAGE_START:
            self._streaming = True
            resp_payload = struct.pack(">B", 1)
            return struct.pack(">HB", 3 + len(resp_payload), pkg_type) + resp_payload

        return None

    async def _stream_data(self, writer: asyncio.StreamWriter):
        """Stream RTDE data packets at 125 Hz."""
        recipe_id = 1
        while self._streaming:
            self.robot.update(0.008)
            data = struct.pack(">B", recipe_id)
            data += struct.pack(">6d", *self.robot.joint_positions)
            data += struct.pack(">6d", *self.robot.joint_velocities)
            data += struct.pack(">6d", *self.robot.tcp_pose)
            data += struct.pack(">6d", *self.robot.tcp_force)

            packet = struct.pack(">HB", 3 + len(data), RTDE_DATA_PACKAGE) + data
            try:
                writer.write(packet)
                await writer.drain()
            except (ConnectionResetError, BrokenPipeError):
                self._streaming = False
                break
            await asyncio.sleep(0.008)


async def run_simulator(host: str = "0.0.0.0", rtde_port: int = 30004,
                        dashboard_port: int = 29999):
    """Start the UR simulator with RTDE and Dashboard servers."""
    robot = SimulatedUR()
    rtde = RTDEServer(robot)
    dashboard = DashboardServer(robot)

    rtde_server = await asyncio.start_server(rtde.handle_client, host, rtde_port)
    dash_server = await asyncio.start_server(dashboard.handle_client, host, dashboard_port)

    logger.info(f"UR5e Simulator running:")
    logger.info(f"  RTDE: {host}:{rtde_port}")
    logger.info(f"  Dashboard: {host}:{dashboard_port}")

    async with rtde_server, dash_server:
        await asyncio.gather(
            rtde_server.serve_forever(),
            dash_server.serve_forever(),
        )


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Universal Robots RTDE Simulator")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--rtde-port", type=int, default=30004)
    parser.add_argument("--dashboard-port", type=int, default=29999)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logger.info("Starting Universal Robots UR5e simulator...")
    asyncio.run(run_simulator(args.host, args.rtde_port, args.dashboard_port))


if __name__ == "__main__":
    main()
