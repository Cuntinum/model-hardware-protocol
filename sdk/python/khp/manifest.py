"""KHP Manifest utilities — generate, validate, and load manifests."""

import json
from pathlib import Path
from typing import Optional
from khp.core import Driver


class Manifest:
    """Parsed capabilities manifest."""

    def __init__(self, data: dict):
        self.data = data
        self.device_id = data.get("device_id", "")
        self.name = data.get("name", "Unknown")
        self.device_type = data.get("type", "custom")
        self.version = data.get("version", "0.0.0")
        self.description = data.get("description", "")
        self.readable = data.get("readable", {})
        self.writable = data.get("writable", {})
        self.procedures = data.get("procedures", {})
        self.safety = data.get("safety", {})
        self.metadata = data.get("metadata", {})

    @classmethod
    def from_file(cls, path: str) -> "Manifest":
        """Load manifest from a JSON file."""
        with open(path) as f:
            return cls(json.load(f))

    @classmethod
    def from_driver(cls, driver: Driver) -> "Manifest":
        """Generate manifest from a live driver."""
        return cls(driver.get_manifest())

    def to_file(self, path: str):
        """Save manifest to a JSON file."""
        with open(path, "w") as f:
            json.dump(self.data, f, indent=2)

    def validate(self) -> list:
        """Validate manifest against schema. Returns list of errors."""
        errors = []
        if not self.device_id:
            errors.append("Missing required field: device_id")
        if not self.name:
            errors.append("Missing required field: name")
        if not self.device_type:
            errors.append("Missing required field: type")

        for prop_name, prop in self.writable.items():
            if "type" not in prop:
                errors.append(f"Writable '{prop_name}' missing type")

        for proc_name, proc in self.procedures.items():
            if "description" not in proc and "params" not in proc:
                errors.append(f"Procedure '{proc_name}' needs description or params")

        hard_limits = self.safety.get("hard_limits", {})
        for limit_name, limit in hard_limits.items():
            if "property" not in limit:
                errors.append(f"Hard limit '{limit_name}' missing property reference")
            if limit.get("max") is None and limit.get("min") is None:
                errors.append(f"Hard limit '{limit_name}' must have min or max")

        return errors

    @property
    def all_capabilities(self) -> set:
        """All capabilities (readable + writable + procedures)."""
        return set(self.readable.keys()) | set(self.writable.keys()) | set(self.procedures.keys())

    def has_capability(self, name: str) -> bool:
        """Check if device has a specific capability."""
        return name in self.all_capabilities

    def get_safety_limits_for(self, property_name: str) -> dict:
        """Get all safety limits for a specific property."""
        limits = {}
        for limit_name, limit in self.safety.get("hard_limits", {}).items():
            if limit.get("property") == property_name:
                limits["hard"] = limit
        for limit_name, limit in self.safety.get("soft_limits", {}).items():
            if limit.get("property") == property_name:
                limits["soft"] = limit
        return limits

    def __repr__(self):
        return f"<Manifest {self.device_id}: {self.name} ({self.device_type})>"


def generate_manifest(driver: Driver, output_path: Optional[str] = None) -> dict:
    """Generate a manifest from a driver instance and optionally save to file."""
    manifest_data = driver.get_manifest()
    if output_path:
        with open(output_path, "w") as f:
            json.dump(manifest_data, f, indent=2)
    return manifest_data
