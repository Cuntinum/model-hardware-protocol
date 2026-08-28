"""KHP Driver — Modbus TCP/RTU.

Supports industrial automation equipment communicating via Modbus protocol.
Covers: PLCs, VFDs, power meters, temperature controllers, flow meters,
pressure transducers, HVAC controllers, motor drives, etc.

Requirements:
    pip install pymodbus
"""

from khp import Driver, readable, writable, procedure, safety, monitor
from khp.core import ConnectionType
from typing import List, Optional


class ModbusDevice(Driver):
    """Generic Modbus TCP/RTU device driver."""

    name = "Modbus Device"
    version = "1.0.0"
    device_type = "custom"
    description = "Industrial Modbus TCP/RTU device (PLCs, VFDs, sensors)"
    connection_type = ConnectionType.MODBUS

    def __init__(self, device_id: str = None, host: str = "192.168.1.1",
                 port: int = 502, unit_id: int = 1, protocol: str = "tcp",
                 serial_port: str = "/dev/ttyUSB0", baud_rate: int = 9600, **config):
        super().__init__(device_id=device_id, host=host, port=port, **config)
        self._host = host
        self._port = port
        self._unit_id = unit_id
        self._protocol = protocol
        self._serial_port = serial_port
        self._baud_rate = baud_rate
        self._client = None

    async def connect(self):
        from pymodbus.client import ModbusTcpClient, ModbusSerialClient

        if self._protocol == "tcp":
            self._client = ModbusTcpClient(self._host, port=self._port)
        else:
            self._client = ModbusSerialClient(
                self._serial_port,
                baudrate=self._baud_rate,
                method="rtu",
            )

        if self._client.connect():
            await super().connect()
        else:
            from khp.errors import DeviceOfflineError
            raise DeviceOfflineError(
                f"Cannot connect to Modbus device at {self._host}:{self._port}",
                device_id=self.device_id,
            )

    async def disconnect(self):
        if self._client:
            self._client.close()
        self._client = None
        await super().disconnect()

    @readable(type="array", description="Read holding registers from device")
    def holding_registers(self) -> list:
        start = self.config.get("register_start", 0)
        count = self.config.get("register_count", 10)
        result = self._client.read_holding_registers(start, count, slave=self._unit_id)
        if result.isError():
            return []
        return result.registers

    @readable(type="array", description="Read input registers (read-only process values)")
    def input_registers(self) -> list:
        start = self.config.get("input_start", 0)
        count = self.config.get("input_count", 10)
        result = self._client.read_input_registers(start, count, slave=self._unit_id)
        if result.isError():
            return []
        return result.registers

    @readable(type="array", description="Read coil status (digital outputs)")
    def coils(self) -> list:
        start = self.config.get("coil_start", 0)
        count = self.config.get("coil_count", 16)
        result = self._client.read_coils(start, count, slave=self._unit_id)
        if result.isError():
            return []
        return result.bits[:count]

    @readable(type="array", description="Read discrete inputs (digital inputs)")
    def discrete_inputs(self) -> list:
        start = self.config.get("discrete_start", 0)
        count = self.config.get("discrete_count", 16)
        result = self._client.read_discrete_inputs(start, count, slave=self._unit_id)
        if result.isError():
            return []
        return result.bits[:count]

    @writable(type="int", description="Write a single holding register")
    def write_register(self, value: int):
        address = self.config.get("write_address", 0)
        self._client.write_register(address, value, slave=self._unit_id)

    @writable(type="bool", description="Write a single coil (digital output)")
    def write_coil(self, value: bool):
        address = self.config.get("coil_address", 0)
        self._client.write_coil(address, value, slave=self._unit_id)

    @procedure(description="Read specific holding register(s)",
               estimated_duration_s=0.1)
    def read_registers(self, address: int = 0, count: int = 1) -> list:
        """Read one or more holding registers at a specific address."""
        result = self._client.read_holding_registers(address, count, slave=self._unit_id)
        if result.isError():
            return {"error": str(result)}
        return result.registers

    @procedure(description="Write multiple holding registers",
               estimated_duration_s=0.1)
    def write_registers(self, address: int = 0, values: list = None) -> dict:
        """Write multiple values to consecutive holding registers."""
        values = values or [0]
        result = self._client.write_registers(address, values, slave=self._unit_id)
        if result.isError():
            return {"error": str(result)}
        return {"address": address, "count": len(values), "success": True}

    @procedure(description="Write single coil at specific address",
               estimated_duration_s=0.1)
    def set_coil(self, address: int = 0, value: bool = True) -> dict:
        """Set a single coil (digital output) at a specific address."""
        result = self._client.write_coil(address, value, slave=self._unit_id)
        if result.isError():
            return {"error": str(result)}
        return {"address": address, "value": value, "success": True}

    @procedure(description="Scan for connected Modbus devices (unit IDs 1-247)",
               estimated_duration_s=30.0)
    def scan_devices(self, start_id: int = 1, end_id: int = 10) -> list:
        """Scan for responding Modbus devices on the bus."""
        found = []
        for uid in range(start_id, end_id + 1):
            try:
                result = self._client.read_holding_registers(0, 1, slave=uid)
                if not result.isError():
                    found.append({"unit_id": uid, "register_0": result.registers[0]})
            except Exception:
                continue
        return found


