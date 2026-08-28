"""KHP Driver — SCPI/VISA Scientific Instruments.

Supports lab instruments that speak SCPI (Standard Commands for Programmable Instruments).
Covers: oscilloscopes, multimeters, signal generators, power supplies,
spectrum analyzers, network analyzers, source measure units (SMUs), etc.

SCPI is the lingua franca of test & measurement equipment.
Most Keysight, Tektronix, Rohde & Schwarz, Rigol instruments support it.

Requirements:
    pip install pyvisa pyvisa-py
"""

from khp import Driver, readable, writable, procedure, safety, monitor
from khp.core import ConnectionType
from typing import Optional, List


class SCPIDevice(Driver):
    """Generic SCPI/VISA instrument driver."""

    name = "SCPI Instrument"
    version = "1.0.0"
    device_type = "custom"
    description = "SCPI-compliant test & measurement instrument"
    connection_type = ConnectionType.USB

    def __init__(self, device_id: str = None,
                 resource_string: str = "TCPIP::192.168.1.1::INSTR",
                 timeout_ms: int = 5000, **config):
        super().__init__(device_id=device_id,
                         endpoint=resource_string, timeout_ms=timeout_ms, **config)
        self._resource_string = resource_string
        self._timeout_ms = timeout_ms
        self._instrument = None
        self._rm = None

    async def connect(self):
        import pyvisa
        self._rm = pyvisa.ResourceManager("@py")
        self._instrument = self._rm.open_resource(self._resource_string)
        self._instrument.timeout = self._timeout_ms
        idn = self._instrument.query("*IDN?").strip()
        self._tags["identity"] = idn
        await super().connect()

    async def disconnect(self):
        if self._instrument:
            self._instrument.close()
        if self._rm:
            self._rm.close()
        self._instrument = None
        self._rm = None
        await super().disconnect()

    def _query(self, command: str) -> str:
        """Send SCPI query and return response."""
        return self._instrument.query(command).strip()

    def _write(self, command: str):
        """Send SCPI command (no response expected)."""
        self._instrument.write(command)

    @readable(type="string", description="Instrument identity (*IDN?)")
    def identity(self) -> str:
        return self._query("*IDN?")

    @readable(type="string", description="Error queue (*ESR?)")
    def error_status(self) -> str:
        return self._query("SYST:ERR?")

    @readable(type="bool", description="Operation complete status")
    def operation_complete(self) -> bool:
        return self._query("*OPC?") == "1"

    @procedure(description="Send any SCPI query and get response",
               estimated_duration_s=1.0)
    def query(self, command: str) -> str:
        """Send a SCPI query command. Returns the response string."""
        return self._query(command)

    @procedure(description="Send a SCPI command (no response)",
               estimated_duration_s=0.5)
    def command(self, command: str) -> dict:
        """Send a SCPI command that doesn't return a response."""
        self._write(command)
        return {"sent": command}

    @procedure(description="Reset instrument to factory defaults (*RST)",
               requires_confirmation=True, estimated_duration_s=5.0)
    def reset(self):
        """Reset to factory defaults."""
        self._write("*RST")
        self._write("*CLS")
        return {"reset": True}

    @procedure(description="Self-test (*TST?)", estimated_duration_s=30.0)
    def self_test(self) -> dict:
        """Run instrument self-test."""
        result = self._query("*TST?")
        return {"passed": result == "0", "result": result}

    @procedure(description="List all available VISA resources on the system",
               estimated_duration_s=5.0)
    def list_resources(self) -> list:
        """Discover all VISA resources (instruments) on the system."""
        import pyvisa
        rm = pyvisa.ResourceManager("@py")
        resources = list(rm.list_resources())
        rm.close()
        return resources


class Oscilloscope(SCPIDevice):
    """SCPI Oscilloscope (Keysight, Tektronix, Rigol, R&S)."""

    name = "SCPI Oscilloscope"
    version = "1.0.0"
    device_type = "custom"
    description = "Digital oscilloscope via SCPI (waveform capture, measurements)"

    @readable(type="float", description="Measured frequency on active channel", unit="Hz")
    def frequency(self) -> float:
        try:
            return float(self._query("MEAS:FREQ?"))
        except ValueError:
            return 0.0

    @readable(type="float", description="Peak-to-peak voltage on active channel", unit="volts")
    def vpp(self) -> float:
        try:
            return float(self._query("MEAS:VPP?"))
        except ValueError:
            return 0.0

    @readable(type="float", description="RMS voltage", unit="volts")
    def vrms(self) -> float:
        try:
            return float(self._query("MEAS:VRMS?"))
        except ValueError:
            return 0.0

    @readable(type="float", description="Current timebase setting", unit="seconds")
    def timebase(self) -> float:
        try:
            return float(self._query("TIM:RANG?"))
        except ValueError:
            return 0.0

    @writable(type="float", description="Set vertical scale", unit="volts/div")
    def vertical_scale(self, value: float):
        channel = self.config.get("channel", 1)
        self._write(f"CHAN{channel}:SCAL {value}")

    @writable(type="float", description="Set timebase", unit="seconds/div")
    def time_scale(self, value: float):
        self._write(f"TIM:SCAL {value}")

    @writable(type="float", description="Set trigger level", unit="volts")
    def trigger_level(self, value: float):
        self._write(f"TRIG:LEV {value}")

    @procedure(description="Single acquisition", estimated_duration_s=2.0)
    def single_shot(self) -> dict:
        """Perform a single-shot acquisition."""
        self._write(":SING")
        import time
        time.sleep(1.0)
        return {"status": "acquired"}

    @procedure(description="Capture waveform data from a channel",
               estimated_duration_s=5.0)
    def capture_waveform(self, channel: int = 1, points: int = 1000) -> dict:
        """Download waveform data from specified channel."""
        self._write(f":WAV:SOUR CHAN{channel}")
        self._write(":WAV:MODE NORM")
        self._write(f":WAV:POIN {points}")
        self._write(":WAV:FORM ASC")
        data_str = self._query(":WAV:DATA?")
        try:
            values = [float(x) for x in data_str.split(",") if x.strip()]
        except ValueError:
            values = []
        return {"channel": channel, "points": len(values), "data": values[:100]}

    @procedure(description="Auto-scale all channels", estimated_duration_s=5.0)
    def autoscale(self) -> dict:
        """Autoscale the oscilloscope."""
        self._write(":AUT")
        return {"autoscaled": True}


