"""Kinetic Hardware Protocol (KHP) — Python SDK.

Build drivers for physical hardware that any AI agent can control.

Usage:
    from khp import Driver, readable, writable, procedure, safety
    from khp import StateBus, Slot, discover

"""

__version__ = "0.1.0"

from khp.core import Driver, DeviceStatus, ConnectionType
from khp.decorators import readable, writable, procedure, safety, monitor
from khp.state_bus import StateBus, Slot, Transform
from khp.discovery import discover, register, deregister
from khp.manifest import Manifest, generate_manifest
from khp.errors import (
    KHPError,
    DeviceNotFoundError,
    PropertyNotFoundError,
    SafetyBlockedError,
    SafetyClampedError,
    PreconditionFailedError,
    ConfirmationRequiredError,
    DeviceBusyError,
    DeviceOfflineError,
    TimeoutError,
    HardwareError,
)

__all__ = [
    "Driver",
    "DeviceStatus",
    "ConnectionType",
    "readable",
    "writable",
    "procedure",
    "safety",
    "monitor",
    "StateBus",
    "Slot",
    "Transform",
    "discover",
    "register",
    "deregister",
    "Manifest",
    "generate_manifest",
    "KHPError",
    "DeviceNotFoundError",
    "PropertyNotFoundError",
    "SafetyBlockedError",
    "SafetyClampedError",
    "PreconditionFailedError",
    "ConfirmationRequiredError",
    "DeviceBusyError",
    "DeviceOfflineError",
    "TimeoutError",
    "HardwareError",
]
