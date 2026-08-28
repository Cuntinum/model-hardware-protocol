"""KHP Driver — Docker Device Simulator.

Provides simulated hardware for testing KHP integrations without physical devices.
Spins up a Docker container that emulates a device's behavior.

Use cases:
- CI/CD testing of KHP drivers
- Development without physical hardware
- Demo environments
- Training AI agents on hardware interaction

Requirements:
    pip install docker
"""

from khp import Driver, readable, writable, procedure, safety
from khp.core import ConnectionType
from typing import Optional, Dict
import time
import random
import math


class SimulatedThermocycler(Driver):
    """Simulated thermocycler — no hardware required, models thermal behavior."""

    name = "Simulated Thermocycler"
    version = "1.0.0"
    device_type = "thermocycler"
    description = "Virtual thermocycler for testing (models realistic thermal dynamics)"
    connection_type = ConnectionType.REST

    def __init__(self, device_id: str = None, **config):
        super().__init__(device_id=device_id or "sim_thermocycler_1", **config)
        self._current_temp = 22.0  # Room temperature
        self._target_temp = 22.0
        self._ramp_rate = 3.0  # °C/s heating, 2.0 cooling
        self._last_update = time.time()
        self._running_protocol = False
        self._protocol_step = 0
        self._lid_temp = 105.0

    def _update_temperature(self):
        """Simulate thermal dynamics."""
        now = time.time()
        dt = now - self._last_update
        self._last_update = now

        diff = self._target_temp - self._current_temp
        if abs(diff) < 0.1:
            self._current_temp = self._target_temp
        else:
            rate = self._ramp_rate if diff > 0 else -2.0
            change = rate * dt
            if abs(change) > abs(diff):
                self._current_temp = self._target_temp
            else:
                self._current_temp += change
        self._current_temp += random.gauss(0, 0.05)

    @readable(type="float", description="Current block temperature", unit="celsius")
    def block_temperature(self) -> float:
        self._update_temperature()
        return round(self._current_temp, 2)

    @readable(type="float", description="Lid temperature", unit="celsius")
    def lid_temperature(self) -> float:
        return self._lid_temp

    @readable(type="float", description="Target temperature setpoint", unit="celsius")
    def target(self) -> float:
        return self._target_temp

    @readable(type="bool", description="Whether a protocol is currently running")
    def protocol_running(self) -> bool:
        return self._running_protocol

    @readable(type="int", description="Current protocol step number")
    def protocol_step(self) -> int:
        return self._protocol_step

    @safety(min=4.0, max=100.0, reason="Safe operating range for thermocycler block")
    @writable(type="float", description="Set target block temperature", unit="celsius")
    def temperature_setpoint(self, value: float):
        self._target_temp = value

    @safety(min=30.0, max=110.0, reason="Lid heater safe range")
    @writable(type="float", description="Set lid temperature", unit="celsius")
    def lid_setpoint(self, value: float):
        self._lid_temp = value

    @procedure(description="Run a PCR protocol (simulated)",
               estimated_duration_s=60.0)
    def run_protocol(self, denature_temp: float = 95.0, anneal_temp: float = 55.0,
                     extend_temp: float = 72.0, cycles: int = 30,
                     denature_s: int = 30, anneal_s: int = 30,
                     extend_s: int = 60) -> dict:
        """Run a simulated PCR thermal cycling protocol."""
        self._running_protocol = True
        total_time = cycles * (denature_s + anneal_s + extend_s)
        for cycle in range(min(cycles, 3)):  # Simulate first 3 cycles only
            self._protocol_step = cycle + 1
            self._target_temp = denature_temp
            time.sleep(0.5)
            self._target_temp = anneal_temp
            time.sleep(0.5)
            self._target_temp = extend_temp
            time.sleep(0.5)
        self._running_protocol = False
        self._target_temp = 4.0
        return {"cycles_completed": cycles, "total_time_s": total_time, "simulated": True}

    @procedure(description="Hold at a specific temperature",
               estimated_duration_s=10.0)
    def hold(self, temperature: float = 4.0, duration_s: int = 10) -> dict:
        """Hold at a temperature for specified duration."""
        self._target_temp = temperature
        time.sleep(min(duration_s, 5))  # Cap simulation time
        return {"held_at": temperature, "duration_s": duration_s}


