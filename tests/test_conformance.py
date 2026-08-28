"""KHP Driver Conformance Test Suite.

Validates that a driver implementation meets the KHP specification.
Checks for proper decorator usage, safety limits, manifest completeness,
error handling, and protocol compliance.

Usage:
    pytest tests/test_conformance.py --driver-path drivers/universal_robots
    # or programmatically:
    from tests.test_conformance import ConformanceRunner
    runner = ConformanceRunner("drivers.universal_robots")
    results = runner.run_all()
"""
from __future__ import annotations

import ast
import sys
import inspect
import importlib
from pathlib import Path
from typing import Any


class ConformanceResult:
    """Single conformance check result."""

    def __init__(self, check_id: str, title: str, passed: bool,
                 message: str = "", severity: str = "required"):
        self.check_id = check_id
        self.title = title
        self.passed = passed
        self.message = message
        self.severity = severity

    def to_dict(self) -> dict:
        return {
            "check_id": self.check_id,
            "title": self.title,
            "passed": self.passed,
            "message": self.message,
            "severity": self.severity,
        }


class ConformanceRunner:
    """Runs the full KHP conformance test suite against a driver module."""

    def __init__(self, module_path: str):
        self.module_path = module_path
        self.module = None
        self.driver_class = None
        self.source_code = ""
        self.results: list[ConformanceResult] = []

    def run_all(self) -> list[ConformanceResult]:
        """Run all conformance checks and return results."""
        self.results = []

        self._check_module_loads()
        if not self.module:
            return self.results

        self._check_driver_class_exists()
        if not self.driver_class:
            return self.results

        self._check_required_attributes()
        self._check_decorator_usage()
        self._check_safety_limits()
        self._check_connect_disconnect()
        self._check_monitor_function()
        self._check_error_handling()
        self._check_type_annotations()
        self._check_docstrings()
        self._check_no_blocking_in_async()
        self._check_init_params()
        self._check_manifest_completeness()

        return self.results

    def _add(self, check_id: str, title: str, passed: bool,
             message: str = "", severity: str = "required"):
        self.results.append(ConformanceResult(check_id, title, passed, message, severity))

    def _check_module_loads(self):
        """CONF-001: Module must import without errors."""
        try:
            self.module = importlib.import_module(self.module_path)
            self._add("CONF-001", "Module imports without error", True)
        except Exception as e:
            self._add("CONF-001", "Module imports without error", False, str(e))

    def _check_driver_class_exists(self):
        """CONF-002: Module must export exactly one Driver subclass."""
        driver_classes = []
        for name, obj in inspect.getmembers(self.module, inspect.isclass):
            if hasattr(obj, "name") and hasattr(obj, "device_type") and obj.__module__ == self.module.__name__:
                driver_classes.append(obj)

        if len(driver_classes) == 1:
            self.driver_class = driver_classes[0]
            self._add("CONF-002", "Single driver class exported", True)
        elif len(driver_classes) == 0:
            self._add("CONF-002", "Single driver class exported", False,
                      "No class with 'name' and 'device_type' attributes found")
        else:
            self.driver_class = driver_classes[0]
            self._add("CONF-002", "Single driver class exported", False,
                      f"Multiple driver classes found: {[c.__name__ for c in driver_classes]}",
                      severity="warning")

    def _check_required_attributes(self):
        """CONF-003: Driver must have name, version, device_type, description."""
        required = ["name", "version", "device_type", "description"]
        missing = []
        for attr in required:
            if not hasattr(self.driver_class, attr):
                missing.append(attr)
            elif not getattr(self.driver_class, attr):
                missing.append(f"{attr} (empty)")

        if not missing:
            self._add("CONF-003", "Required class attributes present", True)
        else:
            self._add("CONF-003", "Required class attributes present", False,
                      f"Missing: {missing}")

    def _check_decorator_usage(self):
        """CONF-004: Must have at least one @readable and one @procedure."""
        has_readable = False
        has_writable = False
        has_procedure = False

        for name in dir(self.driver_class):
            if name.startswith("_"):
                continue
            attr = getattr(self.driver_class, name, None)
            if attr is None:
                continue
            if hasattr(attr, "_khp_readable"):
                has_readable = True
            if hasattr(attr, "_khp_writable"):
                has_writable = True
            if hasattr(attr, "_khp_procedure"):
                has_procedure = True

        self._add("CONF-004a", "Has @readable properties", has_readable,
                  "" if has_readable else "No @readable decorated methods found")
        self._add("CONF-004b", "Has @procedure methods", has_procedure,
                  "" if has_procedure else "No @procedure decorated methods found")
        self._add("CONF-004c", "Has @writable properties", has_writable,
                  "" if has_writable else "No @writable decorated methods (optional but recommended)",
                  severity="recommended")

    def _check_safety_limits(self):
        """CONF-005: Writable properties should have safety decorators."""
        writable_count = 0
        safety_count = 0

        for name in dir(self.driver_class):
            attr = getattr(self.driver_class, name, None)
            if attr and hasattr(attr, "_khp_writable"):
                writable_count += 1
                if hasattr(attr, "_khp_safety"):
                    safety_count += 1

        if writable_count == 0:
            self._add("CONF-005", "Safety limits on writable properties", True,
                      "No writable properties (N/A)")
        elif safety_count == writable_count:
            self._add("CONF-005", "Safety limits on writable properties", True,
                      f"All {writable_count} writable properties have safety limits")
        else:
            ratio = safety_count / writable_count
            self._add("CONF-005", "Safety limits on writable properties",
                      ratio >= 0.5,
                      f"{safety_count}/{writable_count} have safety limits",
                      severity="recommended")

    def _check_connect_disconnect(self):
        """CONF-006: Must implement connect() and disconnect()."""
        has_connect = hasattr(self.driver_class, "connect") and callable(getattr(self.driver_class, "connect"))
        has_disconnect = hasattr(self.driver_class, "disconnect") and callable(getattr(self.driver_class, "disconnect"))

        self._add("CONF-006a", "Implements connect()", has_connect)
        self._add("CONF-006b", "Implements disconnect()", has_disconnect)

        if has_connect:
            connect_method = getattr(self.driver_class, "connect")
            is_async = asyncio.iscoroutinefunction(connect_method) if "asyncio" in sys.modules else inspect.iscoroutinefunction(connect_method)
            self._add("CONF-006c", "connect() is async", is_async,
                      "" if is_async else "connect() should be async for non-blocking operation",
                      severity="recommended")

    def _check_monitor_function(self):
        """CONF-007: Should have at least one @monitor decorated method."""
        has_monitor = False
        for name in dir(self.driver_class):
            attr = getattr(self.driver_class, name, None)
            if attr and hasattr(attr, "_khp_monitor"):
                has_monitor = True
                break

        self._add("CONF-007", "Has @monitor health check", has_monitor,
                  "" if has_monitor else "No @monitor method found. Recommended for health checking.",
                  severity="recommended")

    def _check_error_handling(self):
        """CONF-008: Source should import and use KHP error types."""
        try:
            source_file = inspect.getfile(self.driver_class)
            with open(source_file, encoding="utf-8") as f:
                source = f.read()
            self.source_code = source

            uses_khp_errors = "khp.errors" in source or "from khp" in source
            self._add("CONF-008", "Uses KHP error types", uses_khp_errors,
                      "" if uses_khp_errors else "Should use khp.errors for consistent error reporting",
                      severity="recommended")
        except Exception:
            self._add("CONF-008", "Uses KHP error types", False, "Could not read source file")

    def _check_type_annotations(self):
        """CONF-009: Public methods should have return type annotations."""
        total = 0
        annotated = 0

        for name in dir(self.driver_class):
            if name.startswith("_"):
                continue
            attr = getattr(self.driver_class, name, None)
            if not callable(attr):
                continue
            total += 1
            sig = inspect.signature(attr)
            if sig.return_annotation != inspect.Parameter.empty:
                annotated += 1

        if total == 0:
            self._add("CONF-009", "Type annotations on public methods", True, "N/A")
        else:
            ratio = annotated / total
            self._add("CONF-009", "Type annotations on public methods",
                      ratio >= 0.5,
                      f"{annotated}/{total} methods have return annotations",
                      severity="recommended")

    def _check_docstrings(self):
        """CONF-010: Driver class and public methods should have docstrings."""
        has_class_doc = bool(self.driver_class.__doc__)
        self._add("CONF-010a", "Driver class has docstring", has_class_doc,
                  severity="recommended")

        total_methods = 0
        documented = 0
        for name in dir(self.driver_class):
            if name.startswith("_"):
                continue
            attr = getattr(self.driver_class, name, None)
            if callable(attr) and (hasattr(attr, "_khp_readable") or
                                    hasattr(attr, "_khp_writable") or
                                    hasattr(attr, "_khp_procedure")):
                total_methods += 1
                if attr.__doc__:
                    documented += 1

        if total_methods > 0:
            ratio = documented / total_methods
            self._add("CONF-010b", "KHP methods have docstrings",
                      ratio >= 0.5,
                      f"{documented}/{total_methods} decorated methods documented",
                      severity="recommended")

    def _check_no_blocking_in_async(self):
        """CONF-011: Async methods should not call time.sleep()."""
        if not self.source_code:
            try:
                source_file = inspect.getfile(self.driver_class)
                with open(source_file, encoding="utf-8") as f:
                    self.source_code = f.read()
            except Exception:
                self._add("CONF-011", "No blocking calls in async methods", True, "Could not verify")
                return

        tree = ast.parse(self.source_code)
        blocking_in_async = False

        for node in ast.walk(tree):
            if isinstance(node, (ast.AsyncFunctionDef,)):
                for child in ast.walk(node):
                    if isinstance(child, ast.Call):
                        if isinstance(child.func, ast.Attribute):
                            if child.func.attr == "sleep" and isinstance(child.func.value, ast.Name):
                                if child.func.value.id == "time":
                                    blocking_in_async = True

        self._add("CONF-011", "No blocking calls in async methods", not blocking_in_async,
                  "Found time.sleep() in async method. Use asyncio.sleep() instead." if blocking_in_async else "",
                  severity="warning")

    def _check_init_params(self):
        """CONF-012: __init__ should accept device_id and **config."""
        sig = inspect.signature(self.driver_class.__init__)
        params = list(sig.parameters.keys())

        has_device_id = "device_id" in params
        has_kwargs = any(
            p.kind == inspect.Parameter.VAR_KEYWORD
            for p in sig.parameters.values()
        )

        self._add("CONF-012a", "__init__ accepts device_id parameter", has_device_id)
        self._add("CONF-012b", "__init__ accepts **kwargs for extensibility", has_kwargs,
                  severity="recommended")

    def _check_manifest_completeness(self):
        """CONF-013: All decorated methods should have description metadata."""
        missing_descriptions = []

        for name in dir(self.driver_class):
            if name.startswith("_"):
                continue
            attr = getattr(self.driver_class, name, None)
            if attr is None:
                continue

            for meta_attr in ("_khp_readable", "_khp_writable", "_khp_procedure"):
                if hasattr(attr, meta_attr):
                    meta = getattr(attr, meta_attr)
                    if not meta.get("description"):
                        missing_descriptions.append(name)

        if not missing_descriptions:
            self._add("CONF-013", "All KHP methods have descriptions", True)
        else:
            self._add("CONF-013", "All KHP methods have descriptions", False,
                      f"Missing description: {missing_descriptions[:5]}",
                      severity="warning")

    def summary(self) -> dict:
        """Generate a pass/fail summary."""
        total = len(self.results)
        passed = sum(1 for r in self.results if r.passed)
        required_failed = [r for r in self.results if not r.passed and r.severity == "required"]
        warnings = [r for r in self.results if not r.passed and r.severity == "warning"]
        recommendations = [r for r in self.results if not r.passed and r.severity == "recommended"]

        certified = len(required_failed) == 0

        return {
            "module": self.module_path,
            "driver": self.driver_class.__name__ if self.driver_class else None,
            "total_checks": total,
            "passed": passed,
            "failed_required": len(required_failed),
            "warnings": len(warnings),
            "recommendations": len(recommendations),
            "certified": certified,
            "certification_level": (
                "gold" if certified and not warnings else
                "silver" if certified else
                "none"
            ),
        }


