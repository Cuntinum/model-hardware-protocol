"""KHP Driver — MQTT IoT Devices.

Supports any device communicating via MQTT protocol.
Covers: IoT sensors, smart home devices, industrial IoT (IIoT),
environmental monitors, plant automation, building management, etc.

Handles: Home Assistant, Zigbee2MQTT, Tasmota MQTT, ESPHome, Node-RED, etc.

Requirements:
    pip install paho-mqtt
"""

from khp import Driver, readable, writable, procedure, safety, monitor
from khp.core import ConnectionType
from typing import Any, Dict, Optional
import json
import time
import threading


class MQTTDevice(Driver):
    """Generic MQTT device driver — subscribe/publish to any MQTT topic."""

    name = "MQTT IoT Device"
    version = "1.0.0"
    device_type = "sensor"
    description = "MQTT-connected IoT device (sensors, actuators, smart home)"
    connection_type = ConnectionType.MQTT

    def __init__(self, device_id: str = None, broker: str = "localhost",
                 port: int = 1883, username: str = None, password: str = None,
                 topic_prefix: str = "khp", subscribe_topics: list = None,
                 tls: bool = False, **config):
        super().__init__(device_id=device_id, host=broker, port=port, **config)
        self._broker = broker
        self._port = port
        self._username = username
        self._password = password
        self._topic_prefix = topic_prefix
        self._subscribe_topics = subscribe_topics or [f"{topic_prefix}/#"]
        self._tls = tls
        self._client = None
        self._messages: Dict[str, Any] = {}
        self._message_history: list = []
        self._connected = False

    async def connect(self):
        import paho.mqtt.client as mqtt

        self._client = mqtt.Client(client_id=f"khp_{self.device_id}")

        if self._username:
            self._client.username_pw_set(self._username, self._password)
        if self._tls:
            self._client.tls_set()

        def on_connect(client, userdata, flags, rc):
            self._connected = rc == 0
            if self._connected:
                for topic in self._subscribe_topics:
                    client.subscribe(topic)

        def on_message(client, userdata, msg):
            try:
                payload = json.loads(msg.payload.decode())
            except (json.JSONDecodeError, UnicodeDecodeError):
                payload = msg.payload.decode(errors="replace")
            self._messages[msg.topic] = payload
            self._message_history.append({
                "topic": msg.topic,
                "payload": payload,
                "timestamp": time.time(),
            })
            if len(self._message_history) > 1000:
                self._message_history = self._message_history[-500:]

        self._client.on_connect = on_connect
        self._client.on_message = on_message
        self._client.connect(self._broker, self._port, keepalive=60)
        self._client.loop_start()

        for _ in range(50):
            if self._connected:
                break
            time.sleep(0.1)

        if self._connected:
            await super().connect()
        else:
            from khp.errors import DeviceOfflineError
            raise DeviceOfflineError(
                f"Cannot connect to MQTT broker at {self._broker}:{self._port}",
                device_id=self.device_id,
            )

    async def disconnect(self):
        if self._client:
            self._client.loop_stop()
            self._client.disconnect()
        self._client = None
        self._connected = False
        await super().disconnect()

    @readable(type="object", description="All received messages by topic")
    def all_values(self) -> dict:
        return dict(self._messages)

    @readable(type="int", description="Number of topics receiving data")
    def active_topics(self) -> int:
        return len(self._messages)

    @readable(type="array", description="Recent message history (last 20)")
    def recent_messages(self) -> list:
        return self._message_history[-20:]

    @writable(type="string", description="Publish a value to the device command topic")
    def command(self, value: str):
        topic = f"{self._topic_prefix}/command"
        self._client.publish(topic, value)

    @procedure(description="Publish a message to a specific MQTT topic",
               estimated_duration_s=0.5)
    def publish(self, topic: str, payload: Any, retain: bool = False) -> dict:
        """Publish a message to any MQTT topic."""
        if isinstance(payload, (dict, list)):
            payload = json.dumps(payload)
        result = self._client.publish(topic, str(payload), retain=retain)
        return {"topic": topic, "success": result.rc == 0}

    @procedure(description="Subscribe to additional MQTT topics",
               estimated_duration_s=0.5)
    def subscribe(self, topic: str) -> dict:
        """Subscribe to a new MQTT topic."""
        result = self._client.subscribe(topic)
        self._subscribe_topics.append(topic)
        return {"topic": topic, "subscribed": result[0] == 0}

    @procedure(description="Get the last value received on a specific topic",
               estimated_duration_s=0.1)
    def get_topic(self, topic: str) -> Any:
        """Get the last message received on a topic."""
        return self._messages.get(topic, None)

    @procedure(description="Wait for a message on a topic (with timeout)",
               estimated_duration_s=30.0)
    def wait_for_message(self, topic: str, timeout_s: float = 10.0) -> dict:
        """Block until a message arrives on the specified topic."""
        if topic not in [t for t in self._subscribe_topics]:
            self._client.subscribe(topic)
        start = time.time()
        initial_count = len(self._message_history)
        while time.time() - start < timeout_s:
            for msg in self._message_history[initial_count:]:
                if msg["topic"] == topic:
                    return {"received": True, "payload": msg["payload"]}
            time.sleep(0.1)
        return {"received": False, "timeout": True}

    @procedure(description="List all topics that have received messages",
               estimated_duration_s=0.1)
    def list_topics(self) -> list:
        """List all topics that have received at least one message."""
        return list(self._messages.keys())


