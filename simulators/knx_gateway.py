"""KNXnet/IP Gateway Simulator.

Emulates a KNXnet/IP tunneling gateway on UDP port 3671. Accepts tunneling
connections, responds to connection requests, and echoes back group value
write/read requests with simulated building data.

Usage:
    python -m simulators.knx_gateway [--host 0.0.0.0] [--port 3671]
"""
from __future__ import annotations

import struct
import asyncio
import random
import logging
from datetime import datetime, timezone

logger = logging.getLogger("sim.knx")

KNXNETIP_VERSION = 0x10
SEARCH_REQUEST = 0x0201
SEARCH_RESPONSE = 0x0202
CONNECT_REQUEST = 0x0205
CONNECT_RESPONSE = 0x0206
CONNECTIONSTATE_REQUEST = 0x0207
CONNECTIONSTATE_RESPONSE = 0x0208
DISCONNECT_REQUEST = 0x0209
DISCONNECT_RESPONSE = 0x020A
TUNNELLING_REQUEST = 0x0420
TUNNELLING_ACK = 0x0421

E_NO_ERROR = 0x00
E_CONNECTION_TYPE = 0x22


class KNXSimState:
    """Simulated KNX bus state with room data."""

    def __init__(self):
        self.group_values: dict[str, int] = {
            "1/1/1": 1,      # Living room light on
            "1/1/2": 180,    # Living room dimmer 70%
            "1/2/1": 0,      # Kitchen light off
            "2/1/1": 0,      # Blinds open
            "3/1/1": 1,      # HVAC comfort mode
            "4/1/1": 0,      # Temp placeholder (2 byte float)
            "5/1/1": 0,      # Energy placeholder
        }
        self.temperatures = {
            "4/1/1": 21.5,   # Living room
            "4/1/2": 22.0,   # Kitchen
            "4/1/3": 19.5,   # Bedroom
        }

    def update(self):
        for addr in self.temperatures:
            self.temperatures[addr] += random.gauss(0, 0.1)
            self.temperatures[addr] = max(15, min(30, self.temperatures[addr]))

    def get_value(self, group_address: str) -> bytes:
        if group_address in self.temperatures:
            temp = self.temperatures[group_address]
            encoded = self._encode_dpt9(temp)
            return encoded
        val = self.group_values.get(group_address, 0)
        if val <= 0x3F:
            return struct.pack(">B", val & 0x3F)
        return struct.pack(">B", val)

    def set_value(self, group_address: str, data: bytes):
        if len(data) == 1:
            self.group_values[group_address] = data[0]
        elif len(data) == 2:
            self.group_values[group_address] = struct.unpack(">H", data)[0]

    def _encode_dpt9(self, value: float) -> bytes:
        """Encode a float as KNX DPT 9.x (2 byte float)."""
        sign = 0
        if value < 0:
            sign = 1
            value = -value
        mantissa = int(value * 100)
        exponent = 0
        while mantissa > 2047:
            mantissa >>= 1
            exponent += 1
        if sign:
            mantissa = (~mantissa + 1) & 0x7FF
        raw = (sign << 15) | (exponent << 11) | mantissa
        return struct.pack(">H", raw)


