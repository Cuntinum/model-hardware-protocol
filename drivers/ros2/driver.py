"""KHP Driver: ROS 2 Bridge.

Bridges ROS 2 topics, services, and actions into KHP primitives.
Enables AI agents to control mobile robots, manipulators, drones,
and autonomous vehicles through the Model Hardware Protocol.

Requirements:
    pip install rclpy sensor-msgs geometry-msgs std-srvs
    (Requires a ROS 2 installation: Humble, Iron, or Jazzy)
"""

from khp import Driver, readable, writable, procedure, safety, monitor
from khp.core import ConnectionType
from typing import Dict, List, Optional, Any
import time
import threading


class ROS2BridgeDevice(Driver):
    """ROS 2 Bridge driver: exposes topics, services, and actions as KHP primitives."""

    name = "ROS 2 Bridge"
    version = "1.0.0"
    device_type = "robot"
    description = "Bridge between KHP and ROS 2 (DDS). Controls robots, drones, and autonomous systems."
    connection_type = ConnectionType.SDK

    def __init__(self, device_id: str = None, node_name: str = "khp_bridge",
                 namespace: str = "", cmd_vel_topic: str = "/cmd_vel",
                 odom_topic: str = "/odom", joint_state_topic: str = "/joint_states",
                 max_linear_vel: float = 2.0, max_angular_vel: float = 3.14,
                 workspace_bounds: Dict = None, **config):
        super().__init__(device_id=device_id, node_name=node_name, **config)
        self._node_name = node_name
        self._namespace = namespace
        self._cmd_vel_topic = cmd_vel_topic
        self._odom_topic = odom_topic
        self._joint_state_topic = joint_state_topic
        self._max_linear_vel = max_linear_vel
        self._max_angular_vel = max_angular_vel
        self._workspace_bounds = workspace_bounds or {
            "x_min": -10.0, "x_max": 10.0,
            "y_min": -10.0, "y_max": 10.0,
            "z_min": 0.0, "z_max": 5.0,
        }

        self._node = None
        self._executor = None
        self._spin_thread = None

        self._latest_odom = {"x": 0.0, "y": 0.0, "z": 0.0, "vx": 0.0, "vy": 0.0, "yaw": 0.0}
        self._latest_joint_states = {"names": [], "positions": [], "velocities": [], "efforts": []}
        self._latest_scan = {"ranges": [], "angle_min": 0.0, "angle_max": 0.0, "range_max": 0.0}
        self._battery_level = 100.0
        self._estop_active = False

        self._cmd_vel_pub = None
        self._joint_cmd_pub = None
        self._estop_pub = None

    async def connect(self):
        import rclpy
        from rclpy.node import Node
        from rclpy.executors import MultiThreadedExecutor

        if not rclpy.ok():
            rclpy.init()

        self._node = rclpy.create_node(self._node_name, namespace=self._namespace)

        self._setup_subscribers()
        self._setup_publishers()

        self._executor = MultiThreadedExecutor()
        self._executor.add_node(self._node)
        self._spin_thread = threading.Thread(target=self._executor.spin, daemon=True)
        self._spin_thread.start()

        time.sleep(0.5)
        await super().connect()

    def _setup_subscribers(self):
        from nav_msgs.msg import Odometry
        from sensor_msgs.msg import JointState, LaserScan, BatteryState

        self._node.create_subscription(Odometry, self._odom_topic, self._odom_callback, 10)
        self._node.create_subscription(JointState, self._joint_state_topic, self._joint_callback, 10)
        self._node.create_subscription(LaserScan, "/scan", self._scan_callback, 10)
        self._node.create_subscription(BatteryState, "/battery_state", self._battery_callback, 10)

    def _setup_publishers(self):
        from geometry_msgs.msg import Twist
        from std_msgs.msg import Bool
        from trajectory_msgs.msg import JointTrajectory

        self._cmd_vel_pub = self._node.create_publisher(Twist, self._cmd_vel_topic, 10)
        self._joint_cmd_pub = self._node.create_publisher(
            JointTrajectory, "/joint_trajectory_controller/command", 10
        )
        self._estop_pub = self._node.create_publisher(Bool, "/emergency_stop", 10)

    def _odom_callback(self, msg):
        pos = msg.pose.pose.position
        vel = msg.twist.twist.linear
        self._latest_odom = {
            "x": pos.x, "y": pos.y, "z": pos.z,
            "vx": vel.x, "vy": vel.y, "yaw": msg.twist.twist.angular.z,
        }

    def _joint_callback(self, msg):
        self._latest_joint_states = {
            "names": list(msg.name),
            "positions": list(msg.position),
            "velocities": list(msg.velocity),
            "efforts": list(msg.effort),
        }

    def _scan_callback(self, msg):
        self._latest_scan = {
            "ranges": list(msg.ranges[:36]),
            "angle_min": msg.angle_min,
            "angle_max": msg.angle_max,
            "range_max": msg.range_max,
        }

    def _battery_callback(self, msg):
        self._battery_level = msg.percentage * 100.0

    async def disconnect(self):
        if self._executor:
            self._executor.shutdown()
        if self._node:
            self._node.destroy_node()
        self._node = None
        await super().disconnect()

    @readable(type="object", description="Current robot pose (x, y, z) and velocity from odometry", unit="meters")
    def odometry(self) -> Dict:
        return self._latest_odom

    @readable(type="object", description="Current joint states: positions, velocities, and efforts")
    def joint_states(self) -> Dict:
        return self._latest_joint_states

    @readable(type="object", description="LIDAR scan summary: nearest obstacles in 36 angular sectors", unit="meters")
    def lidar_scan(self) -> Dict:
        return self._latest_scan

    @readable(type="float", description="Battery level percentage", unit="percent")
    def battery_level(self) -> float:
        return self._battery_level

    @readable(type="bool", description="Whether emergency stop is currently active")
    def estop_status(self) -> bool:
        return self._estop_active

    @monitor(interval_s=1.0, alert_below=10.0)
    def battery_monitor(self) -> float:
        return self._battery_level

    @safety(max=2.0, min=-2.0, reason="Linear velocity must stay within safe operating range", hard=True)
    @writable(type="float", description="Commanded linear velocity (forward/backward)", unit="m/s")
    def linear_velocity(self, value: float):
        if self._estop_active:
            from khp.errors import SafetyBlockedError
            raise SafetyBlockedError(
                "Cannot command velocity while emergency stop is active",
                device_id=self.device_id,
                property_name="linear_velocity",
                attempted_value=value,
                limit={"estop": True},
            )
        self._publish_cmd_vel(linear_x=value)

    @safety(max=3.14, min=-3.14, reason="Angular velocity limited to safe turning rate", hard=True)
    @writable(type="float", description="Commanded angular velocity (rotation)", unit="rad/s")
    def angular_velocity(self, value: float):
        if self._estop_active:
            from khp.errors import SafetyBlockedError
            raise SafetyBlockedError(
                "Cannot command velocity while emergency stop is active",
                device_id=self.device_id,
                property_name="angular_velocity",
                attempted_value=value,
                limit={"estop": True},
            )
        self._publish_cmd_vel(angular_z=value)

    def _publish_cmd_vel(self, linear_x: float = 0.0, linear_y: float = 0.0, angular_z: float = 0.0):
        from geometry_msgs.msg import Twist

        msg = Twist()
        msg.linear.x = max(-self._max_linear_vel, min(self._max_linear_vel, linear_x))
        msg.linear.y = max(-self._max_linear_vel, min(self._max_linear_vel, linear_y))
        msg.angular.z = max(-self._max_angular_vel, min(self._max_angular_vel, angular_z))
        self._cmd_vel_pub.publish(msg)

    @procedure(description="Navigate to a target pose in the map frame using Nav2", estimated_duration_s=60)
    async def navigate_to_pose(self, x: float, y: float, yaw: float = 0.0):
        bounds = self._workspace_bounds
        if not (bounds["x_min"] <= x <= bounds["x_max"]):
            from khp.errors import SafetyBlockedError
            raise SafetyBlockedError(
                f"Target x={x} is outside workspace bounds",
                device_id=self.device_id,
                property_name="navigate_to_pose",
                attempted_value=x,
                limit={"x_min": bounds["x_min"], "x_max": bounds["x_max"]},
            )
        if not (bounds["y_min"] <= y <= bounds["y_max"]):
            from khp.errors import SafetyBlockedError
            raise SafetyBlockedError(
                f"Target y={y} is outside workspace bounds",
                device_id=self.device_id,
                property_name="navigate_to_pose",
                attempted_value=y,
                limit={"y_min": bounds["y_min"], "y_max": bounds["y_max"]},
            )

        from geometry_msgs.msg import PoseStamped
        from rclpy.action import ActionClient
        from nav2_msgs.action import NavigateToPose

        action_client = ActionClient(self._node, NavigateToPose, "/navigate_to_pose")
        if not action_client.wait_for_server(timeout_sec=5.0):
            return {"status": "failed", "reason": "Nav2 action server not available"}

        goal = NavigateToPose.Goal()
        goal.pose = PoseStamped()
        goal.pose.header.frame_id = "map"
        goal.pose.pose.position.x = x
        goal.pose.pose.position.y = y
        goal.pose.pose.orientation.z = yaw

        future = action_client.send_goal_async(goal)
        return {"status": "completed", "target": {"x": x, "y": y, "yaw": yaw}}

    @procedure(description="Send joint trajectory command to move robot arm joints")
    async def move_joints(self, joint_names: List[str], positions: List[float], duration_s: float = 2.0):
        from trajectory_msgs.msg import JointTrajectory, JointTrajectoryPoint
        from builtin_interfaces.msg import Duration

        msg = JointTrajectory()
        msg.joint_names = joint_names

        point = JointTrajectoryPoint()
        point.positions = positions
        point.time_from_start = Duration(sec=int(duration_s), nanosec=0)
        msg.points = [point]

        self._joint_cmd_pub.publish(msg)
        return {"status": "completed", "joints": joint_names, "positions": positions}

    @procedure(description="Call a ROS 2 service by name with parameters")
    async def call_service(self, service_name: str, service_type: str, request_data: Dict = None):
        import importlib

        parts = service_type.rsplit(".", 1)
        module = importlib.import_module(parts[0])
        srv_class = getattr(module, parts[1])

        client = self._node.create_client(srv_class, service_name)
        if not client.wait_for_service(timeout_sec=5.0):
            return {"status": "failed", "reason": f"Service {service_name} not available"}

        request = srv_class.Request()
        if request_data:
            for key, value in request_data.items():
                setattr(request, key, value)

        future = client.call_async(request)
        return {"status": "completed", "service": service_name}

    @procedure(description="Stop all motion immediately and publish emergency stop")
    async def emergency_stop(self):
        from geometry_msgs.msg import Twist
        from std_msgs.msg import Bool

        stop_msg = Twist()
        self._cmd_vel_pub.publish(stop_msg)

        estop_msg = Bool()
        estop_msg.data = True
        self._estop_pub.publish(estop_msg)

        self._estop_active = True
        self.status = "error"
        return {"status": "completed", "action": "emergency_stop_activated"}

    @procedure(description="Release emergency stop and allow motion commands again")
    async def release_estop(self):
        from std_msgs.msg import Bool

        estop_msg = Bool()
        estop_msg.data = False
        self._estop_pub.publish(estop_msg)

        self._estop_active = False
        self.status = "online"
        return {"status": "completed", "action": "emergency_stop_released"}

    @procedure(description="List all active ROS 2 topics visible to this node")
    async def list_topics(self):
        topics = self._node.get_topic_names_and_types()
        return {
            "status": "completed",
            "topics": [{"name": name, "types": types} for name, types in topics],
            "count": len(topics),
        }

    @procedure(description="Set a ROS 2 parameter on a remote node")
    async def set_parameter(self, node_name: str, param_name: str, param_value: Any):
        from rcl_interfaces.srv import SetParameters
        from rcl_interfaces.msg import Parameter, ParameterValue, ParameterType

        client = self._node.create_client(SetParameters, f"/{node_name}/set_parameters")
        if not client.wait_for_service(timeout_sec=3.0):
            return {"status": "failed", "reason": f"Node {node_name} parameter service unavailable"}

        param = Parameter()
        param.name = param_name
        param.value = ParameterValue()

        if isinstance(param_value, bool):
            param.value.type = ParameterType.PARAMETER_BOOL
            param.value.bool_value = param_value
        elif isinstance(param_value, int):
            param.value.type = ParameterType.PARAMETER_INTEGER
            param.value.integer_value = param_value
        elif isinstance(param_value, float):
            param.value.type = ParameterType.PARAMETER_DOUBLE
            param.value.double_value = param_value
        elif isinstance(param_value, str):
            param.value.type = ParameterType.PARAMETER_STRING
            param.value.string_value = param_value

        request = SetParameters.Request()
        request.parameters = [param]
        future = client.call_async(request)
        return {"status": "completed", "node": node_name, "param": param_name, "value": param_value}
