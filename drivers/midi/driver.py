"""KHP Driver: MIDI (Musical Instrument Digital Interface).

Connects to MIDI devices: synthesizers, drum machines, controllers, lighting
desks, stage equipment, digital audio workstations, and any MIDI compliant
hardware. Supports MIDI 1.0 and prepares for MIDI 2.0 (UMP).

Handles: note on/off, control change (CC), program change, pitch bend,
system exclusive (SysEx), clock/transport, NRPN/RPN, aftertouch,
and bulk parameter dumps.

Covers: Synthesizers (Moog, Roland, Korg, Sequential, Novation), drum
machines (Elektron, Roland TR, Arturia), controllers (Akai, Novation
Launchpad, Ableton Push), stage lighting via MIDI Show Control (MSC),
DAW control surfaces, and any USB or DIN MIDI device.

Requirements:
    pip install mido python-rtmidi
"""
from __future__ import annotations

import time
import threading
from typing import Any

from khp import Driver, readable, writable, procedure, safety, monitor
from khp.core import ConnectionType


NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

CC_NAMES = {
    0: "bank_select_msb",
    1: "modulation",
    2: "breath_controller",
    4: "foot_controller",
    5: "portamento_time",
    6: "data_entry_msb",
    7: "volume",
    8: "balance",
    10: "pan",
    11: "expression",
    32: "bank_select_lsb",
    38: "data_entry_lsb",
    64: "sustain_pedal",
    65: "portamento",
    66: "sostenuto",
    67: "soft_pedal",
    68: "legato",
    69: "hold_2",
    71: "resonance",
    72: "release_time",
    73: "attack_time",
    74: "cutoff_frequency",
    91: "reverb_depth",
    93: "chorus_depth",
    98: "nrpn_lsb",
    99: "nrpn_msb",
    100: "rpn_lsb",
    101: "rpn_msb",
    120: "all_sound_off",
    121: "reset_all_controllers",
    123: "all_notes_off",
}

GM_PROGRAMS = {
    0: "Acoustic Grand Piano",
    4: "Electric Piano 1",
    16: "Drawbar Organ",
    24: "Nylon Guitar",
    25: "Steel Guitar",
    30: "Overdriven Guitar",
    33: "Electric Bass (finger)",
    40: "Violin",
    48: "String Ensemble 1",
    56: "Trumpet",
    64: "Soprano Sax",
    73: "Flute",
    80: "Square Lead",
    88: "Pad 1 (new age)",
}


