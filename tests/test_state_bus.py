"""Tests for KHP State Bus — slots, transforms, events."""

import pytest
from khp.state_bus import StateBus, Slot, Transform


class TestSlot:
    def test_slot_write_read(self):
        slot = Slot(slot_id="temp", type="float", unit="celsius")
        slot.write(25.0)
        result = slot.read()
        assert result["value"] == 25.0
        assert result["type"] == "float"
        assert result["unit"] == "celsius"
        assert result["last_updated"] is not None

    def test_slot_history(self):
        slot = Slot(slot_id="pressure", type="float")
        for i in range(5):
            slot.write(float(i))
        history = slot.get_history(last_n=3)
        assert len(history) == 3
        assert history[-1]["value"] == 4.0

    def test_slot_history_capped(self):
        slot = Slot(slot_id="test", max_history=10)
        for i in range(20):
            slot.write(i)
        assert len(slot.history) == 10
        assert slot.history[0]["value"] == 10


class TestTransform:
    def test_threshold_above(self):
        t = Transform(
            transform_id="t1", input_slot="temp",
            operation="threshold", params={"above": 100.0}
        )
        result = t.apply(110.0)
        assert result["triggered"] is True
        assert result["direction"] == "above"

    def test_threshold_below(self):
        t = Transform(
            transform_id="t2", input_slot="temp",
            operation="threshold", params={"below": 0.0}
        )
        result = t.apply(-5.0)
        assert result["triggered"] is True
        assert result["direction"] == "below"

    def test_threshold_not_triggered(self):
        t = Transform(
            transform_id="t3", input_slot="temp",
            operation="threshold", params={"above": 100.0, "below": 0.0}
        )
        result = t.apply(50.0)
        assert result["triggered"] is False

    def test_scale(self):
        t = Transform(
            transform_id="t4", input_slot="raw",
            operation="scale", params={"factor": 2.0, "offset": 10.0}
        )
        assert t.apply(5.0) == 20.0

    def test_clamp(self):
        t = Transform(
            transform_id="t5", input_slot="val",
            operation="clamp", params={"min": 0.0, "max": 100.0}
        )
        assert t.apply(150.0) == 100.0
        assert t.apply(-10.0) == 0.0
        assert t.apply(50.0) == 50.0


class TestStateBus:
    def test_create_and_read_slot(self, bus):
        bus.create_slot("temp", type="float", unit="celsius")
        bus.write_slot("temp", 25.0)
        result = bus.read_slot("temp")
        assert result["value"] == 25.0

    def test_auto_create_slot(self, bus):
        bus.write_slot("new_slot", 42)
        result = bus.read_slot("new_slot")
        assert result["value"] == 42

    def test_read_nonexistent_returns_none(self, bus):
        assert bus.read_slot("nope") is None

    def test_list_slots(self, bus):
        bus.create_slot("a")
        bus.create_slot("b")
        slots = bus.list_slots()
        assert len(slots) == 2

    def test_transform_triggers_output_slot(self, bus):
        bus.create_slot("raw_temp")
        bus.create_slot("scaled_temp")
        bus.add_transform(Transform(
            transform_id="scale_temp",
            input_slot="raw_temp",
            operation="scale",
            params={"factor": 1.8, "offset": 32.0},
            output_slot="scaled_temp",
        ))
        bus.write_slot("raw_temp", 100.0)
        result = bus.read_slot("scaled_temp")
        assert abs(result["value"] - 212.0) < 0.01

    def test_transform_emits_event(self, bus):
        events = []
        bus.create_slot("pressure")
        bus.add_transform(Transform(
            transform_id="alert_high",
            input_slot="pressure",
            operation="threshold",
            params={"above": 100.0},
            output_event="pressure_alarm",
        ))
        bus.on("pressure_alarm", lambda e: events.append(e))
        bus.write_slot("pressure", 50.0)
        assert len(events) == 0
        bus.write_slot("pressure", 110.0)
        assert len(events) == 1
        assert events[0]["triggered"] is True

    def test_wildcard_event_handler(self, bus):
        events = []
        bus.on("*", lambda e: events.append(e))
        bus.create_slot("x")
        bus.write_slot("x", 1)
        assert len(events) > 0
        assert events[0]["event"] == "slot_updated"

    def test_list_transforms(self, bus):
        bus.add_transform(Transform(
            transform_id="t1", input_slot="a", operation="scale", params={"factor": 2}
        ))
        transforms = bus.list_transforms()
        assert len(transforms) == 1
        assert transforms[0]["transform_id"] == "t1"