class Multimeter(SCPIDevice):
    """SCPI Digital Multimeter (Keysight 34401A, Fluke, Keithley)."""

    name = "SCPI Multimeter"
    version = "1.0.0"
    device_type = "sensor"
    description = "Digital multimeter via SCPI (voltage, current, resistance)"

    @readable(type="float", description="DC voltage measurement", unit="volts")
    def dc_voltage(self) -> float:
        try:
            return float(self._query("MEAS:VOLT:DC?"))
        except ValueError:
            return 0.0

    @readable(type="float", description="AC voltage measurement", unit="volts")
    def ac_voltage(self) -> float:
        try:
            return float(self._query("MEAS:VOLT:AC?"))
        except ValueError:
            return 0.0

    @readable(type="float", description="DC current measurement", unit="amps")
    def dc_current(self) -> float:
        try:
            return float(self._query("MEAS:CURR:DC?"))
        except ValueError:
            return 0.0

    @readable(type="float", description="Resistance measurement", unit="ohms")
    def resistance(self) -> float:
        try:
            return float(self._query("MEAS:RES?"))
        except ValueError:
            return 0.0

    @readable(type="float", description="Temperature (if probe connected)", unit="celsius")
    def temperature_probe(self) -> float:
        try:
            return float(self._query("MEAS:TEMP?"))
        except ValueError:
            return 0.0

    @procedure(description="Set measurement function",
               estimated_duration_s=0.5)
    def set_function(self, function: str = "VOLT:DC") -> dict:
        """Set measurement function: VOLT:DC, VOLT:AC, CURR:DC, CURR:AC, RES, FREQ, TEMP."""
        self._write(f"CONF:{function}")
        return {"function": function}


class PowerSupply(SCPIDevice):
    """SCPI Power Supply (Keysight E36xx, Rigol DP832, R&S)."""

    name = "SCPI Power Supply"
    version = "1.0.0"
    device_type = "custom"
    description = "Programmable power supply via SCPI"

    @readable(type="float", description="Measured output voltage", unit="volts")
    def voltage(self) -> float:
        try:
            return float(self._query("MEAS:VOLT?"))
        except ValueError:
            return 0.0

    @readable(type="float", description="Measured output current", unit="amps")
    def current(self) -> float:
        try:
            return float(self._query("MEAS:CURR?"))
        except ValueError:
            return 0.0

    @readable(type="float", description="Output power", unit="watts")
    def power_output(self) -> float:
        v = self.voltage()
        i = self.current()
        return round(v * i, 4)

    @readable(type="bool", description="Whether output is enabled")
    def output_enabled(self) -> bool:
        return self._query("OUTP?") == "1"

    @safety(min=0.0, max=60.0, reason="Maximum safe voltage for connected DUT")
    @writable(type="float", description="Set output voltage", unit="volts")
    def voltage_setpoint(self, value: float):
        self._write(f"VOLT {value}")

    @safety(min=0.0, max=5.0, reason="Maximum current to prevent damage")
    @writable(type="float", description="Set current limit", unit="amps")
    def current_limit(self, value: float):
        self._write(f"CURR {value}")

    @writable(type="bool", description="Enable/disable output")
    def output(self, value: bool):
        self._write(f"OUTP {'ON' if value else 'OFF'}")

    @safety(require_confirmation=True)
    @procedure(description="Enable output", estimated_duration_s=0.5)
    def enable_output(self) -> dict:
        """Turn on the power supply output."""
        self._write("OUTP ON")
        return {"output": "enabled"}

    @procedure(description="Disable output", estimated_duration_s=0.5)
    def disable_output(self) -> dict:
        """Turn off the power supply output."""
        self._write("OUTP OFF")
        return {"output": "disabled"}