class Zigbee2MQTTDevice(MQTTDevice):
    """Zigbee device via Zigbee2MQTT bridge."""

    name = "Zigbee2MQTT Device"
    version = "1.0.0"
    description = "Zigbee device controlled via Zigbee2MQTT (lights, sensors, switches)"

    def __init__(self, device_id: str = None, broker: str = "localhost",
                 friendly_name: str = "my_device", **config):
        topic_prefix = f"zigbee2mqtt/{friendly_name}"
        super().__init__(
            device_id=device_id, broker=broker,
            topic_prefix=topic_prefix,
            subscribe_topics=[topic_prefix],
            **config,
        )
        self._friendly_name = friendly_name

    @readable(type="object", description="Full device state from Zigbee2MQTT")
    def state(self) -> dict:
        topic = f"zigbee2mqtt/{self._friendly_name}"
        return self._messages.get(topic, {})

    @procedure(description="Set device state (e.g., turn on/off, brightness)",
               estimated_duration_s=1.0)
    def set_state(self, state: str = "ON", brightness: int = None,
                  color_temp: int = None) -> dict:
        """Set Zigbee device state."""
        payload = {"state": state}
        if brightness is not None:
            payload["brightness"] = brightness
        if color_temp is not None:
            payload["color_temp"] = color_temp
        topic = f"zigbee2mqtt/{self._friendly_name}/set"
        self._client.publish(topic, json.dumps(payload))
        return payload


class HomeAssistantDevice(MQTTDevice):
    """Home Assistant device via MQTT Discovery."""

    name = "Home Assistant MQTT"
    version = "1.0.0"
    description = "Home Assistant entity controlled via MQTT"

    def __init__(self, device_id: str = None, broker: str = "localhost",
                 entity_id: str = "switch.my_device", domain: str = "switch",
                 object_id: str = "my_device", **config):
        topic_prefix = f"homeassistant/{domain}/{object_id}"
        super().__init__(
            device_id=device_id, broker=broker,
            topic_prefix=topic_prefix,
            subscribe_topics=[f"{topic_prefix}/state"],
            **config,
        )
        self._entity_id = entity_id
        self._domain = domain
        self._object_id = object_id

    @readable(type="string", description="Entity state (on/off/value)")
    def entity_state(self) -> str:
        topic = f"homeassistant/{self._domain}/{self._object_id}/state"
        return str(self._messages.get(topic, "unknown"))

    @procedure(description="Send command to HA entity",
               estimated_duration_s=1.0)
    def set_entity(self, command: str = "ON") -> dict:
        """Send a command to the Home Assistant entity."""
        topic = f"homeassistant/{self._domain}/{self._object_id}/set"
        self._client.publish(topic, command)
        return {"entity": self._entity_id, "command": command}
