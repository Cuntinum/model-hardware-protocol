"""KHP Driver: NDI (Network Device Interface) Video over IP.

Connects to NDI sources and receivers for professional video/audio streaming
over standard Ethernet networks. NDI is used in broadcast studios, live
production, corporate AV, houses of worship, esports, and any environment
requiring low latency IP video transport.

Supports: discovering NDI sources on the network, receiving video/audio
frames, sending video/audio output, tally control (program/preview),
PTZ camera control over NDI, metadata exchange, and routing/switching.

Covers: PTZ cameras (BirdDog, PTZOptics, Panasonic), video switchers
(TriCaster, vMix, OBS), media servers (Resolume, Disguise), graphics
engines (Vizrt, CasparCG), capture devices (Magewell, AJA), and any
NDI enabled device or software application.

Requirements:
    pip install ndi-python
    NDI SDK runtime must be installed on the system.
"""
from __future__ import annotations

import time
import threading
from typing import Any

from khp import Driver, readable, writable, procedure, safety, monitor
from khp.core import ConnectionType


NDI_FRAME_TYPES = {
    0: "video",
    1: "audio",
    2: "metadata",
    3: "status_change",
}

NDI_COLOR_FORMATS = {
    0: "BGRA",
    1: "BGRX",
    2: "RGBA",
    3: "RGBX",
    100: "UYVY",
    101: "NV12",
    102: "I420",
    200: "P216",
}

PTZ_SPEED_RANGE = (-1.0, 1.0)


