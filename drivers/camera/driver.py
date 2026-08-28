"""KHP Driver — Camera (USB, IP, RTSP, V4L2).

Supports visual monitoring and frame capture for:
- Pre-movement safety checks (plate orientation, obstruction detection)
- Error detection (bubbles, spills, misalignment)
- Process monitoring (reaction color, growth, fill level)
- Security/surveillance

Requirements:
    pip install opencv-python numpy
"""

from khp import Driver, readable, writable, procedure, safety, monitor
from khp.core import ConnectionType
from typing import Optional
import time
import base64


class Camera(Driver):
    """Camera driver — USB, IP (RTSP/HTTP), or V4L2 cameras."""

    name = "Camera"
    version = "1.0.0"
    device_type = "camera"
    description = "Visual monitoring camera (USB, IP, RTSP)"
    connection_type = ConnectionType.USB

    def __init__(self, device_id: str = None, source: str = "0",
                 width: int = 1920, height: int = 1080, fps: int = 30, **config):
        super().__init__(device_id=device_id, **config)
        self._source = int(source) if source.isdigit() else source
        self._width = width
        self._height = height
        self._fps = fps
        self._cap = None
        self._last_frame = None
        self._recording = False

    async def connect(self):
        import cv2
        self._cap = cv2.VideoCapture(self._source)
        if not self._cap.isOpened():
            from khp.errors import DeviceOfflineError
            raise DeviceOfflineError(
                f"Cannot open camera source: {self._source}",
                device_id=self.device_id,
            )
        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, self._width)
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self._height)
        self._cap.set(cv2.CAP_PROP_FPS, self._fps)
        await super().connect()

    async def disconnect(self):
        if self._cap:
            self._cap.release()
        self._cap = None
        await super().disconnect()

    def _grab_frame(self):
        """Grab a single frame."""
        if self._cap and self._cap.isOpened():
            ret, frame = self._cap.read()
            if ret:
                self._last_frame = frame
                return frame
        return None

    @readable(type="object", description="Camera resolution and properties")
    def camera_info(self) -> dict:
        if not self._cap:
            return {}
        import cv2
        return {
            "width": int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
            "height": int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
            "fps": int(self._cap.get(cv2.CAP_PROP_FPS)),
            "backend": self._cap.getBackendName(),
            "source": str(self._source),
        }

    @readable(type="bool", description="Whether camera is currently capturing")
    def is_active(self) -> bool:
        return self._cap is not None and self._cap.isOpened()

    @readable(type="float", description="Average brightness of last frame (0-255)")
    def brightness(self) -> float:
        frame = self._grab_frame()
        if frame is None:
            return 0.0
        import numpy as np
        return float(np.mean(frame))

    @readable(type="object", description="Motion detection (difference from last frame)")
    def motion(self) -> dict:
        import cv2
        import numpy as np
        prev = self._last_frame
        current = self._grab_frame()
        if prev is None or current is None:
            return {"detected": False, "magnitude": 0.0}
        gray_prev = cv2.cvtColor(prev, cv2.COLOR_BGR2GRAY)
        gray_curr = cv2.cvtColor(current, cv2.COLOR_BGR2GRAY)
        diff = cv2.absdiff(gray_prev, gray_curr)
        magnitude = float(np.mean(diff))
        return {
            "detected": magnitude > 5.0,
            "magnitude": round(magnitude, 2),
        }

    @writable(type="int", description="Set camera exposure (-13 to -1 for auto, 0+ for manual)")
    def exposure(self, value: int):
        import cv2
        if self._cap:
            self._cap.set(cv2.CAP_PROP_EXPOSURE, value)

    @writable(type="int", description="Set camera brightness (0-255)")
    def set_brightness(self, value: int):
        import cv2
        if self._cap:
            self._cap.set(cv2.CAP_PROP_BRIGHTNESS, value)

    @procedure(description="Capture a single frame and save to file",
               estimated_duration_s=1.0)
    def capture(self, output_path: str = "/tmp/khp_frame.jpg",
                quality: int = 95) -> dict:
        """Capture a frame and save as JPEG."""
        import cv2
        frame = self._grab_frame()
        if frame is None:
            return {"success": False, "error": "No frame captured"}
        cv2.imwrite(output_path, frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
        h, w = frame.shape[:2]
        return {"success": True, "path": output_path, "width": w, "height": h}

    @procedure(description="Capture frame and return as base64 JPEG",
               estimated_duration_s=1.0)
    def capture_base64(self, quality: int = 80) -> dict:
        """Capture a frame and return as base64-encoded JPEG."""
        import cv2
        frame = self._grab_frame()
        if frame is None:
            return {"success": False, "error": "No frame captured"}
        _, buffer = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
        b64 = base64.b64encode(buffer).decode("utf-8")
        h, w = frame.shape[:2]
        return {"success": True, "width": w, "height": h, "base64": b64[:100] + "..."}

    @procedure(description="Capture N frames at interval (timelapse)",
               estimated_duration_s=60.0)
    def timelapse(self, count: int = 10, interval_s: float = 1.0,
                  output_dir: str = "/tmp/khp_timelapse") -> dict:
        """Capture a series of frames at regular intervals."""
        import cv2
        import os
        os.makedirs(output_dir, exist_ok=True)
        captured = []
        for i in range(count):
            frame = self._grab_frame()
            if frame is not None:
                path = f"{output_dir}/frame_{i:04d}.jpg"
                cv2.imwrite(path, frame)
                captured.append(path)
            if i < count - 1:
                time.sleep(interval_s)
        return {"count": len(captured), "directory": output_dir}

    @procedure(description="Detect objects/changes in the frame (simple threshold)",
               estimated_duration_s=1.0)
    def detect_anomaly(self, threshold: float = 30.0) -> dict:
        """Simple anomaly detection: checks if frame differs significantly from reference."""
        import cv2
        import numpy as np
        frame = self._grab_frame()
        if frame is None:
            return {"error": "No frame"}
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        mean_brightness = float(np.mean(gray))
        std_brightness = float(np.std(gray))
        edges = cv2.Canny(gray, 50, 150)
        edge_density = float(np.sum(edges > 0)) / (gray.shape[0] * gray.shape[1]) * 100
        anomaly = std_brightness > threshold or edge_density > 30.0
        return {
            "anomaly_detected": anomaly,
            "mean_brightness": round(mean_brightness, 1),
            "std_brightness": round(std_brightness, 1),
            "edge_density_pct": round(edge_density, 1),
        }

    @procedure(description="Check if a specific region contains an object (plate check)",
               estimated_duration_s=1.0)
    def check_region(self, x: int = 0, y: int = 0, w: int = 200, h: int = 200,
                     min_brightness: float = 50.0) -> dict:
        """Check if a specific region of the frame has expected content."""
        import cv2
        import numpy as np
        frame = self._grab_frame()
        if frame is None:
            return {"present": False, "error": "No frame"}
        roi = frame[y:y+h, x:x+w]
        if roi.size == 0:
            return {"present": False, "error": "Invalid region"}
        gray_roi = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        mean = float(np.mean(gray_roi))
        present = mean > min_brightness
        return {
            "present": present,
            "mean_brightness": round(mean, 1),
            "region": {"x": x, "y": y, "w": w, "h": h},
        }
