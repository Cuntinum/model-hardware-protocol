"""Art Net DMX Node Simulator.

Emulates an Art Net node that receives DMX512 data and responds to ArtPoll
with device information. Logs received DMX values for verification during
development and testing.

Usage:
    python -m simulators.artnet_node [--host 0.0.0.0] [--port 6454]
"""
from __future__ import annotations

import struct
import asyncio
import logging
from datetime import datetime, timezone

logger = logging.getLogger("sim.artnet")

ARTNET_HEADER = b"Art-Net\x00"
ARTNET_OPCODE_DMX = 0x5000
ARTNET_OPCODE_POLL = 0x2000
ARTNET_OPCODE_POLL_REPLY = 0x2100


class ArtNetNodeState:
    """Simulated DMX universe state."""

    def __init__(self, universes: int = 4):
        self._universes: dict[int, bytearray] = {}
        for i in range(universes):
            self._universes[i] = bytearray(512)
        self._frame_count = 0
        self._last_source: tuple | None = None

    def receive_dmx(self, universe: int, data: bytes):
        if universe not in self._universes:
            self._universes[universe] = bytearray(512)
        self._universes[universe][:len(data)] = data[:512]
        self._frame_count += 1

    @property
    def frame_count(self) -> int:
        return self._frame_count

    def get_universe(self, universe: int) -> bytearray:
        return self._universes.get(universe, bytearray(512))


class ArtNetNodeProtocol(asyncio.DatagramProtocol):
    """Art Net UDP protocol handler for the simulated node."""

    def __init__(self, state: ArtNetNodeState, node_name: str = "KHP-SIM-ARTNET"):
        self.state = state
        self.transport = None
        self._node_name = node_name
        self._node_ip = b"\xc0\xa8\x01\x64"  # 192.168.1.100

    def connection_made(self, transport):
        self.transport = transport
        logger.info("Art Net node ready")

    def datagram_received(self, data: bytes, addr: tuple):
        if len(data) < 12 or data[:8] != ARTNET_HEADER:
            return

        opcode = struct.unpack_from("<H", data, 8)[0]

        if opcode == ARTNET_OPCODE_DMX:
            self._handle_dmx(data, addr)
        elif opcode == ARTNET_OPCODE_POLL:
            self._handle_poll(data, addr)

    def _handle_dmx(self, data: bytes, addr: tuple):
        if len(data) < 18:
            return
        sequence = data[12]
        physical = data[13]
        universe = struct.unpack_from("<H", data, 14)[0]
        length = struct.unpack_from(">H", data, 16)[0]
        dmx_data = data[18:18 + length]

        self.state.receive_dmx(universe, dmx_data)
        self.state._last_source = addr

        if self.state.frame_count % 1000 == 0:
            active = sum(1 for v in dmx_data if v > 0)
            logger.debug(f"Universe {universe}: {active}/512 active channels (frame {self.state.frame_count})")

    def _handle_poll(self, data: bytes, addr: tuple):
        reply = bytearray(239)
        reply[:8] = ARTNET_HEADER
        struct.pack_into("<H", reply, 8, ARTNET_OPCODE_POLL_REPLY)

        reply[10:14] = self._node_ip
        struct.pack_into("<H", reply, 14, 0x1936)

        struct.pack_into(">H", reply, 16, 0x0001)  # version
        reply[18] = 0  # NetSwitch
        reply[19] = 0  # SubSwitch

        struct.pack_into(">H", reply, 20, 0x0000)  # OEM
        reply[22] = 0  # UBEA version
        reply[23] = 0xF0  # Status1

        short_name = self._node_name.encode()[:17]
        reply[26:26 + len(short_name)] = short_name

        long_name = f"{self._node_name} (KHP Simulator)".encode()[:63]
        reply[44:44 + len(long_name)] = long_name

        struct.pack_into(">H", reply, 172, 4)  # NumPorts

        reply[174] = 0x80  # port 1 output
        reply[175] = 0x80  # port 2 output
        reply[176] = 0x80  # port 3 output
        reply[177] = 0x80  # port 4 output

        self.transport.sendto(bytes(reply), addr)
        logger.debug(f"Sent ArtPollReply to {addr}")


async def run_simulator(host: str = "0.0.0.0", port: int = 6454):
    """Start the Art Net node simulator."""
    state = ArtNetNodeState(universes=4)
    loop = asyncio.get_event_loop()

    transport, protocol = await loop.create_datagram_endpoint(
        lambda: ArtNetNodeProtocol(state),
        local_addr=(host, port),
    )

    logger.info(f"Art Net Node Simulator on {host}:{port}")
    logger.info(f"  Universes: 0-3 (2048 channels)")
    logger.info(f"  Responds to ArtPoll with device info")

    try:
        while True:
            await asyncio.sleep(10)
            if state.frame_count > 0:
                logger.info(f"  Total frames received: {state.frame_count}")
    except asyncio.CancelledError:
        pass
    finally:
        transport.close()


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Art Net DMX Node Simulator")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=6454)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    asyncio.run(run_simulator(args.host, args.port))


if __name__ == "__main__":
    main()