class MIDIDevice(Driver):
    """MIDI instrument/controller driver via rtmidi."""

    name = "MIDI Controller"
    version = "1.0.0"
    device_type = "midi_instrument"
    description = "MIDI driver for synthesizers, controllers, and instruments"
    connection_type = ConnectionType.USB

    def __init__(self, device_id: str | None = None, port_name: str | None = None,
                 channel: int = 0, virtual: bool = False,
                 clock_bpm: float = 120.0, **config):
        super().__init__(device_id=device_id, **config)
        self._port_name = port_name
        self._channel = min(max(channel, 0), 15)
        self._virtual = virtual
        self._clock_bpm = clock_bpm
        self._input_port = None
        self._output_port = None
        self._lock = threading.Lock()
        self._listener_thread: threading.Thread | None = None
        self._running = False

        self._active_notes: dict[int, dict] = {}
        self._cc_values: dict[int, int] = {}
        self._program: int = 0
        self._pitch_bend: int = 8192
        self._channel_pressure: int = 0
        self._received_messages: list[dict] = []
        self._clock_running = False
        self._song_position = 0
        self._message_count = 0
        self._last_activity_time = 0.0

    async def connect(self):
        """Open MIDI input and output ports."""
        try:
            import mido
            import mido.backends.rtmidi  # noqa: F401

            if self._virtual:
                self._output_port = mido.open_output(
                    self._port_name or "KHP Virtual MIDI", virtual=True
                )
                self._input_port = mido.open_input(
                    self._port_name or "KHP Virtual MIDI", virtual=True
                )
            else:
                available_out = mido.get_output_names()
                available_in = mido.get_input_names()

                out_name = self._port_name or (available_out[0] if available_out else None)
                in_name = self._port_name or (available_in[0] if available_in else None)

                if out_name:
                    self._output_port = mido.open_output(out_name)
                if in_name:
                    self._input_port = mido.open_input(in_name)

            self._running = True
            if self._input_port:
                self._listener_thread = threading.Thread(
                    target=self._listen_loop, daemon=True
                )
                self._listener_thread.start()

            await super().connect()

        except ImportError:
            from khp.errors import DriverLoadError
            raise DriverLoadError(
                "mido/rtmidi not installed. Install with: pip install mido python-rtmidi",
                device_id=self.device_id,
            )
        except Exception as e:
            from khp.errors import ConnectionFailedError
            raise ConnectionFailedError(
                f"MIDI connection failed: {e}",
                device_id=self.device_id,
            )

    async def disconnect(self):
        """Close MIDI ports and stop listener."""
        self._running = False
        if self._listener_thread:
            self._listener_thread.join(timeout=2.0)
            self._listener_thread = None
        if self._input_port:
            self._input_port.close()
            self._input_port = None
        if self._output_port:
            self._output_port.close()
            self._output_port = None
        await super().disconnect()

    def _listen_loop(self):
        """Background thread to receive MIDI messages."""
        while self._running and self._input_port:
            try:
                for msg in self._input_port.iter_pending():
                    self._process_incoming(msg)
                time.sleep(0.001)
            except Exception:
                break

    def _process_incoming(self, msg):
        """Process a received MIDI message."""
        self._message_count += 1
        self._last_activity_time = time.time()

        record = {
            "type": msg.type,
            "channel": getattr(msg, "channel", None),
            "timestamp": time.time(),
        }

        if msg.type == "note_on":
            record["note"] = msg.note
            record["velocity"] = msg.velocity
            record["name"] = self._note_name(msg.note)
            if msg.velocity > 0:
                self._active_notes[msg.note] = record
            else:
                self._active_notes.pop(msg.note, None)

        elif msg.type == "note_off":
            record["note"] = msg.note
            record["velocity"] = msg.velocity
            self._active_notes.pop(msg.note, None)

        elif msg.type == "control_change":
            record["control"] = msg.control
            record["value"] = msg.value
            record["name"] = CC_NAMES.get(msg.control, f"cc_{msg.control}")
            self._cc_values[msg.control] = msg.value

        elif msg.type == "program_change":
            record["program"] = msg.program
            self._program = msg.program

        elif msg.type == "pitchwheel":
            record["pitch"] = msg.pitch
            self._pitch_bend = msg.pitch + 8192

        elif msg.type == "clock":
            pass
        elif msg.type == "start":
            self._clock_running = True
        elif msg.type == "stop":
            self._clock_running = False
        elif msg.type == "songpos":
            self._song_position = msg.pos

        self._received_messages.append(record)
        if len(self._received_messages) > 1000:
            self._received_messages = self._received_messages[-500:]

    def _note_name(self, note: int) -> str:
        """Convert MIDI note number to name (e.g. 60 -> C4)."""
        octave = (note // 12) - 1
        name = NOTE_NAMES[note % 12]
        return f"{name}{octave}"

    def _send(self, msg):
        """Thread safe message send."""
        with self._lock:
            if self._output_port:
                self._output_port.send(msg)
                self._message_count += 1
                self._last_activity_time = time.time()

    @readable(type="dict", description="Currently held notes with velocity and name")
    def active_notes(self) -> dict:
        return {
            "notes": self._active_notes,
            "count": len(self._active_notes),
            "polyphony": len(self._active_notes),
        }

    @readable(type="dict", description="Current CC (control change) values for all controllers")
    def cc_state(self) -> dict:
        labeled = {}
        for cc, val in self._cc_values.items():
            labeled[CC_NAMES.get(cc, f"cc_{cc}")] = val
        return labeled

    @readable(type="int", description="Current program/patch number", unit="program")
    def current_program(self) -> int:
        return self._program

    @readable(type="int", description="Current pitch bend value (0=min, 8192=center, 16383=max)")
    def pitch_bend_value(self) -> int:
        return self._pitch_bend

    @readable(type="bool", description="Whether MIDI clock transport is running")
    def transport_running(self) -> bool:
        return self._clock_running

    @readable(type="int", description="Total MIDI messages sent and received", unit="count")
    def message_count(self) -> int:
        return self._message_count

    @readable(type="list", description="Recent received MIDI messages (last 50)")
    def recent_messages(self) -> list:
        return self._received_messages[-50:]

    @readable(type="int", description="Active MIDI channel (0 indexed)", unit="channel")
    def active_channel(self) -> int:
        return self._channel

    @safety(min=0, max=127, reason="MIDI note must be 0 to 127", hard=True)
    @writable(type="dict", description="Send note on (note: 0 to 127, velocity: 0 to 127)")
    def note_on(self, config: dict):
        """Send Note On. Config: {note: int, velocity: int}."""
        import mido
        note = int(config.get("note", 60))
        velocity = min(max(int(config.get("velocity", 100)), 0), 127)
        msg = mido.Message("note_on", channel=self._channel, note=note, velocity=velocity)
        self._send(msg)
        self._active_notes[note] = {
            "note": note,
            "velocity": velocity,
            "name": self._note_name(note),
            "timestamp": time.time(),
        }

    @writable(type="dict", description="Send note off (note: 0 to 127)")
    def note_off(self, config: dict):
        """Send Note Off. Config: {note: int}."""
        import mido
        note = int(config.get("note", 60))
        velocity = int(config.get("velocity", 0))
        msg = mido.Message("note_off", channel=self._channel, note=note, velocity=velocity)
        self._send(msg)
        self._active_notes.pop(note, None)

    @writable(type="dict", description="Send control change (control: 0 to 127, value: 0 to 127)")
    def control_change(self, config: dict):
        """Send CC message. Config: {control: int, value: int}."""
        import mido
        control = min(max(int(config.get("control", 1)), 0), 127)
        value = min(max(int(config.get("value", 64)), 0), 127)
        msg = mido.Message("control_change", channel=self._channel,
                           control=control, value=value)
        self._send(msg)
        self._cc_values[control] = value

    @writable(type="int", description="Send program change (0 to 127)")
    def program_change(self, program: int):
        """Switch instrument patch/program."""
        import mido
        program = min(max(program, 0), 127)
        msg = mido.Message("program_change", channel=self._channel, program=program)
        self._send(msg)
        self._program = program

    @writable(type="int", description="Send pitch bend (0=min, 8192=center, 16383=max)")
    def pitch_bend(self, value: int):
        """Send pitch bend. Center = 8192."""
        import mido
        pitch = min(max(value - 8192, -8192), 8191)
        msg = mido.Message("pitchwheel", channel=self._channel, pitch=pitch)
        self._send(msg)
        self._pitch_bend = value

    @writable(type="int", description="Set active MIDI channel (0 to 15)")
    def channel(self, ch: int):
        """Switch active transmit channel."""
        self._channel = min(max(ch, 0), 15)

    @procedure(description="Send system exclusive message (SysEx) to device")
    def send_sysex(self, data: list[int] | None = None, manufacturer_id: list[int] | None = None):
        """Send raw SysEx data. Manufacturer ID is prepended automatically."""
        import mido

        if not data:
            return {"error": "data list is required (list of byte values 0 to 127)"}

        payload = (manufacturer_id or [0x7E]) + data
        msg = mido.Message("sysex", data=payload)
        self._send(msg)

        return {
            "status": "sent",
            "length": len(payload),
            "manufacturer_id": manufacturer_id or [0x7E],
        }

    @procedure(description="Send all notes off on active channel (panic button)")
    def all_notes_off(self):
        """Send CC 123 (All Notes Off) and clear active notes."""
        import mido
        msg = mido.Message("control_change", channel=self._channel, control=123, value=0)
        self._send(msg)
        self._active_notes.clear()
        return {"status": "all_notes_off", "channel": self._channel}

    @procedure(description="Send all sound off (kills sustained/reverb tails too)")
    def all_sound_off(self):
        """Send CC 120 (All Sound Off) for immediate silence."""
        import mido
        msg = mido.Message("control_change", channel=self._channel, control=120, value=0)
        self._send(msg)
        self._active_notes.clear()
        return {"status": "all_sound_off", "channel": self._channel}

    @procedure(description="Reset all controllers to defaults on active channel")
    def reset_controllers(self):
        """Send CC 121 to reset all controllers."""
        import mido
        msg = mido.Message("control_change", channel=self._channel, control=121, value=0)
        self._send(msg)
        self._cc_values.clear()
        self._pitch_bend = 8192
        return {"status": "controllers_reset", "channel": self._channel}

    @procedure(description="Start MIDI clock transport (sends Start + Clock messages)")
    def transport_start(self):
        """Send MIDI Start message to begin clock."""
        import mido
        msg = mido.Message("start")
        self._send(msg)
        self._clock_running = True
        return {"status": "started", "bpm": self._clock_bpm}

    @procedure(description="Stop MIDI clock transport")
    def transport_stop(self):
        """Send MIDI Stop message."""
        import mido
        msg = mido.Message("stop")
        self._send(msg)
        self._clock_running = False
        return {"status": "stopped"}

    @procedure(description="Continue MIDI clock from current position")
    def transport_continue(self):
        """Send MIDI Continue message."""
        import mido
        msg = mido.Message("continue")
        self._send(msg)
        self._clock_running = True
        return {"status": "continued", "position": self._song_position}

    @procedure(description="List available MIDI input and output ports")
    def list_ports(self):
        """Enumerate all available MIDI ports on the system."""
        import mido
        return {
            "input_ports": mido.get_input_names(),
            "output_ports": mido.get_output_names(),
        }

    @procedure(description="Play a chord (multiple notes simultaneously)")
    def play_chord(self, notes: list[int] | None = None, velocity: int = 100,
                    duration_ms: int = 500):
        """Play multiple notes at once. Notes are MIDI note numbers."""
        import mido

        if not notes:
            notes = [60, 64, 67]  # C major

        velocity = min(max(velocity, 0), 127)

        for note in notes:
            n = min(max(note, 0), 127)
            msg = mido.Message("note_on", channel=self._channel, note=n, velocity=velocity)
            self._send(msg)
            self._active_notes[n] = {
                "note": n, "velocity": velocity,
                "name": self._note_name(n), "timestamp": time.time(),
            }

        return {
            "status": "chord_playing",
            "notes": [self._note_name(n) for n in notes],
            "velocity": velocity,
            "duration_ms": duration_ms,
        }

    @procedure(description="Send NRPN (Non Registered Parameter Number) for extended control")
    def send_nrpn(self, parameter_msb: int = 0, parameter_lsb: int = 0,
                   value_msb: int = 0, value_lsb: int = 0):
        """Send a 14 bit NRPN message (4 CC messages in sequence)."""
        import mido
        ch = self._channel

        msgs = [
            mido.Message("control_change", channel=ch, control=99, value=parameter_msb),
            mido.Message("control_change", channel=ch, control=98, value=parameter_lsb),
            mido.Message("control_change", channel=ch, control=6, value=value_msb),
            mido.Message("control_change", channel=ch, control=38, value=value_lsb),
        ]
        for msg in msgs:
            self._send(msg)

        return {
            "status": "sent",
            "parameter": (parameter_msb << 7) | parameter_lsb,
            "value": (value_msb << 7) | value_lsb,
        }

    @procedure(description="Request device identity via Universal SysEx (Identity Request)")
    def identity_request(self):
        """Send Universal SysEx Identity Request to discover device info."""
        import mido
        msg = mido.Message("sysex", data=[0x7E, 0x7F, 0x06, 0x01])
        self._send(msg)
        return {"status": "identity_request_sent", "awaiting_reply": True}

    @monitor(interval_ms=500, description="Monitor MIDI activity and connection health")
    def check_midi_health(self) -> dict[str, Any]:
        alerts = []

        if not self._output_port and not self._input_port:
            alerts.append({
                "level": "critical",
                "message": "No MIDI ports open",
            })

        if self._last_activity_time > 0:
            idle_time = time.time() - self._last_activity_time
            if idle_time > 30:
                alerts.append({
                    "level": "info",
                    "message": f"No MIDI activity for {idle_time:.0f} seconds",
                })

        if len(self._active_notes) > 32:
            alerts.append({
                "level": "warning",
                "message": f"High polyphony ({len(self._active_notes)} notes held)",
            })

        return {
            "healthy": len(alerts) == 0,
            "active_notes": len(self._active_notes),
            "messages_total": self._message_count,
            "transport_running": self._clock_running,
            "bpm": self._clock_bpm,
            "channel": self._channel,
            "output_connected": self._output_port is not None,
            "input_connected": self._input_port is not None,
            "alerts": alerts,
        }