class ModbusTemperatureController(ModbusDevice):
    """Modbus PID temperature controller (Watlow, Omega, Eurotherm)."""

    name = "Modbus Temperature Controller"
    version = "1.0.0"
    device_type = "thermocycler"
    description = "PID temperature controller via Modbus (setpoint, PV, output)"

    def __init__(self, device_id: str = None, host: str = "192.168.1.1",
                 pv_register: int = 0, sp_register: int = 1,
                 output_register: int = 2, scale_factor: float = 10.0, **config):
        super().__init__(device_id=device_id, host=host, **config)
        self._pv_reg = pv_register
        self._sp_reg = sp_register
        self._out_reg = output_register
        self._scale = scale_factor

    @monitor(interval_ms=1000, alert_above=200.0, action="emergency_stop")
    @readable(type="float", description="Process variable (actual temperature)", unit="celsius")
    def temperature(self) -> float:
        result = self._client.read_input_registers(self._pv_reg, 1, slave=self._unit_id)
        if result.isError():
            return 0.0
        return result.registers[0] / self._scale

    @readable(type="float", description="Current setpoint", unit="celsius")
    def setpoint(self) -> float:
        result = self._client.read_holding_registers(self._sp_reg, 1, slave=self._unit_id)
        if result.isError():
            return 0.0
        return result.registers[0] / self._scale

    @readable(type="float", description="Controller output", unit="percent")
    def output_power(self) -> float:
        result = self._client.read_input_registers(self._out_reg, 1, slave=self._unit_id)
        if result.isError():
            return 0.0
        return result.registers[0] / 10.0

    @safety(min=0.0, max=300.0, reason="Maximum safe operating temperature")
    @writable(type="float", description="Set target temperature", unit="celsius")
    def target_temperature(self, value: float):
        raw = int(value * self._scale)
        self._client.write_register(self._sp_reg, raw, slave=self._unit_id)

    @procedure(description="Ramp temperature at a controlled rate",
               estimated_duration_s=600.0)
    def ramp_to(self, target: float, rate_per_min: float = 5.0) -> dict:
        """Ramp to target temperature at specified rate (°C/min)."""
        import time
        current = self.read("temperature")["value"]
        steps = abs(target - current) / rate_per_min
        step_size = (target - current) / max(steps * 60, 1)
        temp = current
        for _ in range(int(steps * 60)):
            temp += step_size
            self.write("target_temperature", temp)
            time.sleep(1.0)
        self.write("target_temperature", target)
        return {"target": target, "rate": rate_per_min}