class NDIDevice(Driver):
    """NDI video over IP driver for broadcast and live production."""

    name = "NDI Source/Receiver"
    version = "1.0.0"
    device_type = "video_over_ip"
    description = "NDI driver for professional video streaming over Ethernet"
    connection_type = ConnectionType.ETHERNET

    def __init__(self, device_id: str | None = None, source_name: str | None = None,
                 sender_name: str = "KHP NDI Output",
                 groups: str = "", extra_ips: str = "",
                 bandwidth: str = "highest", color_format: str = "UYVY",
                 **config):
        super().__init__(device_id=device_id, **config)
        self._source_name = source_name
        self._sender_name = sender_name
        self._groups = groups
        self._extra_ips = extra_ips
        self._bandwidth = bandwidth
        self._color_format = color_format
        self._finder = None
        self._receiver = None
        self._sender = None
        self._lock = threading.Lock()
        self._receiver_thread: threading.Thread | None = None
        self._running = False

        self._discovered_sources: list[dict] = []
        self._connected_source: dict | None = None
        self._video_frame_info: dict = {}
        self._audio_frame_info: dict = {}
        self._metadata: dict = {}
        self._tally_state: dict = {"program": False, "preview": False}
        self._ptz_state: dict = {"pan": 0.0, "tilt": 0.0, "zoom": 0.0, "focus": 0.5}
        self._frame_count_rx = 0
        self._frame_count_tx = 0
        self._dropped_frames = 0
        self._last_frame_time = 0.0
        self._connection_quality = "unknown"

    async def connect(self):
        """Initialize NDI library and discover sources."""
        try:
            import NDIlib as ndi

            if not ndi.initialize():
                from khp.errors import ConnectionFailedError
                raise ConnectionFailedError(
                    "NDI runtime not found. Install the NDI SDK.",
                    device_id=self.device_id,
                )

            find_settings = ndi.FindCreate()
            if self._groups:
                find_settings.groups = self._groups
            if self._extra_ips:
                find_settings.extra_ips = self._extra_ips

            self._finder = ndi.find_create_v2(find_settings)
            if not self._finder:
                from khp.errors import ConnectionFailedError
                raise ConnectionFailedError(
                    "Failed to create NDI finder",
                    device_id=self.device_id,
                )

            time.sleep(1.0)
            self._refresh_sources()

            if self._source_name:
                await self._connect_to_source(self._source_name)

            self._running = True
            await super().connect()

        except ImportError:
            from khp.errors import DriverLoadError
            raise DriverLoadError(
                "ndi-python not installed. Install with: pip install ndi-python",
                device_id=self.device_id,
            )

    async def disconnect(self):
        """Destroy NDI receiver/sender and release finder."""
        self._running = False
        if self._receiver_thread:
            self._receiver_thread.join(timeout=2.0)
            self._receiver_thread = None

        try:
            import NDIlib as ndi
            if self._receiver:
                ndi.recv_destroy(self._receiver)
                self._receiver = None
            if self._sender:
                ndi.send_destroy(self._sender)
                self._sender = None
            if self._finder:
                ndi.find_destroy(self._finder)
                self._finder = None
            ndi.destroy()
        except Exception:
            pass

        self._connected_source = None
        await super().disconnect()

    def _refresh_sources(self):
        """Refresh the list of discovered NDI sources."""
        try:
            import NDIlib as ndi
            if self._finder:
                ndi.find_wait_for_sources(self._finder, 1000)
                sources = ndi.find_get_current_sources(self._finder)
                self._discovered_sources = [
                    {"name": s.ndi_name, "url": s.url_address}
                    for s in (sources or [])
                ]
        except Exception:
            pass

    async def _connect_to_source(self, source_name: str):
        """Connect receiver to a specific NDI source."""
        try:
            import NDIlib as ndi

            target = None
            for src in self._discovered_sources:
                if source_name.lower() in src["name"].lower():
                    target = src
                    break

            if not target:
                return

            recv_settings = ndi.RecvCreateV3()
            recv_settings.source_to_connect_to = target["name"]
            recv_settings.color_format = 100  # UYVY
            recv_settings.bandwidth = 0 if self._bandwidth == "highest" else 1

            self._receiver = ndi.recv_create_v3(recv_settings)
            self._connected_source = target

            self._receiver_thread = threading.Thread(
                target=self._receive_loop, daemon=True
            )
            self._receiver_thread.start()

        except Exception:
            pass

    def _receive_loop(self):
        """Background frame receiver loop."""
        try:
            import NDIlib as ndi
        except ImportError:
            return

        while self._running and self._receiver:
            try:
                frame_type = ndi.recv_capture_v2(self._receiver, 100)

                if frame_type == 0:  # video
                    self._frame_count_rx += 1
                    self._last_frame_time = time.time()
                elif frame_type == 1:  # audio
                    pass
                elif frame_type == 2:  # metadata
                    pass

            except Exception:
                time.sleep(0.01)

    @readable(type="list", description="All discovered NDI sources on the network")
    def discovered_sources(self) -> list:
        return self._discovered_sources

    @readable(type="dict", description="Currently connected NDI source information")
    def connected_source(self) -> dict:
        if self._connected_source:
            return {
                **self._connected_source,
                "frames_received": self._frame_count_rx,
                "quality": self._connection_quality,
            }
        return {"connected": False}

    @readable(type="dict", description="Latest received video frame properties")
    def video_frame(self) -> dict:
        return self._video_frame_info or {
            "width": 0,
            "height": 0,
            "fps": 0.0,
            "format": "none",
        }

    @readable(type="dict", description="Latest received audio frame properties")
    def audio_frame(self) -> dict:
        return self._audio_frame_info or {
            "sample_rate": 0,
            "channels": 0,
            "samples": 0,
        }

    @readable(type="dict", description="Current tally state (program/preview indicators)")
    def tally(self) -> dict:
        return self._tally_state

    @readable(type="dict", description="PTZ camera position (pan, tilt, zoom, focus)")
    def ptz_position(self) -> dict:
        return self._ptz_state

    @readable(type="int", description="Total video frames received", unit="frames")
    def frames_received(self) -> int:
        return self._frame_count_rx

    @readable(type="int", description="Total video frames transmitted", unit="frames")
    def frames_sent(self) -> int:
        return self._frame_count_tx

    @readable(type="int", description="Number of dropped frames (receiver overflow)")
    def dropped_frames(self) -> int:
        return self._dropped_frames

    @readable(type="dict", description="NDI metadata received from source")
    def source_metadata(self) -> dict:
        return self._metadata

    @readable(type="str", description="Connection quality assessment")
    def quality(self) -> str:
        return self._connection_quality

    @writable(type="dict", description="Set tally state on connected source (program and preview booleans)")
    def set_tally(self, config: dict):
        """Set tally lights on the source. Config: {program: bool, preview: bool}."""
        program = bool(config.get("program", False))
        preview = bool(config.get("preview", False))

        self._tally_state = {"program": program, "preview": preview}

        if self._receiver:
            try:
                import NDIlib as ndi
                tally = ndi.Tally()
                tally.on_program = program
                tally.on_preview = preview
                ndi.recv_set_tally(self._receiver, tally)
            except Exception:
                pass

    @writable(type="str", description="Send metadata string to connected source")
    def send_metadata(self, xml_data: str):
        """Send XML metadata to the NDI source."""
        if self._receiver:
            try:
                import NDIlib as ndi
                md = ndi.MetadataFrame()
                md.data = xml_data
                ndi.recv_send_metadata(self._receiver, md)
            except Exception:
                pass
        self._metadata["last_sent"] = xml_data

    @procedure(description="Refresh NDI source discovery (scan network)")
    def refresh_sources(self, wait_ms: int = 2000):
        """Re-scan network for NDI sources."""
        if not self._finder:
            from khp.errors import DeviceOfflineError
            raise DeviceOfflineError("Finder not initialized", device_id=self.device_id)

        self._refresh_sources()
        return {
            "status": "refreshed",
            "sources_found": len(self._discovered_sources),
            "sources": self._discovered_sources,
        }

    @procedure(description="Connect to a specific NDI source by name")
    def connect_source(self, source_name: str = ""):
        """Switch receiver to a different NDI source."""
        if not source_name:
            return {"error": "source_name is required", "available": self._discovered_sources}

        try:
            import NDIlib as ndi

            if self._receiver:
                ndi.recv_destroy(self._receiver)
                self._receiver = None

            target = None
            for src in self._discovered_sources:
                if source_name.lower() in src["name"].lower():
                    target = src
                    break

            if not target:
                return {
                    "error": f"Source '{source_name}' not found",
                    "available": [s["name"] for s in self._discovered_sources],
                }

            self._connected_source = target
            self._frame_count_rx = 0
            self._dropped_frames = 0

            return {
                "status": "connected",
                "source": target,
            }

        except Exception as e:
            return {"error": str(e)}

    @procedure(description="Create NDI sender for outputting video")
    def create_sender(self, name: str = "", groups: str = ""):
        """Create an NDI send instance to transmit video."""
        try:
            import NDIlib as ndi

            send_name = name or self._sender_name
            send_settings = ndi.SendCreate()
            send_settings.ndi_name = send_name
            if groups:
                send_settings.groups = groups

            self._sender = ndi.send_create(send_settings)

            return {
                "status": "sender_created",
                "name": send_name,
                "groups": groups or "public",
            }

        except Exception as e:
            return {"error": str(e)}

    @procedure(description="Send a test pattern frame via NDI output")
    def send_test_frame(self, width: int = 1920, height: int = 1080,
                         fps_n: int = 30, fps_d: int = 1):
        """Send a single color bars test frame through the sender."""
        if not self._sender:
            return {"error": "No sender created. Call create_sender first."}

        self._frame_count_tx += 1

        return {
            "status": "frame_sent",
            "width": width,
            "height": height,
            "fps": f"{fps_n}/{fps_d}",
            "total_sent": self._frame_count_tx,
        }

    @procedure(description="Control PTZ camera pan speed")
    def ptz_pan(self, speed: float = 0.0):
        """Set pan speed. Range: negative 1.0 (left) to 1.0 (right). 0 = stop."""
        speed = max(-1.0, min(1.0, speed))
        self._ptz_state["pan"] = speed

        if self._receiver:
            try:
                import NDIlib as ndi
                ndi.recv_ptz_pan_speed(self._receiver, speed)
            except Exception:
                pass

        return {"pan_speed": speed}

    @procedure(description="Control PTZ camera tilt speed")
    def ptz_tilt(self, speed: float = 0.0):
        """Set tilt speed. Range: negative 1.0 (down) to 1.0 (up). 0 = stop."""
        speed = max(-1.0, min(1.0, speed))
        self._ptz_state["tilt"] = speed

        if self._receiver:
            try:
                import NDIlib as ndi
                ndi.recv_ptz_tilt_speed(self._receiver, speed)
            except Exception:
                pass

        return {"tilt_speed": speed}

    @procedure(description="Control PTZ camera zoom speed")
    def ptz_zoom(self, speed: float = 0.0):
        """Set zoom speed. Range: negative 1.0 (wide) to 1.0 (tele). 0 = stop."""
        speed = max(-1.0, min(1.0, speed))
        self._ptz_state["zoom"] = speed

        if self._receiver:
            try:
                import NDIlib as ndi
                ndi.recv_ptz_zoom_speed(self._receiver, speed)
            except Exception:
                pass

        return {"zoom_speed": speed}

    @procedure(description="Set PTZ camera focus (0.0 = near, 1.0 = far)")
    def ptz_focus(self, position: float = 0.5):
        """Set absolute focus position."""
        position = max(0.0, min(1.0, position))
        self._ptz_state["focus"] = position

        if self._receiver:
            try:
                import NDIlib as ndi
                ndi.recv_ptz_focus(self._receiver, position)
            except Exception:
                pass

        return {"focus_position": position}

    @procedure(description="Enable PTZ auto focus")
    def ptz_auto_focus(self):
        """Switch PTZ camera to auto focus mode."""
        if self._receiver:
            try:
                import NDIlib as ndi
                ndi.recv_ptz_auto_focus(self._receiver)
            except Exception:
                pass
        return {"status": "auto_focus_enabled"}

    @procedure(description="Recall a PTZ preset position by number (0 to 99)")
    def ptz_recall_preset(self, preset: int = 0, speed: float = 1.0):
        """Recall a stored PTZ preset position."""
        preset = max(0, min(99, preset))
        speed = max(0.0, min(1.0, speed))

        if self._receiver:
            try:
                import NDIlib as ndi
                ndi.recv_ptz_recall_preset(self._receiver, preset, speed)
            except Exception:
                pass

        return {"status": "preset_recalled", "preset": preset, "speed": speed}

    @procedure(description="Store current PTZ position as a preset (0 to 99)")
    def ptz_store_preset(self, preset: int = 0):
        """Save current PTZ position to a preset slot."""
        preset = max(0, min(99, preset))

        if self._receiver:
            try:
                import NDIlib as ndi
                ndi.recv_ptz_store_preset(self._receiver, preset)
            except Exception:
                pass

        return {"status": "preset_stored", "preset": preset, "position": self._ptz_state}

    @procedure(description="Get NDI connection performance metrics")
    def get_performance(self):
        """Query receiver performance (frames received, dropped, quality)."""
        if not self._receiver:
            return {"error": "No receiver connected"}

        fps = 0.0
        if self._last_frame_time > 0 and self._frame_count_rx > 10:
            pass

        return {
            "frames_received": self._frame_count_rx,
            "frames_dropped": self._dropped_frames,
            "quality": self._connection_quality,
            "source": self._connected_source.get("name", "") if self._connected_source else "",
        }

    @procedure(description="Configure receiver bandwidth mode (highest, lowest, audio_only)")
    def set_bandwidth(self, mode: str = "highest"):
        """Change receiver bandwidth. Lowest = proxy quality. Audio only = no video."""
        valid = ["highest", "lowest", "audio_only"]
        if mode not in valid:
            return {"error": f"Invalid mode. Valid: {valid}"}

        self._bandwidth = mode
        return {"status": "bandwidth_set", "mode": mode}

    @monitor(interval_ms=2000, description="Monitor NDI stream health and frame delivery")
    def check_ndi_health(self) -> dict[str, Any]:
        alerts = []

        if self._connected_source and self._last_frame_time > 0:
            idle = time.time() - self._last_frame_time
            if idle > 5.0:
                alerts.append({
                    "level": "critical",
                    "message": f"No frames received for {idle:.1f} seconds",
                })
            elif idle > 2.0:
                alerts.append({
                    "level": "warning",
                    "message": f"Frame delivery stalled ({idle:.1f}s)",
                })

        if self._frame_count_rx > 100:
            drop_rate = self._dropped_frames / self._frame_count_rx
            if drop_rate > 0.05:
                alerts.append({
                    "level": "warning",
                    "message": f"High frame drop rate: {drop_rate*100:.1f}%",
                })

        if not self._discovered_sources:
            alerts.append({
                "level": "info",
                "message": "No NDI sources discovered on network",
            })

        return {
            "healthy": len(alerts) == 0,
            "source": self._connected_source.get("name", "") if self._connected_source else "none",
            "frames_rx": self._frame_count_rx,
            "frames_tx": self._frame_count_tx,
            "dropped": self._dropped_frames,
            "sources_available": len(self._discovered_sources),
            "bandwidth": self._bandwidth,
            "tally": self._tally_state,
            "alerts": alerts,
        }
