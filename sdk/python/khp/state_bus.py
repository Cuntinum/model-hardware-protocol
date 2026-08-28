"""KHP State Bus — shared data layer for inter-device and agent communication."""

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional
from datetime import datetime, timezone


@dataclass
class Slot:
    """A named, typed data channel in the State Bus."""
    slot_id: str
    type: str = "float"
    unit: Optional[str] = None
    retention_s: int = 3600
    value: Any = None
    last_updated: Optional[str] = None
    history: List[dict] = field(default_factory=list)
    subscribers: List[str] = field(default_factory=list)
    max_history: int = 1000

    def write(self, value: Any):
        """Write a new value to this slot."""
        self.value = value
        self.last_updated = datetime.now(timezone.utc).isoformat()
        entry = {"value": value, "timestamp": self.last_updated}
        self.history.append(entry)
        if len(self.history) > self.max_history:
            self.history = self.history[-self.max_history:]

    def read(self) -> dict:
        """Read current value from this slot."""
        return {
            "slot_id": self.slot_id,
            "value": self.value,
            "type": self.type,
            "unit": self.unit,
            "last_updated": self.last_updated,
        }

    def get_history(self, last_n: int = 100) -> List[dict]:
        """Get recent history entries."""
        return self.history[-last_n:]


@dataclass
class Transform:
    """A composable data operation on slots."""
    transform_id: str
    input_slot: str
    operation: str
    params: dict = field(default_factory=dict)
    output_slot: Optional[str] = None
    output_event: Optional[str] = None

    def apply(self, value: Any) -> Any:
        """Apply this transform to a value."""
        if self.operation == "threshold":
            above = self.params.get("above")
            below = self.params.get("below")
            if above is not None and value > above:
                return {"triggered": True, "direction": "above", "value": value}
            if below is not None and value < below:
                return {"triggered": True, "direction": "below", "value": value}
            return {"triggered": False, "value": value}

        elif self.operation == "scale":
            factor = self.params.get("factor", 1.0)
            offset = self.params.get("offset", 0.0)
            return value * factor + offset

        elif self.operation == "moving_average":
            window = self.params.get("window", 10)
            return value  # Needs history context

        elif self.operation == "delta":
            return value  # Needs previous value

        elif self.operation == "clamp":
            min_v = self.params.get("min", float("-inf"))
            max_v = self.params.get("max", float("inf"))
            return max(min_v, min(max_v, value))

        return value


class StateBus:
    """Shared state bus — the communication backbone between devices and agents."""

    def __init__(self):
        self._slots: Dict[str, Slot] = {}
        self._transforms: Dict[str, Transform] = {}
        self._event_handlers: Dict[str, List[Callable]] = {}
        self._running = False

    def create_slot(self, slot_id: str, type: str = "float",
                    unit: str = None, retention_s: int = 3600) -> Slot:
        """Create a new data slot."""
        slot = Slot(slot_id=slot_id, type=type, unit=unit, retention_s=retention_s)
        self._slots[slot_id] = slot
        return slot

    def get_slot(self, slot_id: str) -> Optional[Slot]:
        """Get a slot by ID."""
        return self._slots.get(slot_id)

    def write_slot(self, slot_id: str, value: Any):
        """Write value to a slot and trigger any transforms."""
        slot = self._slots.get(slot_id)
        if slot is None:
            slot = self.create_slot(slot_id)
        slot.write(value)
        self._apply_transforms(slot_id, value)
        self._emit("slot_updated", {"slot_id": slot_id, "value": value})

    def read_slot(self, slot_id: str) -> Optional[dict]:
        """Read current value from a slot."""
        slot = self._slots.get(slot_id)
        if slot is None:
            return None
        return slot.read()

    def add_transform(self, transform: Transform):
        """Register a transform on the bus."""
        self._transforms[transform.transform_id] = transform

    def _apply_transforms(self, slot_id: str, value: Any):
        """Apply all transforms that watch this slot."""
        for t in self._transforms.values():
            if t.input_slot != slot_id:
                continue
            result = t.apply(value)
            if t.output_slot:
                self.write_slot(t.output_slot, result)
            if t.output_event:
                if isinstance(result, dict) and result.get("triggered"):
                    self._emit(t.output_event, result)

    def on(self, event: str, handler: Callable):
        """Subscribe to a bus event."""
        if event not in self._event_handlers:
            self._event_handlers[event] = []
        self._event_handlers[event].append(handler)

    def _emit(self, event: str, data: dict):
        """Emit an event to subscribers."""
        payload = {
            "event": event,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **data,
        }
        for handler in self._event_handlers.get(event, []):
            handler(payload)
        for handler in self._event_handlers.get("*", []):
            handler(payload)

    def list_slots(self) -> List[dict]:
        """List all active slots."""
        return [slot.read() for slot in self._slots.values()]

    def list_transforms(self) -> List[dict]:
        """List all registered transforms."""
        return [
            {
                "transform_id": t.transform_id,
                "input_slot": t.input_slot,
                "operation": t.operation,
                "output_slot": t.output_slot,
                "output_event": t.output_event,
            }
            for t in self._transforms.values()
        ]