class KNXGatewayProtocol(asyncio.DatagramProtocol):
    """KNXnet/IP UDP protocol handler."""

    def __init__(self, state: KNXSimState):
        self.state = state
        self.transport = None
        self._connections: dict[int, tuple] = {}
        self._next_channel = 1
        self._sequence: dict[int, int] = {}

    def connection_made(self, transport):
        self.transport = transport
        logger.info("KNX gateway ready for connections")

    def datagram_received(self, data: bytes, addr: tuple):
        if len(data) < 6:
            return

        header_length = data[0]
        version = data[1]
        service_type = struct.unpack(">H", data[2:4])[0]
        total_length = struct.unpack(">H", data[4:6])[0]

        if version != KNXNETIP_VERSION:
            return

        self.state.update()

        if service_type == SEARCH_REQUEST:
            self._handle_search(addr)
        elif service_type == CONNECT_REQUEST:
            self._handle_connect(data, addr)
        elif service_type == CONNECTIONSTATE_REQUEST:
            self._handle_connectionstate(data, addr)
        elif service_type == DISCONNECT_REQUEST:
            self._handle_disconnect(data, addr)
        elif service_type == TUNNELLING_REQUEST:
            self._handle_tunnelling(data, addr)

    def _send(self, data: bytes, addr: tuple):
        self.transport.sendto(data, addr)

    def _make_header(self, service_type: int, body_length: int) -> bytes:
        total = 6 + body_length
        return struct.pack(">BBHH", 6, KNXNETIP_VERSION, service_type, total)

    def _handle_search(self, addr: tuple):
        body = bytearray()
        body += struct.pack(">BB", 8, 1)  # HPAI UDP
        body += struct.pack(">4sH", b"\x00\x00\x00\x00", 3671)
        body += struct.pack(">BB", 54, 2)  # DIB device info
        body += struct.pack(">B", 2)  # KNX medium TP1
        body += struct.pack(">B", 0)  # device status
        body += struct.pack(">H", 0)  # KNX address
        body += struct.pack(">H", 0)  # project installation
        body += b"\x00" * 6  # serial
        body += struct.pack(">4s", b"\xE0\x00\x17\x0C")  # multicast
        body += b"\x00" * 6  # MAC
        name = b"KHP-SIM-KNX-GW" + b"\x00" * 16
        body += name[:30]

        resp = self._make_header(SEARCH_RESPONSE, len(body)) + bytes(body)
        self._send(resp, addr)

    def _handle_connect(self, data: bytes, addr: tuple):
        channel = self._next_channel
        self._next_channel += 1
        self._connections[channel] = addr
        self._sequence[channel] = 0

        body = struct.pack(">B", channel)
        body += struct.pack(">B", E_NO_ERROR)
        body += struct.pack(">BB", 8, 1)  # HPAI
        body += struct.pack(">4sH", b"\x00\x00\x00\x00", 3671)
        body += struct.pack(">BBH", 4, 4, 0)  # CRD tunnelling

        resp = self._make_header(CONNECT_RESPONSE, len(body)) + body
        self._send(resp, addr)
        logger.info(f"KNX connection {channel} from {addr}")

    def _handle_connectionstate(self, data: bytes, addr: tuple):
        channel = data[6] if len(data) > 6 else 0
        body = struct.pack(">BB", channel, E_NO_ERROR)
        resp = self._make_header(CONNECTIONSTATE_RESPONSE, len(body)) + body
        self._send(resp, addr)

    def _handle_disconnect(self, data: bytes, addr: tuple):
        channel = data[6] if len(data) > 6 else 0
        self._connections.pop(channel, None)
        self._sequence.pop(channel, None)
        body = struct.pack(">BB", channel, E_NO_ERROR)
        resp = self._make_header(DISCONNECT_RESPONSE, len(body)) + body
        self._send(resp, addr)
        logger.info(f"KNX connection {channel} disconnected")

    def _handle_tunnelling(self, data: bytes, addr: tuple):
        if len(data) < 10:
            return
        conn_header_len = data[6]
        channel = data[7]
        seq_counter = data[8]

        ack_body = struct.pack(">BBBB", 4, channel, seq_counter, E_NO_ERROR)
        ack = self._make_header(TUNNELLING_ACK, len(ack_body)) + ack_body
        self._send(ack, addr)

        cemi_offset = 6 + conn_header_len
        if cemi_offset < len(data):
            cemi = data[cemi_offset:]
            if len(cemi) >= 2:
                message_code = cemi[0]
                if message_code == 0x11:  # L_Data.req
                    logger.debug(f"Tunnelling request on channel {channel}, seq {seq_counter}")


async def run_simulator(host: str = "0.0.0.0", port: int = 3671):
    """Start the KNXnet/IP gateway simulator."""
    state = KNXSimState()
    loop = asyncio.get_event_loop()

    transport, protocol = await loop.create_datagram_endpoint(
        lambda: KNXGatewayProtocol(state),
        local_addr=(host, port),
    )

    logger.info(f"KNXnet/IP Gateway Simulator on {host}:{port}")
    logger.info(f"  Simulating: 3 rooms, lighting + HVAC + blinds")

    try:
        await asyncio.sleep(float("inf"))
    finally:
        transport.close()


def main():
    import argparse
    parser = argparse.ArgumentParser(description="KNXnet/IP Gateway Simulator")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=3671)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    asyncio.run(run_simulator(args.host, args.port))


if __name__ == "__main__":
    main()