def run_conformance(module_path: str, verbose: bool = True) -> dict:
    """Run conformance suite and optionally print results."""
    runner = ConformanceRunner(module_path)
    results = runner.run_all()
    summary = runner.summary()

    if verbose:
        print(f"\nKHP Conformance Report: {module_path}")
        print("=" * 60)
        for r in results:
            status = "PASS" if r.passed else "FAIL"
            severity_tag = f" [{r.severity}]" if not r.passed else ""
            print(f"  [{status}] {r.check_id}: {r.title}{severity_tag}")
            if r.message and not r.passed:
                print(f"         {r.message}")
        print("=" * 60)
        print(f"  Total: {summary['total_checks']} | Passed: {summary['passed']} | "
              f"Required Failed: {summary['failed_required']}")
        print(f"  Certification: {summary['certification_level'].upper()}")
        if summary['certified']:
            print(f"  Status: CERTIFIED")
        else:
            print(f"  Status: NOT CERTIFIED (fix required failures)")
        print()

    return summary


import asyncio  # noqa: E402 (needed for iscoroutinefunction check)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m tests.test_conformance <module_path>")
        print("Example: python -m tests.test_conformance drivers.universal_robots")
        sys.exit(1)

    module_path = sys.argv[1]
    summary = run_conformance(module_path)
    sys.exit(0 if summary["certified"] else 1)
