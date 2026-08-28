"""KHP Driver: CAN Bus (Controller Area Network).

Supports automotive ECUs, electric vehicles, industrial vehicles, battery
management systems, and any device communicating via CAN 2.0A/B or CAN FD.
Uses DBC files to decode/encode signals from raw CAN frames.

Requirements:
    pip install python-can cantools
"""

from khp import Driver, readable, writable, procedure, safety, monitor
from khp.core import ConnectionType
from typing import Dict, Optional, List
import time


class CANBusDevice(Driver):
    """CAN Bus device driver with DBC signal decoding.

    Connects via SocketCAN (Linux), PCAN (Windows/Linux), Vector (Windows),
    or virtual interfaces for testing. Decodes and encodes signals using
    industry standard DBC database files.
    """

    name = "CAN Bus Device"
    version = "1.0.0"
    device_type = "canbus"
    description = "CAN Bus device with DBC signal decoding for automotive and industrial use"
    connection_type = ConnectionType.SDK

    def __init__(self, device_id: str = None, interface: str = "socketcan",
                 channel: str = "can0", bitrate: int = 500000,
                 dbc_path: str = None, bus_type: str = "can",
                 fd: bool = False, **config):
        super().__init__(device_id=device_id, interface=interface, channel=channel, **config)
        self._interface = interface
        self._channel = channel
        self._bitrate = bitrate
        self._dbc_path = dbc_path
        self._bus_type = bus_type
        self._fd = fd
        self._bus = None
        self._db = None
        self._last_frames: Dict[int, dict] = {}
        self._message_rates: Dict[int, float] = {}
        self._bus_off = False

    async def connect(self):
        import can
        try:
            self._bus = can.Bus(
                interface=self._interface,
                channel=self._channel,
                bitrate=self._bitrate,
                fd=self._fd,
            )
        except Exception as e:
            from khp.errors import ConnectionFailedError
            raise ConnectionFailedError(
                f"Cannot open CAN interface {self._interface} on {self._channel}: {e}",
                device_id=self.device_id,
            )

        if self._dbc_path:
            import cantools
            self._db = cantools.database.load_file(self._dbc_path)

        await super().connect()

    async def disconnect(self):
        if self._bus:
            self._bus.shutdown()
            self._bus = None
        await super().disconnect()

    def _receive_latest(self, arb_id: int, timeout: float = 0.5) -> Optional[dict]:
        """Receive and decode the latest message with the given arbitration ID."""
        import can
        end_time = time.time() + timeout
        while time.time() < end_time:
            msg = self._bus.recv(timeout=0.1)
            if msg is None:
                continue
            self._last_frames[msg.arbitration_id] = {
                "data": msg.data,
                "timestamp": msg.timestamp,
                "is_error": msg.is_error_frame,
            }
            if msg.is_error_frame:
                self._bus_off = True
            if msg.arbitration_id == arb_id:
                if self._db:
                    try:
                        decoded = self._db.decode_message(arb_id, msg.data)
                        return decoded
                    except Exception:
                        return {"raw": list(msg.data)}
                return {"raw": list(msg.data)}
        return None

    @readable(type="float", description="Vehicle speed from CAN bus (km/h)", unit="km/h")
    def vehicle_speed(self) -> float:
        data = self._receive_latest(0x120)
        if data and "VehicleSpeed" in data:
            return float(data["VehicleSpeed"])
        return 0.0

    @readable(type="float", description="Engine RPM from CAN bus", unit="rpm")
    def engine_rpm(self) -> float:
        data = self._receive_latest(0x100)
        if data and "EngineRPM" in data:
            return float(data["EngineRPM"])
        return 0.0

    @readable(type="float", description="Battery state of charge", unit="percent")
    def battery_soc(self) -> float:
        data = self._receive_latest(0x180)
        if data and "BatterySOC" in data:
            return float(data["BatterySOC"])
        return 0.0

    @monitor(interval_ms=500, alert_above=85.0, action="emergency_stop")
    @readable(type="float", description="Motor temperature", unit="celsius")
    def motor_temperature(self) -> float:
        data = self._receive_latest(0x200)
        if data and "MotorTemp" in data:
            return float(data["MotorTemp"])
        return 0.0

    @readable(type="bool", description="Whether the CAN bus is in bus off state")
    def bus_off_status(self) -> bool:
        return self._bus_off

    @readable(type="object", description="Last received frames by arbitration ID")
    def recent_frames(self) -> dict:
        result = {}
        for arb_id, frame in self._last_frames.items():
            result[hex(arb_id)] = {
                "data": list(frame["data"]),
                "timestamp": frame["timestamp"],
            }
        return result

    @safety(min=0.0, max=100.0, reason="Throttle must stay within 0 to 100 percent", hard=True)
    @writable(type="float", description="Set throttle command (0 to 100 percent)", unit="percent")
    def throttle_command(self, value: float):
        self._send_signal(0x300, "ThrottleCmd", value)

    @safety(min=-45.0, max=45.0, reason="Steering angle limited to 45 degrees each direction", hard=True)
    @writable(type="float", description="Set steering angle command", unit="degrees")
    def steering_command(self, value: float):
        self._send_signal(0x301, "SteeringAngle", value)

    @safety(min=0.0, max=100.0, reason="Actuator position 0 to 100 percent of travel", hard=True)
    @writable(type="float", description="Set actuator position", unit="percent")
    def actuator_position(self, value: float):
        self._send_signal(0x310, "ActuatorPos", value)

    def _send_signal(self, arb_id: int, signal_name: str, value: float):
        """Encode a signal via DBC and send on the bus."""
        import can
        if self._bus_off:
            from khp.errors import SafetyBlockedError
            raise SafetyBlockedError(
                "CAN bus is in bus off state, cannot send",
                device_id=self.device_id,
                property_name=signal_name,
                attempted_value=value,
                limit=None,
            )
        if self._db:
            msg_def = self._db.get_message_by_frame_id(arb_id)
            data = msg_def.encode({signal_name: value})
        else:
            import struct
            data = struct.pack("<f", value) + b"\x00" * 4

        msg = can.Message(arbitration_id=arb_id, data=data, is_extended_id=False)
        self._bus.send(msg)

    @procedure(description="Send UDS diagnostic request and return response",
               estimated_duration_s=2.0)
    def uds_request(self, service_id: int = 0x22, sub_function: int = 0xF190) -> dict:
        """Send a Unified Diagnostic Services request (ISO 14229)."""
        import can
        import struct
        request_data = struct.pack(">BH", service_id, sub_function)
        padded = request_data.ljust(8, b"\x00")
        msg = can.Message(arbitration_id=0x7DF, data=padded, is_extended_id=False)
        self._bus.send(msg)

        response = self._bus.recv(timeout=2.0)
        if response and response.arbitration_id == 0x7E8:
            return {
                "service": hex(service_id),
                "sub_function": hex(sub_function),
                "response_data": list(response.data),
                "status": "completed",
            }
        return {"status": "timeout", "service": hex(service_id)}

    @procedure(description="Run ECU self test via diagnostic mode",
               estimated_duration_s=10.0, requires_confirmation=True)
    def self_test(self) -> dict:
        """Request ECU self test (UDS routine control 0x31)."""
        import can
        request = bytes([0x31, 0x01, 0xFF, 0x00, 0x00, 0x00, 0x00, 0x00])
        msg = can.Message(arbitration_id=0x7DF, data=request, is_extended_id=False)
        self._bus.send(msg)

        results = []
        end_time = time.time() + 10.0
        while time.time() < end_time:
            response = self._bus.recv(timeout=1.0)
            if response and response.arbitration_id == 0x7E8:
                if response.data[0] == 0x71:
                    results.append(list(response.data))
                    if response.data[2] == 0x02:
                        break
        return {"status": "completed", "responses": results}

    @procedure(description="Flash firmware to ECU (requires confirmation)",
               estimated_duration_s=120.0, requires_confirmation=True)
    def flash_firmware(self, firmware_path: str = "", block_size: int = 256) -> dict:
        """Upload firmware via UDS transfer protocol (0x34/0x36/0x37)."""
        if not firmware_path:
            return {"status": "failed", "reason": "No firmware path provided"}
        import can
        try:
            with open(firmware_path, "rb") as f:
                firmware = f.read()
        except FileNotFoundError:
            return {"status": "failed", "reason": "Firmware file not found"}

        request_download = bytes([0x34, 0x00, 0x44]) + len(firmware).to_bytes(4, "big") + b"\x00"
        msg = can.Message(arbitration_id=0x7DF, data=request_download, is_extended_id=False)
        self._bus.send(msg)

        response = self._bus.recv(timeout=5.0)
        if not response or response.data[0] != 0x74:
            return {"status": "failed", "reason": "ECU rejected download request"}

        blocks_sent = 0
        offset = 0
        while offset < len(firmware):
            block = firmware[offset:offset + block_size]
            seq = (blocks_sent + 1) & 0xFF
            transfer = bytes([0x36, seq]) + block[:6]
            msg = can.Message(arbitration_id=0x7DF, data=transfer, is_extended_id=False)
            self._bus.send(msg)
            offset += block_size
            blocks_sent += 1
            time.sleep(0.01)

        exit_msg = bytes([0x37, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])
        msg = can.Message(arbitration_id=0x7DF, data=exit_msg, is_extended_id=False)
        self._bus.send(msg)

        return {
            "status": "completed",
            "blocks_sent": blocks_sent,
            "total_bytes": len(firmware),
        }

    @procedure(description="Scan CAN bus for active arbitration IDs",
               estimated_duration_s=5.0)
    def scan_bus(self, duration_s: float = 3.0) -> dict:
        """Listen to the bus and report all active arbitration IDs."""
        seen = {}
        end_time = time.time() + duration_s
        while time.time() < end_time:
            msg = self._bus.recv(timeout=0.1)
            if msg:
                arb_hex = hex(msg.arbitration_id)
                if arb_hex not in seen:
                    seen[arb_hex] = {"count": 0, "dlc": msg.dlc}
                seen[arb_hex]["count"] += 1
        return {"duration_s": duration_s, "active_ids": seen, "total_unique": len(seen)}

    @procedure(description="Reset bus off state and reinitialize interface",
               estimated_duration_s=3.0)
    def reset_bus(self) -> dict:
        """Attempt to recover from bus off condition."""
        import can
        if self._bus:
            self._bus.shutdown()
        try:
            self._bus = can.Bus(
                interface=self._interface,
                channel=self._channel,
                bitrate=self._bitrate,
                fd=self._fd,
            )
            self._bus_off = False
            return {"status": "completed", "bus_off": False}
        except Exception as e:
            return {"status": "failed", "reason": str(e)}