class SimulatedLiquidHandler(Driver):
    """Simulated liquid handler — models pipetting operations."""

    name = "Simulated Liquid Handler"
    version = "1.0.0"
    device_type = "liquid_handler"
    description = "Virtual liquid handler for testing (8-channel, 96-well plate)"
    connection_type = ConnectionType.REST

    def __init__(self, device_id: str = None, **config):
        super().__init__(device_id=device_id or "sim_pipette_1", **config)
        self._tips_attached = [False] * 8
        self._volume_loaded = [0.0] * 8
        self._position = {"x": 0, "y": 0, "z": 100}
        self._plate = {f"{chr(65+r)}{c+1}": 0.0
                       for r in range(8) for c in range(12)}

    @readable(type="array", description="Which channels have tips (8 bools)")
    def tip_status(self) -> list:
        return self._tips_attached

    @readable(type="array", description="Volume loaded per channel (uL)")
    def volumes(self) -> list:
        return [round(v, 1) for v in self._volume_loaded]

    @readable(type="object", description="Current arm position (x, y, z)")
    def position(self) -> dict:
        return self._position

    @readable(type="object", description="Plate well contents (volume in uL)")
    def plate_contents(self) -> dict:
        return {k: round(v, 1) for k, v in self._plate.items() if v > 0}

    @safety(min=0.5, max=1000.0, reason="Pipette volume range 0.5-1000 uL")
    @writable(type="float", description="Volume to aspirate/dispense", unit="uL")
    def volume_setting(self, value: float):
        pass  # Just validates, used by procedures

    @procedure(description="Pick up tips from tip rack",
               preconditions=[], estimated_duration_s=2.0)
    def pick_up_tips(self, channels: list = None) -> dict:
        """Pick up tips on specified channels (default: all 8)."""
        channels = channels or list(range(8))
        for ch in channels:
            if 0 <= ch < 8:
                self._tips_attached[ch] = True
        return {"tips_attached": self._tips_attached}

    @procedure(description="Drop tips into waste",
               estimated_duration_s=2.0)
    def drop_tips(self) -> dict:
        """Drop all tips."""
        self._tips_attached = [False] * 8
        self._volume_loaded = [0.0] * 8
        return {"tips_attached": self._tips_attached}

    @procedure(description="Aspirate liquid from wells",
               preconditions=["tip_attached"], estimated_duration_s=3.0)
    def aspirate(self, volume_ul: float = 100.0, well: str = "A1",
                 speed: str = "normal") -> dict:
        """Aspirate liquid from a well."""
        if not any(self._tips_attached):
            from khp.errors import PreconditionFailedError
            raise PreconditionFailedError("No tips attached", device_id=self.device_id)
        for i, attached in enumerate(self._tips_attached):
            if attached:
                self._volume_loaded[i] = volume_ul
        self._plate[well] = max(0, self._plate.get(well, 0) - volume_ul)
        return {"aspirated": volume_ul, "from_well": well, "speed": speed}

    @procedure(description="Dispense liquid into wells",
               preconditions=["tip_attached"], estimated_duration_s=3.0)
    def dispense(self, volume_ul: float = None, well: str = "A1",
                 speed: str = "normal", blowout: bool = False) -> dict:
        """Dispense liquid into a well."""
        if not any(self._tips_attached):
            from khp.errors import PreconditionFailedError
            raise PreconditionFailedError("No tips attached", device_id=self.device_id)
        dispensed = 0.0
        for i, attached in enumerate(self._tips_attached):
            if attached and self._volume_loaded[i] > 0:
                vol = volume_ul if volume_ul else self._volume_loaded[i]
                actual = min(vol, self._volume_loaded[i])
                self._volume_loaded[i] -= actual
                dispensed = actual
        self._plate[well] = self._plate.get(well, 0) + dispensed
        return {"dispensed": dispensed, "to_well": well, "speed": speed}

    @procedure(description="Move arm to a specific well position",
               estimated_duration_s=1.0)
    def move_to(self, well: str = "A1") -> dict:
        """Move the arm to position above a well."""
        col = int(well[1:]) - 1
        row = ord(well[0]) - 65
        self._position = {"x": col * 9, "y": row * 9, "z": 50}
        return {"moved_to": well, "position": self._position}

    def _check_tip_attached(self) -> bool:
        """Precondition check: at least one tip attached."""
        return any(self._tips_attached)


