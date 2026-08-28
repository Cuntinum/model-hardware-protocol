"""KHP decorators — mark methods as readable, writable, procedure, or safety-constrained."""

import functools
import inspect
from typing import Any, Callable, List, Optional
from khp.core import PropertyMeta, ProcedureMeta, SafetyLimit


def readable(
    type: str = "float",
    description: str = "",
    unit: Optional[str] = None,
    min_value: Optional[float] = None,
    max_value: Optional[float] = None,
    poll_interval_ms: Optional[int] = None,
):
    """Mark a method as a readable property.

    Usage:
        @readable(type="float", description="Current temperature", unit="celsius")
        def temperature(self) -> float:
            return self.device.read_temp()
    """
    def decorator(func: Callable) -> Callable:
        meta = PropertyMeta(
            name=func.__name__,
            type=type,
            description=description or func.__doc__ or "",
            unit=unit,
            min_value=min_value,
            max_value=max_value,
            poll_interval_ms=poll_interval_ms,
        )
        func._khp_readable = meta

        @functools.wraps(func)
        def wrapper(self, *args, **kwargs):
            return func(self, *args, **kwargs)
        wrapper._khp_readable = meta
        return wrapper
    return decorator


def writable(
    type: str = "float",
    description: str = "",
    unit: Optional[str] = None,
    min_value: Optional[float] = None,
    max_value: Optional[float] = None,
    step: Optional[float] = None,
    enum_values: Optional[List[str]] = None,
    pattern: Optional[str] = None,
    default: Any = None,
    requires_confirmation: bool = False,
):
    """Mark a method as a writable property.

    Usage:
        @writable(type="float", description="Target temperature", unit="celsius", min_value=4, max_value=100)
        def temperature(self, value: float):
            self.device.set_temp(value)
    """
    def decorator(func: Callable) -> Callable:
        meta = PropertyMeta(
            name=func.__name__,
            type=type,
            description=description or func.__doc__ or "",
            unit=unit,
            min_value=min_value,
            max_value=max_value,
            step=step,
            enum_values=enum_values,
            pattern=pattern,
            default=default,
            requires_confirmation=requires_confirmation,
        )
        func._khp_writable = meta

        @functools.wraps(func)
        def wrapper(self, *args, **kwargs):
            return func(self, *args, **kwargs)
        wrapper._khp_writable = meta
        return wrapper
    return decorator


def procedure(
    description: str = "",
    params: dict = None,
    preconditions: List[str] = None,
    postconditions: List[str] = None,
    estimated_duration_s: Optional[float] = None,
    requires_confirmation: bool = False,
    idempotent: bool = False,
    reversible: bool = False,
):
    """Mark a method as an executable procedure.

    Usage:
        @procedure(description="Aspirate liquid", estimated_duration_s=5, preconditions=["tip_attached"])
        def aspirate(self, volume_ul: float, speed: str = "normal"):
            ...
    """
    def decorator(func: Callable) -> Callable:
        sig = inspect.signature(func)
        auto_params = {}
        for param_name, param in sig.parameters.items():
            if param_name == "self":
                continue
            ptype = "string"
            if param.annotation != inspect.Parameter.empty:
                ann = param.annotation
                if ann == float:
                    ptype = "float"
                elif ann == int:
                    ptype = "int"
                elif ann == bool:
                    ptype = "bool"
            auto_params[param_name] = {
                "type": ptype,
                "required": param.default == inspect.Parameter.empty,
                "default": None if param.default == inspect.Parameter.empty else param.default,
            }

        meta = ProcedureMeta(
            name=func.__name__,
            description=description or func.__doc__ or "",
            params=params or auto_params,
            preconditions=preconditions or [],
            postconditions=postconditions or [],
            estimated_duration_s=estimated_duration_s,
            requires_confirmation=requires_confirmation,
            idempotent=idempotent,
            reversible=reversible,
        )
        func._khp_procedure = meta

        @functools.wraps(func)
        def wrapper(self, *args, **kwargs):
            return func(self, *args, **kwargs)
        wrapper._khp_procedure = meta
        return wrapper
    return decorator


def safety(
    min: Optional[float] = None,
    max: Optional[float] = None,
    reason: str = "",
    hard: bool = True,
    require_confirmation: bool = False,
):
    """Attach safety limits to a writable property or procedure.

    Usage:
        @safety(max=200.0, reason="Prevents sample bleaching")
        @writable(type="float", unit="mW")
        def laser_power(self, value: float):
            ...
    """
    def decorator(func: Callable) -> Callable:
        if not hasattr(func, "_khp_safety"):
            func._khp_safety = []

        limit = SafetyLimit(
            property_name=func.__name__,
            min_value=min,
            max_value=max,
            reason=reason,
            hard=hard,
        )
        func._khp_safety.append(limit)

        if require_confirmation:
            if hasattr(func, "_khp_writable"):
                func._khp_writable.requires_confirmation = True
            if hasattr(func, "_khp_procedure"):
                func._khp_procedure.requires_confirmation = True

        @functools.wraps(func)
        def wrapper(self, *args, **kwargs):
            return func(self, *args, **kwargs)
        wrapper._khp_safety = func._khp_safety
        if hasattr(func, "_khp_readable"):
            wrapper._khp_readable = func._khp_readable
        if hasattr(func, "_khp_writable"):
            wrapper._khp_writable = func._khp_writable
        if hasattr(func, "_khp_procedure"):
            wrapper._khp_procedure = func._khp_procedure
        return wrapper
    return decorator


def monitor(interval_ms: int = 1000, alert_above: float = None,
            alert_below: float = None, action: str = "emit_event"):
    """Mark a readable property for continuous monitoring.

    Usage:
        @monitor(interval_ms=500, alert_above=95.0, action="emergency_stop")
        @readable(type="float", unit="celsius")
        def temperature(self) -> float:
            ...
    """
    def decorator(func: Callable) -> Callable:
        func._khp_monitor = {
            "interval_ms": interval_ms,
            "alert_above": alert_above,
            "alert_below": alert_below,
            "action": action,
        }

        @functools.wraps(func)
        def wrapper(self, *args, **kwargs):
            return func(self, *args, **kwargs)
        wrapper._khp_monitor = func._khp_monitor
        if hasattr(func, "_khp_readable"):
            wrapper._khp_readable = func._khp_readable
        return wrapper
    return decorator
