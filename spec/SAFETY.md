# KHP Safety Framework

## Overview

Safety in KHP is enforced at the DRIVER level — not the agent level. An AI agent cannot bypass safety limits regardless of what instructions it receives. The driver is the final authority on what operations are physically safe.

## Three-Layer Safety Model

### Layer 1: Hard Limits (Absolute — NEVER overridable)

Physical constraints that protect equipment and humans from damage.

**Examples:**
- Maximum laser power (prevents sample bleaching/eye damage)
- Maximum temperature (prevents fire/equipment damage)
- Collision zones (prevents robotic arm crashes)
- Maximum voltage/current (prevents circuit damage)
- Minimum/maximum speed (prevents mechanical stress)

**Enforcement:** Driver rejects the command entirely. Returns `SAFETY_BLOCKED`.

**Configuration:** Set by driver developer or device administrator. Cannot be modified by operators or agents.

### Layer 2: Soft Limits (Clamped — advisory but enforced)

Recommended operating ranges. Values outside are clamped to the nearest safe boundary.

**Examples:**
- Recommended pipetting speed (prevents bubble formation)
- Optimal temperature range (prevents sample degradation)
- Suggested acceleration profiles (prevents liquid splashing)

**Enforcement:** Driver clamps value to boundary. Returns `SAFETY_CLAMPED` with actual applied value.

**Override:** Administrator can temporarily expand soft limits for calibration.

### Layer 3: Confirmation Gates (Human-in-the-loop)

Operations that are irreversible, destructive, or high-consequence require human confirmation.

**Examples:**
- Discarding samples
- Homing axes (could crash if plate is misaligned)
- Running long protocols (hours of unattended operation)
- First-time use of a new procedure
- Any operation flagged by the agent as uncertain

**Enforcement:** Driver returns `CONFIRMATION_REQUIRED`. Operation blocks until human approves via dashboard/CLI/notification.

## Pre-Execution Checks

Before any WRITE or EXECUTE, the driver validates:

1. **Device online** — Can communicate with hardware
2. **Preconditions met** — Required state exists (e.g., tip attached before aspirate)
3. **Safety envelope** — Value within hard/soft limits
4. **Collision check** — No other device occupies target space
5. **Resource availability** — Required consumables present (tips, reagents, plates)

All five must pass before the operation proceeds. Any failure returns immediately with a specific error code.

## Emergency Stop

KHP defines a global emergency stop mechanism:

```
EXECUTE <any_device>.emergency_stop
```

- Propagates to ALL connected devices simultaneously
- Each driver implements device-specific safe shutdown
- State bus emits `emergency_stop` event to all subscribers
- All pending procedures abort
- Requires administrator to clear before operations resume

## Monitoring and Alerting

### Continuous Monitoring

Drivers can define properties that are monitored continuously:

```json
{
  "monitors": {
    "temperature": {
      "interval_ms": 1000,
      "alert_above": 95.0,
      "alert_below": 2.0,
      "action": "emit_event"
    },
    "vibration": {
      "interval_ms": 100,
      "alert_above": 5.0,
      "action": "emergency_stop"
    }
  }
}
```

### Safety Event Log

All safety-relevant events are logged separately:

```json
{
  "timestamp": "2026-08-28T14:30:00Z",
  "level": "WARNING",
  "type": "SAFETY_CLAMPED",
  "device_id": "pipette_1",
  "detail": "Aspirate speed 600 uL/s clamped to max 500 uL/s",
  "agent_id": "claude_session_abc",
  "action_taken": "clamped_and_continued"
}
```

## Camera-Based Safety (Optional)

Drivers can integrate camera verification:

1. **Pre-movement check** — Verify plate present and correctly oriented
2. **Post-operation check** — Verify expected result (no bubbles, correct color)
3. **Continuous monitoring** — Watch for unexpected events (spills, obstructions)

Camera integration is via a separate `camera` device with READ-only access to frame data.

## Agent Behavior Expectations

KHP does not control agent behavior (model-agnostic), but the safety framework is designed assuming agents may:

- Attempt values outside safe ranges (handled by hard/soft limits)
- Retry failed operations (safe — each command is independently validated)
- Run overnight without supervision (safe — limits still enforced)
- Make mistakes about physical constraints (safe — driver knows the physics)

The principle: **assume the agent will try anything within the command vocabulary. Make every possible command either safe or blocked.**

## Certification

Future: driver certification program where:
1. Driver passes automated safety test suite
2. All hard limits verified against manufacturer specs
3. Emergency stop tested
4. Pre-condition checks validated
5. Certified drivers get a badge in the registry

---

*Safety is the reason KHP exists as a separate layer from the model. Models are smart but not physically grounded. Drivers are physically grounded but not smart. Together they're both.*