class SimulatedRoboticArm(Driver):
    """Simulated 6-axis robotic arm for testing."""

    name = "Simulated Robotic Arm"
    version = "1.0.0"
    device_type = "robotic_arm"
    description = "Virtual 6-axis robotic arm (pick-and-place, plate transfer)"
    connection_type = ConnectionType.REST

    def __init__(self, device_id: str = None, **config):
        super().__init__(device_id=device_id or "sim_robot_arm_1", **config)
        self._joints = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        self._gripper_open = True
        self._holding = None
        self._position = {"x": 0, "y": 0, "z": 500}
        self._speed = 0.5

    @readable(type="array", description="Joint angles in degrees (6 joints)")
    def joint_angles(self) -> list:
        return [round(j, 1) for j in self._joints]

    @readable(type="object", description="End-effector XYZ position (mm)")
    def end_effector_position(self) -> dict:
        return self._position

    @readable(type="bool", description="Whether gripper is open")
    def gripper_open(self) -> bool:
        return self._gripper_open

    @readable(type="string", description="What object the gripper is holding")
    def holding(self) -> str:
        return self._holding or "nothing"

    @safety(min=0.01, max=1.0, reason="Speed factor 0.01-1.0 (safety limit)")
    @writable(type="float", description="Movement speed factor (0.01=slow, 1.0=max)")
    def speed(self, value: float):
        self._speed = value

    @procedure(description="Move to XYZ position",
               estimated_duration_s=3.0)
    def move_to_position(self, x: float = 0, y: float = 0, z: float = 500,
                         speed: float = None) -> dict:
        """Move end-effector to absolute XYZ position (mm)."""
        self._position = {"x": x, "y": y, "z": z}
        move_time = 1.0 / (speed or self._speed)
        time.sleep(min(move_time, 2.0))
        return {"position": self._position, "duration_s": move_time}

    @procedure(description="Open gripper", estimated_duration_s=1.0)
    def open_gripper(self) -> dict:
        """Open the gripper (release object)."""
        released = self._holding
        self._gripper_open = True
        self._holding = None
        return {"gripper": "open", "released": released}

    @procedure(description="Close gripper", estimated_duration_s=1.0)
    def close_gripper(self, object_name: str = None) -> dict:
        """Close the gripper (grab object)."""
        self._gripper_open = False
        if object_name:
            self._holding = object_name
        return {"gripper": "closed", "holding": self._holding}

    @procedure(description="Pick up an object at position, move to target, place",
               estimated_duration_s=10.0)
    def pick_and_place(self, pick_x: float = 0, pick_y: float = 0, pick_z: float = 10,
                       place_x: float = 200, place_y: float = 0, place_z: float = 10,
                       object_name: str = "plate") -> dict:
        """Full pick-and-place sequence."""
        self._position = {"x": pick_x, "y": pick_y, "z": pick_z + 50}
        time.sleep(0.5)
        self._position["z"] = pick_z
        time.sleep(0.5)
        self._gripper_open = False
        self._holding = object_name
        time.sleep(0.3)
        self._position["z"] = pick_z + 50
        time.sleep(0.5)
        self._position = {"x": place_x, "y": place_y, "z": place_z + 50}
        time.sleep(0.5)
        self._position["z"] = place_z
        time.sleep(0.5)
        self._gripper_open = True
        self._holding = None
        self._position["z"] = place_z + 50
        return {"picked": object_name, "from": (pick_x, pick_y), "to": (place_x, place_y)}

    @safety(require_confirmation=True)
    @procedure(description="Home all axes (move to zero position)",
               estimated_duration_s=10.0)
    def home(self) -> dict:
        """Home all axes to zero position."""
        self._joints = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        self._position = {"x": 0, "y": 0, "z": 500}
        return {"homed": True}
