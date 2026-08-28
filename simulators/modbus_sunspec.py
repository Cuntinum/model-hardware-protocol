"""SunSpec Solar Inverter Modbus TCP Simulator.

Emulates a SunSpec compliant solar inverter with models 1 (Common),
103 (Three Phase Inverter), 120 (Nameplate), 160 (MPPT), and
124 (Storage). Responds to Modbus TCP read/write holding register
requests with realistic solar production data.

Usage:
    python -m simulators.modbus_sunspec [--host 0.0.0.0] [--port 502]
"""
from __future__ import annotations

import struct
import socket
import asyncio
import math
import time
import random
import logging

logger = logging.getLogger("sim.sunspec")

MODBUS_FC_READ_HOLDING = 3
MODBUS_FC_WRITE_SINGLE = 6
MODBUS_FC_WRITE_MULTIPLE = 16


class SunSpecInverterSim:
    """Simulated 10kW solar inverter with battery storage."""

    def __init__(self):
        self.manufacturer = "KHP Simulator Corp"
        self.model_name = "KHP-SIM-10K"
        self.serial_number = "SIM2024000001"
        self.version = "2.1.0"

        self.rated_power_w = 10000
        self.ac_power_w = 0.0
        self.ac_voltage_v = 240.0
        self.ac_current_a = 0.0
        self.frequency_hz = 60.0
        self.total_energy_wh = 1_500_000.0
        self.dc_voltage_v = 380.0
        self.dc_current_a = 0.0
        self.dc_power_w = 0.0
        self.temperature_c = 42.0
        self.soc_pct = 75
        self.power_limit_pct = 100
        self.operating_state = 4  # MPPT

        self._start_time = time.time()
        self._registers: dict[int, int] = {}
        self._build_register_map()

    def _build_register_map(self):
        """Populate the Modbus register map with SunSpec model layout."""
        base = 40000
        self._registers[base] = 0x5375      # 'Su'
        self._registers[base + 1] = 0x6E53  # 'nS'

        self._model_1_addr = base + 2
        self._registers[self._model_1_addr] = 1      # Model ID
        self._registers[self._model_1_addr + 1] = 66  # Length

        self._model_103_addr = self._model_1_addr + 2 + 66
        self._registers[self._model_103_addr] = 103  # Model ID
        self._registers[self._model_103_addr + 1] = 50  # Length

        self._model_120_addr = self._model_103_addr + 2 + 50
        self._registers[self._model_120_addr] = 120  # Model ID
        self._registers[self._model_120_addr + 1] = 26  # Length

        self._model_160_addr = self._model_120_addr + 2 + 26
        self._registers[self._model_160_addr] = 160  # Model ID
        self._registers[self._model_160_addr + 1] = 48  # Length

        self._model_124_addr = self._model_160_addr + 2 + 48
        self._registers[self._model_124_addr] = 124  # Model ID
        self._registers[self._model_124_addr + 1] = 24  # Length

        end_addr = self._model_124_addr + 2 + 24
        self._registers[end_addr] = 0xFFFF
        self._registers[end_addr + 1] = 0

    def _string_to_registers(self, s: str, length: int) -> list[int]:
        """Convert string to register values (2 chars per register)."""
        padded = s.ljust(length * 2, "\x00")
        regs = []
        for i in range(0, length * 2, 2):
            regs.append((ord(padded[i]) << 8) | ord(padded[i + 1]))
        return regs

    def _int16_signed(self, value: int) -> int:
        """Convert signed int to unsigned 16 bit register value."""
        if value < 0:
            return value + 65536
        return value & 0xFFFF

    def update(self):
        """Update simulated solar production based on time of day."""
        elapsed = time.time() - self._start_time
        hour_angle = (elapsed / 3600.0) * (2 * math.pi / 24.0)

        solar_factor = max(0, math.sin(hour_angle))
        cloud_factor = 0.8 + 0.2 * math.sin(elapsed * 0.01)
        irradiance = solar_factor * cloud_factor

        self.dc_power_w = self.rated_power_w * irradiance * (self.power_limit_pct / 100.0)
        self.dc_voltage_v = 300 + 100 * irradiance + random.gauss(0, 2)
        self.dc_current_a = self.dc_power_w / max(self.dc_voltage_v, 1)

        efficiency = 0.97 - 0.005 * (1 - irradiance)
        self.ac_power_w = self.dc_power_w * efficiency
        self.ac_current_a = self.ac_power_w / self.ac_voltage_v
        self.frequency_hz = 60.0 + random.gauss(0, 0.02)

        self.total_energy_wh += self.ac_power_w * 0.001

        self.temperature_c = 25 + 20 * irradiance + random.gauss(0, 0.5)

        if self.ac_power_w > 5000 and self.soc_pct < 100:
            self.soc_pct = min(100, self.soc_pct + random.uniform(0, 0.01))
        elif self.ac_power_w < 100 and self.soc_pct > 10:
            self.soc_pct = max(0, self.soc_pct - random.uniform(0, 0.005))

    def read_registers(self, address: int, count: int) -> list[int]:
        """Read holding registers at address."""
        self.update()
        results = []

        for i in range(count):
            addr = address + i
            if addr in self._registers:
                results.append(self._registers[addr])
            elif self._is_common_model(addr):
                results.append(self._read_common(addr))
            elif self._is_inverter_model(addr):
                results.append(self._read_inverter(addr))
            elif self._is_nameplate_model(addr):
                results.append(self._read_nameplate(addr))
            elif self._is_mppt_model(addr):
                results.append(self._read_mppt(addr))
            elif self._is_storage_model(addr):
                results.append(self._read_storage(addr))
            else:
                results.append(0)
        return results

    def write_register(self, address: int, value: int):
        """Write a single holding register."""
        if self._is_inverter_model(address):
            offset = address - (self._model_103_addr + 2)
            if offset == 48:
                self.power_limit_pct = min(100, max(0, value))
        self._registers[address] = value

    def _is_common_model(self, addr: int) -> bool:
        start = self._model_1_addr + 2
        return start <= addr < start + 66

    def _is_inverter_model(self, addr: int) -> bool:
        start = self._model_103_addr + 2
        return start <= addr < start + 50

    def _is_nameplate_model(self, addr: int) -> bool:
        start = self._model_120_addr + 2
        return start <= addr < start + 26

    def _is_mppt_model(self, addr: int) -> bool:
        start = self._model_160_addr + 2
        return start <= addr < start + 48

    def _is_storage_model(self, addr: int) -> bool:
        start = self._model_124_addr + 2
        return start <= addr < start + 24

    def _read_common(self, addr: int) -> int:
        offset = addr - (self._model_1_addr + 2)
        mfr = self._string_to_registers(self.manufacturer, 16)
        mdl = self._string_to_registers(self.model_name, 16)
        ver = self._string_to_registers(self.version, 8)
        sn = self._string_to_registers(self.serial_number, 16)

        all_regs = mfr + mdl + [0]*8 + ver + sn + [0, 0]
        if offset < len(all_regs):
            return all_regs[offset]
        return 0

    def _read_inverter(self, addr: int) -> int:
        offset = addr - (self._model_103_addr + 2)
        ac_a = int(self.ac_current_a * 10)
        a_sf = self._int16_signed(-1)
        ac_v = int(self.ac_voltage_v * 10)
        v_sf = self._int16_signed(-1)
        ac_w = int(self.ac_power_w)
        w_sf = self._int16_signed(0)
        hz = int(self.frequency_hz * 100)
        hz_sf = self._int16_signed(-2)
        wh_hi = (int(self.total_energy_wh) >> 16) & 0xFFFF
        wh_lo = int(self.total_energy_wh) & 0xFFFF
        wh_sf = self._int16_signed(0)
        state = self.operating_state

        regs = [
            ac_a, ac_a, ac_a, a_sf,         # 0-3: currents + SF
            ac_v, ac_v, ac_v, v_sf,          # 4-7: voltages + SF
            0, 0, 0, 0, 0,                   # 8-12: padding
            ac_w, w_sf,                      # 13-14: watts + SF
            hz, hz_sf,                       # 15-16: freq + SF
            wh_hi, wh_lo, wh_sf,            # 17-19: energy + SF
            state,                           # 20: operating state
        ]
        if offset < len(regs):
            return regs[offset]
        return 0

    def _read_nameplate(self, addr: int) -> int:
        offset = addr - (self._model_120_addr + 2)
        regs = [
            4,  # DER type (PV)
            0, 0,
            self.rated_power_w & 0xFFFF,  # WRtg
            (self.rated_power_w >> 16) & 0xFFFF,
            self._int16_signed(0),  # WRtg_SF
        ]
        if offset < len(regs):
            return regs[offset]
        return 0

    def _read_mppt(self, addr: int) -> int:
        offset = addr - (self._model_160_addr + 2)
        dc_a_sf = self._int16_signed(-2)
        dc_v_sf = self._int16_signed(-1)
        dc_w_sf = self._int16_signed(0)
        dc_wh_sf = self._int16_signed(0)

        regs = [
            dc_a_sf, dc_v_sf, dc_w_sf, dc_wh_sf,  # 0-3: scale factors
            0, 2,  # 4-5: events, n_modules
            0, 0,  # 6-7: padding
            # Module 1
            1, 0, 0,  # ID, event, status
            int(self.dc_current_a * 100),  # DC A (offset+11)
            int(self.dc_voltage_v * 10),   # DC V
            int(self.dc_power_w),          # DC W
            0, 0,  # DC Wh (high, low)
            0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
            # Module 2
            2, 0, 0,
            int(self.dc_current_a * 50),
            int(self.dc_voltage_v * 10),
            int(self.dc_power_w * 0.5),
            0, 0,
        ]
        if offset < len(regs):
            return regs[offset]
        return 0

    def _read_storage(self, addr: int) -> int:
        offset = addr - (self._model_124_addr + 2)
        charge_w = int(self.ac_power_w * 0.1) if self.soc_pct < 100 else 0
        regs = [
            0, 0, 0, 0,              # 0-3
            self.soc_pct,            # 4: SOC
            0, 0, 0,                 # 5-7
            charge_w & 0xFFFF,       # 8: charge power
            self._int16_signed(0),   # 9: W_SF
        ]
        if offset < len(regs):
            return regs[offset]
        return 0


