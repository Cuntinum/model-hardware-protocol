"""KHP Driver — File-Drop Interface.

Supports devices that communicate by dropping files into watched directories.
This is common in legacy lab automation:
- Scheduling software that reads job files
- Plate readers that export CSV results
- Sequencers that write FASTQ to output directories
- CNC machines that read G-code from folders
- 3D printers with file-based job submission

Requirements:
    pip install watchdog (for directory monitoring)
"""

from khp import Driver, readable, writable, procedure, safety
from khp.core import ConnectionType
from typing import Optional, List
import json
import os
import time
import glob
from pathlib import Path


class FileDropDevice(Driver):
    """File-drop interface driver — communicate via filesystem."""

    name = "File-Drop Device"
    version = "1.0.0"
    device_type = "custom"
    description = "File-based command interface (drop files to control, watch for results)"
    connection_type = ConnectionType.FILE_DROP

    def __init__(self, device_id: str = None,
                 input_dir: str = "/tmp/khp/input",
                 output_dir: str = "/tmp/khp/output",
                 status_file: str = None,
                 file_format: str = "json",
                 poll_interval_s: float = 1.0, **config):
        super().__init__(device_id=device_id, **config)
        self._input_dir = Path(input_dir)
        self._output_dir = Path(output_dir)
        self._status_file = Path(status_file) if status_file else None
        self._file_format = file_format
        self._poll_interval = poll_interval_s
        self._job_counter = 0
        self._last_result = None

    async def connect(self):
        self._input_dir.mkdir(parents=True, exist_ok=True)
        self._output_dir.mkdir(parents=True, exist_ok=True)
        await super().connect()

    @readable(type="array", description="Files currently in input directory (pending jobs)")
    def pending_jobs(self) -> list:
        return sorted([f.name for f in self._input_dir.iterdir() if f.is_file()])

    @readable(type="array", description="Files in output directory (completed results)")
    def completed_results(self) -> list:
        return sorted([f.name for f in self._output_dir.iterdir() if f.is_file()])

    @readable(type="int", description="Number of pending jobs")
    def pending_count(self) -> int:
        return len(list(self._input_dir.iterdir()))

    @readable(type="object", description="Device status from status file")
    def device_status_info(self) -> dict:
        if self._status_file and self._status_file.exists():
            try:
                with open(self._status_file) as f:
                    if self._file_format == "json":
                        return json.load(f)
                    return {"raw": f.read()}
            except (json.JSONDecodeError, IOError):
                return {"status": "unknown"}
        return {"status": "no status file configured"}

    @readable(type="object", description="Latest result from output directory")
    def latest_result(self) -> dict:
        files = sorted(self._output_dir.iterdir(), key=lambda f: f.stat().st_mtime)
        if not files:
            return {"result": None}
        latest = files[-1]
        try:
            with open(latest) as f:
                if self._file_format == "json":
                    return json.load(f)
                return {"filename": latest.name, "content": f.read()[:5000]}
        except (json.JSONDecodeError, IOError):
            return {"filename": latest.name, "error": "cannot read"}

    @procedure(description="Submit a job by writing a file to input directory",
               estimated_duration_s=1.0)
    def submit_job(self, content: dict = None, filename: str = None) -> dict:
        """Write a job file to the input directory."""
        self._job_counter += 1
        if filename is None:
            filename = f"job_{self._job_counter:04d}.{self._file_format}"
        path = self._input_dir / filename
        content = content or {"job_id": self._job_counter}

        with open(path, "w") as f:
            if self._file_format == "json":
                json.dump(content, f, indent=2)
            else:
                f.write(str(content))

        return {"submitted": True, "filename": filename, "path": str(path)}

    @procedure(description="Submit raw text/G-code to input directory",
               estimated_duration_s=1.0)
    def submit_raw(self, content: str, filename: str = None) -> dict:
        """Write raw text content as a job file."""
        self._job_counter += 1
        if filename is None:
            filename = f"job_{self._job_counter:04d}.txt"
        path = self._input_dir / filename
        with open(path, "w") as f:
            f.write(content)
        return {"submitted": True, "filename": filename}

    @procedure(description="Wait for a result file to appear in output directory",
               estimated_duration_s=120.0)
    def wait_for_result(self, pattern: str = "*", timeout_s: float = 60.0) -> dict:
        """Wait for a new file matching pattern to appear in output directory."""
        existing = set(f.name for f in self._output_dir.iterdir())
        start = time.time()
        while time.time() - start < timeout_s:
            current = set(f.name for f in self._output_dir.iterdir())
            new_files = current - existing
            for name in new_files:
                if pattern == "*" or glob.fnmatch.fnmatch(name, pattern):
                    path = self._output_dir / name
                    try:
                        with open(path) as f:
                            if self._file_format == "json":
                                content = json.load(f)
                            else:
                                content = f.read()[:5000]
                        return {"found": True, "filename": name, "content": content}
                    except (json.JSONDecodeError, IOError) as e:
                        return {"found": True, "filename": name, "error": str(e)}
            time.sleep(self._poll_interval)
        return {"found": False, "timeout": True}

    @procedure(description="Read a specific result file from output directory",
               estimated_duration_s=0.5)
    def read_result(self, filename: str) -> dict:
        """Read a specific file from the output directory."""
        path = self._output_dir / filename
        if not path.exists():
            return {"error": f"File not found: {filename}"}
        try:
            with open(path) as f:
                if self._file_format == "json":
                    return json.load(f)
                return {"filename": filename, "content": f.read()[:10000]}
        except (json.JSONDecodeError, IOError) as e:
            return {"error": str(e)}

    @procedure(description="Clear all files from input directory",
               requires_confirmation=True, estimated_duration_s=2.0)
    def clear_input(self) -> dict:
        """Remove all files from the input directory."""
        count = 0
        for f in self._input_dir.iterdir():
            if f.is_file():
                f.unlink()
                count += 1
        return {"cleared": count}

    @procedure(description="Clear all files from output directory",
               requires_confirmation=True, estimated_duration_s=2.0)
    def clear_output(self) -> dict:
        """Remove all files from the output directory."""
        count = 0
        for f in self._output_dir.iterdir():
            if f.is_file():
                f.unlink()
                count += 1
        return {"cleared": count}


class GCodePrinter(FileDropDevice):
    """3D printer / CNC that accepts G-code files."""

    name = "G-Code Printer"
    version = "1.0.0"
    device_type = "custom"
    description = "3D printer or CNC machine accepting G-code via file drop"

    def __init__(self, device_id: str = None,
                 gcode_dir: str = "/tmp/printer/gcode",
                 status_file: str = "/tmp/printer/status.json", **config):
        super().__init__(
            device_id=device_id,
            input_dir=gcode_dir,
            output_dir=gcode_dir + "/done",
            status_file=status_file,
            file_format="text",
            **config,
        )

    @procedure(description="Submit G-code for printing",
               estimated_duration_s=1.0)
    def print_gcode(self, gcode: str, job_name: str = "khp_job") -> dict:
        """Submit G-code content for printing."""
        filename = f"{job_name}.gcode"
        return self.submit_raw(gcode, filename)

    @procedure(description="Submit common commands (home, heat, move)",
               estimated_duration_s=1.0)
    def send_gcode_command(self, command: str = "G28") -> dict:
        """Send a single G-code command as a job."""
        return self.submit_raw(command + "\n", f"cmd_{int(time.time())}.gcode")
