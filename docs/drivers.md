# Available Drivers

KHP ships with 10 ready-to-use drivers covering common hardware categories.

## Simulated (No Hardware Required)

| Driver | Class | Use Case |
|--------|-------|----------|
| Docker Simulator | `SimulatedThermocycler` | Testing without hardware |
| Docker Simulator | `SimulatedLiquidHandler` | Pipetting simulation |
| Docker Simulator | `SimulatedRoboticArm` | Motion planning tests |

Install: No extra dependencies.

## Physical Hardware

| Driver | Class | Connection | Install |
|--------|-------|------------|---------|
| Raspberry Pi | `RaspberryPiGPIO` | GPIO/I2C | `pip install khp[gpio]` |
| Arduino | `ArduinoDevice` | Serial USB | `pip install khp[serial]` |
| Serial Generic | `SerialDevice` | RS-232/485 | `pip install khp[serial]` |
| Camera | `Camera` | USB/IP/RTSP | `pip install khp[camera]` |

## Network/Protocol

| Driver | Class | Connection | Install |
|--------|-------|------------|---------|
| REST Generic | `RESTDevice` | HTTP API | `pip install khp[rest]` |
| MQTT IoT | `MQTTDevice` | MQTT broker | `pip install khp[mqtt]` |
| Modbus | `ModbusDevice` | TCP/RTU | `pip install khp[modbus]` |
| SCPI/VISA | `SCPIDevice` | GPIB/USB/LAN | `pip install khp[visa]` |
| File Drop | `FileDropDevice` | Filesystem | No extras |

## Driver Details

### Raspberry Pi GPIO

Controls GPIO pins, reads I2C sensors (BME280, etc.), accesses Pi camera.

```python
from drivers.raspberry_pi.driver import RaspberryPiGPIO

pi = RaspberryPiGPIO(device_id="rpi_lab_1")
await pi.connect()
pi.write("gpio_pin", {"pin": 17, "value": 1})
temp = pi.read("cpu_temperature")
```

### Arduino

Communicates via serial text protocol. Supports GPIO, ADC, PWM, servos.

```python
from drivers.arduino.driver import ArduinoDevice

arduino = ArduinoDevice(device_id="ard_1", port="/dev/ttyACM0", baud_rate=115200)
await arduino.connect()
pi.write("digital_pin", {"pin": 13, "value": 1})
analog = pi.read("analog_pin")
```

### SCPI/VISA Instruments

Controls lab instruments (oscilloscopes, multimeters, power supplies).

```python
from drivers.scpi_visa.driver import Oscilloscope

scope = Oscilloscope(device_id="scope_1", address="TCPIP0::192.168.1.100::INSTR")
await scope.connect()
trace = scope.read("waveform")
scope.write("timebase", 0.001)  # 1ms/div
```

### MQTT IoT

Integrates Zigbee2MQTT, Home Assistant, or any MQTT device.

```python
from drivers.mqtt_iot.driver import Zigbee2MQTTDevice

light = Zigbee2MQTTDevice(
    device_id="office_light",
    broker="192.168.1.10",
    topic_prefix="zigbee2mqtt/office_light",
)
await light.connect()
light.write("state", "ON")
light.write("brightness", 200)
```

### Camera

USB webcams, IP cameras (RTSP), with motion detection and region monitoring.

```python
from drivers.camera.driver import Camera

cam = Camera(device_id="lab_cam", source="rtsp://192.168.1.50/stream1")
await cam.connect()
frame = cam.read("frame")           # Latest frame as base64
motion = cam.read("motion_detected")  # Boolean
```

## Writing Custom Drivers

See [Writing a Driver](./writing-a-driver.md) for the full guide.