async def handle_modbus_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                                inverter: SunSpecInverterSim):
    """Handle one Modbus TCP client connection."""
    addr = writer.get_extra_info("peername")
    logger.info(f"Modbus connection from {addr}")

    try:
        while True:
            header = await reader.readexactly(7)
            transaction_id = struct.unpack(">H", header[0:2])[0]
            protocol_id = struct.unpack(">H", header[2:4])[0]
            length = struct.unpack(">H", header[4:6])[0]
            unit_id = header[6]

            pdu = await reader.readexactly(length - 1)
            function_code = pdu[0]

            if function_code == MODBUS_FC_READ_HOLDING:
                start_addr = struct.unpack(">H", pdu[1:3])[0]
                reg_count = struct.unpack(">H", pdu[3:5])[0]
                values = inverter.read_registers(start_addr, reg_count)

                resp_pdu = struct.pack(">B", function_code)
                resp_pdu += struct.pack(">B", reg_count * 2)
                for val in values:
                    resp_pdu += struct.pack(">H", val & 0xFFFF)

            elif function_code == MODBUS_FC_WRITE_SINGLE:
                reg_addr = struct.unpack(">H", pdu[1:3])[0]
                reg_value = struct.unpack(">H", pdu[3:5])[0]
                inverter.write_register(reg_addr, reg_value)
                resp_pdu = pdu[:5]

            elif function_code == MODBUS_FC_WRITE_MULTIPLE:
                start_addr = struct.unpack(">H", pdu[1:3])[0]
                reg_count = struct.unpack(">H", pdu[3:5])[0]
                byte_count = pdu[5]
                for i in range(reg_count):
                    val = struct.unpack(">H", pdu[6 + i*2:8 + i*2])[0]
                    inverter.write_register(start_addr + i, val)
                resp_pdu = pdu[:5]

            else:
                resp_pdu = struct.pack(">BB", function_code | 0x80, 1)

            resp_header = struct.pack(">HHH", transaction_id, protocol_id, len(resp_pdu) + 1)
            resp_header += struct.pack(">B", unit_id)
            writer.write(resp_header + resp_pdu)
            await writer.drain()

    except (asyncio.IncompleteReadError, ConnectionResetError, asyncio.CancelledError):
        pass
    finally:
        writer.close()


async def run_simulator(host: str = "0.0.0.0", port: int = 5020):
    """Start the SunSpec Modbus TCP simulator."""
    inverter = SunSpecInverterSim()

    server = await asyncio.start_server(
        lambda r, w: handle_modbus_client(r, w, inverter),
        host, port,
    )

    logger.info(f"SunSpec Inverter Simulator running on {host}:{port}")
    logger.info(f"  Model: {inverter.model_name} ({inverter.rated_power_w}W)")
    logger.info(f"  Base address: 40000 (SunS signature)")

    async with server:
        await server.serve_forever()


def main():
    import argparse
    parser = argparse.ArgumentParser(description="SunSpec Solar Inverter Modbus Simulator")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=5020, help="Modbus TCP port (default 5020 to avoid needing root)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    asyncio.run(run_simulator(args.host, args.port))


if __name__ == "__main__":
    main()
