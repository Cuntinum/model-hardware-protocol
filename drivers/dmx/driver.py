"""KHP Driver: DMX512 and ArtNet Lighting Controller.

Implements a DMX512 universe controller over ArtNet (UDP protocol). Supports
individual channel control, fixture grouping, scene management, fade transitions,
and multi universe output. DMX512 is the standard protocol for stage lighting,
architectural illumination, and entertainment effects.

Covers: Moving heads, LED pars, wash fixtures, strobes, fog machines, laser
controllers, dimmers, pixel tape (WS2812 via DMX bridge), house lighting,
theatrical rigs, concert stages, and architectural installations.

Requirements:
    pip install stupidArtnet
"""
from __future__ import annotations

import time
import struct
import threading
from typing import Any
from dataclasses import dataclass, field

from khp import Driver, readable, writable, procedure, safety, monitor
from khp.core import ConnectionType


DMX_UNIVERSE_SIZE = 512


@dataclass
class Fixture:
    name: str
    start_channel: int
    channel_count: int
    fixture_type: str = "generic"
    label: str = ""


@dataclass
class Scene:
    name: str
    channels: dict[int, int] = field(default_factory=dict)
    fade_time_ms: int = 0
    created_at: float = 0.0


class DMXDevice(Driver):
    """DMX512/ArtNet lighting controller for stage and architectural fixtures."""

    name = "DMX512 ArtNet Controller"
    version = "1.0.0"
    device_type = "lighting_controller"
    description = "DMX512 universe controller via ArtNet UDP protocol"
    connection_type = ConnectionType.UDP

    def __init__(self, device_id: str | None = None, target_ip: str = "255.255.255.255",
                 universe: int = 0, port: int = 6454, fps: int = 40,
                 num_universes: int = 1, **config):
        super().__init__(device_id=device_id, target_ip=target_ip, universe=universe, **config)
        self._target_ip = target_ip
        self._universe = universe
        self._port = port
        self._fps = fps
        self._num_universes = num_universes

        self._artnet = None
        self._dmx_data = bytearray(DMX_UNIVERSE_SIZE)
        self._output_enabled = True
        self._master_dimmer = 255
        self._blackout = False

        self._fixtures: dict[str, Fixture] = {}
        self._scenes: dict[str, Scene] = {}
        self._active_scene: str | None = None
        self._fade_thread: threading.Thread | None = None
        self._fade_running = False
        self._frame_count = 0
        self._last_frame_time: float = 0.0
        self._lock = threading.Lock()

    async def connect(self):
        """Initialize ArtNet output socket and start transmitting."""
        try:
            from stupidArtnet import StupidArtnet
        except ImportError:
            from khp.errors import DriverLoadError
            raise DriverLoadError(
                "stupidArtnet not installed. Install with: pip install stupidArtnet",
                device_id=self.device_id,
            )

        self._artnet = StupidArtnet(
            self._target_ip,
            self._universe,
            DMX_UNIVERSE_SIZE,
            self._fps,
            True,
        )
        self._artnet.start()
        self._output_enabled = True
        await super().connect()

    async def disconnect(self):
        """Stop ArtNet transmission and blackout."""
        if self._fade_running:
            self._fade_running = False
            if self._fade_thread:
                self._fade_thread.join(timeout=2.0)

        if self._artnet:
            self._artnet.blackout()
            self._artnet.stop()
            self._artnet.close()
            self._artnet = None

        self._output_enabled = False
        await super().disconnect()

    def _send_frame(self):
        """Send current DMX buffer to ArtNet output."""
        if not self._artnet or not self._output_enabled:
            return

        output = bytearray(DMX_UNIVERSE_SIZE)
        with self._lock:
            for i in range(DMX_UNIVERSE_SIZE):
                if self._blackout:
                    output[i] = 0
                else:
                    val = self._dmx_data[i]
                    val = int(val * self._master_dimmer / 255)
                    output[i] = min(255, max(0, val))

        self._artnet.set(output)
        self._frame_count += 1
        self._last_frame_time = time.time()

    def _fade_worker(self, target: dict[int, int], duration_ms: int):
        """Background thread for smooth fading between values."""
        start_values = {}
        with self._lock:
            for ch in target:
                if 1 <= ch <= DMX_UNIVERSE_SIZE:
                    start_values[ch] = self._dmx_data[ch - 1]

        steps = max(1, int(duration_ms / (1000.0 / self._fps)))
        self._fade_running = True

        for step in range(steps + 1):
            if not self._fade_running:
                break
            progress = step / steps
            with self._lock:
                for ch, end_val in target.items():
                    if 1 <= ch <= DMX_UNIVERSE_SIZE:
                        start_val = start_values.get(ch, 0)
                        current = int(start_val + (end_val - start_val) * progress)
                        self._dmx_data[ch - 1] = min(255, max(0, current))
            self._send_frame()
            time.sleep(1.0 / self._fps)

        self._fade_running = False

    @readable(type="list", description="Current DMX channel values (1 to 512)")
    def channel_values(self) -> list:
        with self._lock:
            return list(self._dmx_data)

    @readable(type="bool", description="Whether blackout mode is active")
    def is_blackout(self) -> bool:
        return self._blackout

    @readable(type="int", description="Master dimmer level (0 to 255)")
    def master_level(self) -> int:
        return self._master_dimmer

    @readable(type="dict", description="Registered fixture definitions and addresses")
    def fixtures(self) -> dict:
        return {
            name: {
                "start_channel": f.start_channel,
                "channel_count": f.channel_count,
                "fixture_type": f.fixture_type,
                "label": f.label,
                "channels": list(range(f.start_channel, f.start_channel + f.channel_count)),
            }
            for name, f in self._fixtures.items()
        }

    @readable(type="list", description="Available scene names")
    def scene_list(self) -> list:
        return [
            {"name": s.name, "channels": len(s.channels), "fade_ms": s.fade_time_ms}
            for s in self._scenes.values()
        ]

    @readable(type="str", description="Currently active scene name (or none)")
    def active_scene(self) -> str | None:
        return self._active_scene

    @readable(type="dict", description="ArtNet output statistics")
    def output_stats(self) -> dict:
        return {
            "universe": self._universe,
            "target_ip": self._target_ip,
            "fps": self._fps,
            "frames_sent": self._frame_count,
            "output_enabled": self._output_enabled,
            "fade_active": self._fade_running,
            "last_frame_age_ms": (time.time() - self._last_frame_time) * 1000 if self._last_frame_time else None,
        }

    @safety(min=0, max=255, reason="DMX channel values must be 0 to 255 (8 bit)", hard=True)
    @writable(type="dict", description="Set a single DMX channel value ({channel: 1 to 512, value: 0 to 255})")
    def set_channel(self, config: dict):
        channel = int(config.get("channel", 1))
        value = int(config.get("value", 0))
        if channel < 1 or channel > DMX_UNIVERSE_SIZE:
            from khp.errors import SafetyBlockedError
            raise SafetyBlockedError(
                f"Channel {channel} out of range (1 to {DMX_UNIVERSE_SIZE})",
                device_id=self.device_id,
                property_name="channel",
                value=channel,
                limit={"min": 1, "max": DMX_UNIVERSE_SIZE},
            )
        with self._lock:
            self._dmx_data[channel - 1] = min(255, max(0, value))
        self._send_frame()

    @safety(min=0, max=255, reason="Master dimmer must be 0 to 255", hard=True)
    @writable(type="int", description="Set master dimmer level (scales all output)")
    def master_dimmer(self, value: int):
        self._master_dimmer = min(255, max(0, int(value)))
        self._send_frame()

    @writable(type="bool", description="Enable or disable blackout mode (all outputs zero)")
    def blackout(self, value: bool):
        self._blackout = bool(value)
        self._send_frame()

    @procedure(description="Set multiple DMX channels at once")
    def set_channels(self, channels: dict[int, int] | None = None, fade_ms: int = 0):
        """Set multiple channels. channels = {channel_number: value}.
        Optional fade_ms for smooth transition."""
        if not channels:
            return {"status": "no channels provided"}

        if fade_ms > 0:
            if self._fade_running:
                self._fade_running = False
                if self._fade_thread:
                    self._fade_thread.join(timeout=2.0)
            self._fade_thread = threading.Thread(
                target=self._fade_worker,
                args=(channels, fade_ms),
                daemon=True,
            )
            self._fade_thread.start()
            return {"status": "fading", "channels": len(channels), "duration_ms": fade_ms}

        with self._lock:
            for ch, val in channels.items():
                ch = int(ch)
                if 1 <= ch <= DMX_UNIVERSE_SIZE:
                    self._dmx_data[ch - 1] = min(255, max(0, int(val)))
        self._send_frame()
        return {"status": "set", "channels": len(channels)}

    @procedure(description="Register a fixture with name, start channel, and channel count")
    def add_fixture(self, name: str = "", start_channel: int = 1,
                    channel_count: int = 1, fixture_type: str = "generic",
                    label: str = ""):
        if not name:
            return {"error": "fixture name required"}
        if start_channel < 1 or start_channel + channel_count - 1 > DMX_UNIVERSE_SIZE:
            return {"error": "fixture channels exceed DMX universe bounds"}

        self._fixtures[name] = Fixture(
            name=name,
            start_channel=start_channel,
            channel_count=channel_count,
            fixture_type=fixture_type,
            label=label,
        )
        return {"status": "added", "fixture": name, "channels": list(range(start_channel, start_channel + channel_count))}

    @procedure(description="Remove a registered fixture")
    def remove_fixture(self, name: str = ""):
        if name in self._fixtures:
            del self._fixtures[name]
            return {"status": "removed", "fixture": name}
        return {"error": f"fixture '{name}' not found"}

    @procedure(description="Set all channels of a named fixture")
    def set_fixture(self, name: str = "", values: list[int] | None = None, fade_ms: int = 0):
        """Set fixture channels. values = list of channel values in fixture order."""
        if name not in self._fixtures:
            return {"error": f"fixture '{name}' not found"}
        fixture = self._fixtures[name]
        if not values:
            return {"error": "values list required"}

        channels = {}
        for i, val in enumerate(values[:fixture.channel_count]):
            channels[fixture.start_channel + i] = val

        return self.set_channels(channels=channels, fade_ms=fade_ms)

    @procedure(description="Save current DMX state as a named scene")
    def save_scene(self, name: str = "", fade_time_ms: int = 0):
        if not name:
            return {"error": "scene name required"}

        with self._lock:
            channels = {}
            for i in range(DMX_UNIVERSE_SIZE):
                if self._dmx_data[i] > 0:
                    channels[i + 1] = self._dmx_data[i]

        self._scenes[name] = Scene(
            name=name,
            channels=channels,
            fade_time_ms=fade_time_ms,
            created_at=time.time(),
        )
        return {"status": "saved", "scene": name, "active_channels": len(channels)}

    @procedure(description="Recall a saved scene (optionally with crossfade)")
    def recall_scene(self, name: str = "", fade_ms: int | None = None):
        if name not in self._scenes:
            return {"error": f"scene '{name}' not found"}

        scene = self._scenes[name]
        duration = fade_ms if fade_ms is not None else scene.fade_time_ms
        self._active_scene = name

        full_channels = {i: 0 for i in range(1, DMX_UNIVERSE_SIZE + 1)}
        full_channels.update(scene.channels)

        if duration > 0:
            return self.set_channels(channels=scene.channels, fade_ms=duration)
        else:
            with self._lock:
                self._dmx_data = bytearray(DMX_UNIVERSE_SIZE)
                for ch, val in scene.channels.items():
                    if 1 <= ch <= DMX_UNIVERSE_SIZE:
                        self._dmx_data[ch - 1] = min(255, max(0, val))
            self._send_frame()
            return {"status": "recalled", "scene": name, "channels": len(scene.channels)}

    @procedure(description="Delete a saved scene")
    def delete_scene(self, name: str = ""):
        if name in self._scenes:
            del self._scenes[name]
            if self._active_scene == name:
                self._active_scene = None
            return {"status": "deleted", "scene": name}
        return {"error": f"scene '{name}' not found"}

    @procedure(description="Set all channels to zero (blackout without master dimmer change)")
    def all_off(self, fade_ms: int = 0):
        channels = {i: 0 for i in range(1, DMX_UNIVERSE_SIZE + 1)}
        return self.set_channels(channels=channels, fade_ms=fade_ms)

    @procedure(description="Set all channels to full (255)")
    def all_full(self, fade_ms: int = 0):
        channels = {i: 255 for i in range(1, DMX_UNIVERSE_SIZE + 1)}
        return self.set_channels(channels=channels, fade_ms=fade_ms)

    @procedure(description="Generate a color wash across RGB fixtures")
    def color_wash(self, red: int = 0, green: int = 0, blue: int = 0,
                   white: int = 0, fade_ms: int = 0):
        """Apply RGBW color to all registered fixtures that have 3+ channels."""
        channels = {}
        for fixture in self._fixtures.values():
            if fixture.channel_count >= 3:
                channels[fixture.start_channel] = red
                channels[fixture.start_channel + 1] = green
                channels[fixture.start_channel + 2] = blue
                if fixture.channel_count >= 4:
                    channels[fixture.start_channel + 3] = white

        if channels:
            return self.set_channels(channels=channels, fade_ms=fade_ms)
        return {"status": "no RGB fixtures registered"}

    @procedure(description="Run a chase pattern across fixtures (sequential highlight)")
    def chase(self, intensity: int = 255, gap: int = 0, hold_ms: int = 200):
        """Run a simple chase across all registered fixtures."""
        if not self._fixtures:
            return {"error": "no fixtures registered"}

        fixture_list = sorted(self._fixtures.values(), key=lambda f: f.start_channel)
        steps = len(fixture_list)

        for step in range(steps):
            with self._lock:
                for i, f in enumerate(fixture_list):
                    val = intensity if i == step else gap
                    for ch in range(f.start_channel, f.start_channel + f.channel_count):
                        if 1 <= ch <= DMX_UNIVERSE_SIZE:
                            self._dmx_data[ch - 1] = val
            self._send_frame()
            time.sleep(hold_ms / 1000.0)

        return {"status": "chase_complete", "steps": steps}

    @monitor(interval_ms=2000, description="Monitor DMX output health and frame rate")
    def check_dmx_health(self) -> dict[str, Any]:
        alerts = []

        if not self._artnet:
            alerts.append({"level": "critical", "message": "ArtNet output not initialized"})

        if not self._output_enabled:
            alerts.append({"level": "warning", "message": "DMX output disabled"})

        if self._last_frame_time and (time.time() - self._last_frame_time) > 5.0:
            alerts.append({"level": "warning", "message": "No frames sent in 5 seconds"})

        active_channels = sum(1 for v in self._dmx_data if v > 0)

        return {
            "healthy": len(alerts) == 0,
            "universe": self._universe,
            "active_channels": active_channels,
            "master_dimmer": self._master_dimmer,
            "blackout": self._blackout,
            "frames_sent": self._frame_count,
            "fade_active": self._fade_running,
            "fixtures_registered": len(self._fixtures),
            "scenes_saved": len(self._scenes),
            "alerts": alerts,
        }
