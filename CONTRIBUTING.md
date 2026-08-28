# Contributing to KHP

We welcome contributions from the community! Here's how to get involved.

## Writing a Driver

The most impactful contribution is a new device driver. If you have hardware that isn't supported yet:

1. Create a new folder under `drivers/your-device-name/`
2. Implement a class that extends `khp.Driver`
3. Use `@readable`, `@writable`, `@procedure`, and `@safety` decorators
4. Add safety limits for ALL writable properties
5. Write a `__init__.py` that exports your driver class
6. Test with `examples/quickstart.py` as a template
7. Submit a PR

### Driver Requirements

- Every writable property MUST have a `@safety` decorator with reasonable limits
- Every procedure that modifies physical state needs `estimated_duration_s`
- Destructive procedures need `requires_confirmation=True`
- Include docstrings on all decorated methods (these become agent documentation)
- Test with at least one simulated scenario (no real hardware required for PR)

## Improving the SDK

- Bug fixes: Always welcome
- New features: Open an issue first to discuss
- Performance: Profile before optimizing

## Spec Changes

Changes to `spec/` documents require an RFC process:
1. Open an issue describing the change and motivation
2. Draft the change in a PR
3. Two maintainer approvals required
4. Breaking changes require a major version bump

## Code Style

- Python: Follow PEP 8, type hints encouraged
- Docstrings: Google style
- Tests: pytest, one test file per driver

## License

By contributing, you agree that your contributions will be licensed under Apache 2.0.
