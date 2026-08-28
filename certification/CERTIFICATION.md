# Model Hardware Protocol: Driver Certification Program

The MHP Driver Certification Program establishes quality, safety, and reliability standards for community contributed drivers. Certified drivers receive an official badge, priority listing in the driver catalog, and increased trust from integrators.

## Certification Tiers

### Bronze: Community Verified

The entry level tier confirms that a driver follows MHP conventions and passes automated checks.

**Requirements:**

| Category | Criteria |
|----------|----------|
| Code Quality | Passes `ruff check` with zero errors; no `# type: ignore` without justification |
| Manifest | Valid manifest with all required fields (see Manifest Rules below) |
| Safety | At least one hard limit defined OR documented justification for omission |
| Emergency Stop | `emergency_stop()` implemented and functional |
| Documentation | README with: installation, supported hardware, example usage |
| Tests | Minimum 5 unit tests covering read, write, and execute paths |
| License | Apache 2.0, MIT, or BSD (OSI approved) |
| CI | All automated checks pass in the MHP CI pipeline |

**Badge:**

```
![MHP Bronze](https://img.shields.io/badge/MHP-Bronze-cd7f32?style=flat-square&logo=data:image/svg+xml;base64,...)
```

### Silver: Tested and Reviewed

Silver drivers have been manually reviewed by a maintainer and demonstrate comprehensive test coverage with proper error handling.

**Requirements (all Bronze requirements plus):**

| Category | Criteria |
|----------|----------|
| Code Review | Approved by at least one MHP maintainer |
| Test Coverage | Minimum 80% line coverage on driver code |
| Integration Tests | At least 3 integration tests demonstrating full workflows |
| Error Handling | All KHP error types used appropriately (no bare exceptions) |
| Connection Lifecycle | `connect()`, `disconnect()`, `health_check()` all implemented |
| Preconditions | Procedures with physical prerequisites define precondition checks |
| Audit Logging | All operations logged (inherited from base, but verified functional) |
| Documentation | API reference, configuration guide, troubleshooting section |
| Type Hints | Full type annotations on all public methods |
| Changelog | Version history with semantic versioning |

**Badge:**

```
![MHP Silver](https://img.shields.io/badge/MHP-Silver-c0c0c0?style=flat-square&logo=data:image/svg+xml;base64,...)
```

### Gold: Hardware Validated

Gold certification requires physical hardware testing, demonstrating that the driver actually controls the claimed device safely under real conditions.

**Requirements (all Silver requirements plus):**

| Category | Criteria |
|----------|----------|
| Hardware Test | Video or photographic evidence of driver controlling real hardware |
| Safety Validation | Hard limits tested at boundaries (device does not exceed rated values) |
| Emergency Stop Validation | Emergency stop verified to halt physical operation within 100ms |
| Stress Test | 1000+ operations without connection loss or state corruption |
| Edge Cases | Tested: device power loss, cable disconnect, simultaneous access |
| Performance | Response time benchmarks documented (read latency, write latency) |
| Multi Device | Tested with 2+ instances running concurrently (if applicable) |
| Production Use | At least one deployment report from a real user or lab |
| Maintainer Commitment | Named maintainer commits to 90 day response time on issues |

**Badge:**

```
![MHP Gold](https://img.shields.io/badge/MHP-Gold-ffd700?style=flat-square&logo=data:image/svg+xml;base64,...)
```

## Manifest Validation Rules

Every certified driver must produce a valid manifest containing:

**Required Fields:**

| Field | Type | Description |
|-------|------|-------------|
| `$schema` | string | Must be `https://khp.dev/schema/manifest/v1` |
| `device_id` | string | Unique device identifier |
| `name` | string | Human readable device name |
| `type` | string | Device category (sensor, actuator, instrument, robot, etc.) |
| `driver` | string | Driver class name |
| `version` | string | Semantic version of the driver |
| `readable` | object | At least one readable property |
| `safety` | object | Safety configuration (may be empty with justification) |

**Required per Readable Property:**

| Field | Type | Description |
|-------|------|-------------|
| `type` | string | Data type (float, int, bool, string, object) |
| `description` | string | Human readable explanation |
| `unit` | string | SI unit or "none" (required for numeric types) |

**Required per Writable Property:**

| Field | Type | Description |
|-------|------|-------------|
| `type` | string | Data type |
| `description` | string | Human readable explanation |
| `unit` | string | SI unit or "none" |

**Required per Procedure:**

| Field | Type | Description |
|-------|------|-------------|
| `description` | string | What this procedure does |
| `params` | object | Parameter schema |
| `preconditions` | array | List of required preconditions |
| `estimated_duration_s` | number | Expected execution time |

## Safety Audit Checklist

The safety audit is mandatory for Silver and Gold tiers. Bronze requires automated checks only.

### Automated Safety Checks (Bronze)

- [ ] Manifest contains a `safety` section
- [ ] `emergency_stop()` method exists and sets status to ERROR
- [ ] No direct hardware writes without passing through `checkSafety()`
- [ ] Hard limits reference physically meaningful values (not arbitrary large numbers)
- [ ] Writable properties with numeric types have at least soft limits defined

### Manual Safety Review (Silver)

- [ ] Hard limits match manufacturer specifications (datasheet reference provided)
- [ ] Soft limits represent safe operating ranges for the intended use case
- [ ] Confirmation gates protect irreversible or dangerous operations
- [ ] Emergency stop is reachable from any device state (no deadlocks)
- [ ] Connection loss is handled gracefully (device goes to safe state)
- [ ] No race conditions between safety checks and hardware writes
- [ ] Audit log captures all safety events (blocks, clamps, confirmations)
- [ ] Error messages include actionable information for operators

### Physical Safety Validation (Gold)

- [ ] Hard limits tested: writes at boundary values accepted correctly
- [ ] Hard limits tested: writes beyond boundary values blocked with correct error
- [ ] Soft limits tested: values outside range clamped to boundary
- [ ] Emergency stop tested: device halts within 100ms of signal
- [ ] Power loss tested: device recovers to safe state on reconnection
- [ ] Concurrent access tested: two agents cannot create conflicting commands
- [ ] Safety cannot be bypassed via direct property access or raw commands
- [ ] Watchdog behavior: device enters safe state if driver crashes without disconnect

## Submission Process

### Step 1: Self Assessment

Run the automated certification checker:

```bash
khp certify ./my_driver/ --tier bronze
```

This validates manifest, safety rules, test coverage, and code quality.

### Step 2: Open a Pull Request

Use the driver certification PR template:

```markdown
## Driver Certification Request

**Driver name:** [your driver name]
**Target tier:** [Bronze / Silver / Gold]
**Hardware:** [device make and model]
**Connection type:** [Serial / TCP / USB / GPIO / etc.]

### Checklist

- [ ] All automated checks pass (`khp certify` exits 0)
- [ ] README covers installation, configuration, and usage
- [ ] Tests pass in CI (Python 3.9 through 3.12)
- [ ] No secrets, credentials, or hardware addresses hardcoded
- [ ] License file present (OSI approved)

### For Silver and Above

- [ ] 80%+ test coverage report attached
- [ ] Integration test demonstrates full read/write/execute workflow
- [ ] Safety limits reference manufacturer datasheet (link provided)

### For Gold

- [ ] Hardware test evidence (video/photos) linked
- [ ] Stress test report (1000+ ops) attached
- [ ] Named maintainer commits to 90 day issue response
```

### Step 3: Review Process

| Tier | Review Type | Reviewers | Timeline |
|------|-------------|-----------|----------|
| Bronze | Automated + quick manual | 1 maintainer | 3 business days |
| Silver | Full code review + safety audit | 2 maintainers | 10 business days |
| Gold | Code review + safety audit + hardware evidence review | 2 maintainers + 1 safety lead | 20 business days |

### Step 4: Certification Issued

Upon approval:

1. Badge added to your driver README
2. Driver listed in the official catalog with certification tier
3. Certification record published (driver version, date, reviewers)
4. Notification sent to MHP mailing list

## Recertification

Certification is valid for **12 months** from the date of issue.

### Annual Renewal Requirements

| Tier | Renewal Criteria |
|------|-----------------|
| Bronze | Automated checks still pass on latest MHP SDK version |
| Silver | All Bronze requirements + tests still pass + no unresolved safety issues |
| Gold | All Silver requirements + maintainer confirms hardware still functions + no critical bugs in past year |

### Automatic Renewal

If the driver passes all automated checks against the latest MHP SDK release and has no open safety issues, Bronze and Silver certifications renew automatically. Gold always requires manual confirmation.

### Renewal Reminders

- 60 days before expiry: email notification to listed maintainer
- 30 days before expiry: second notification + GitHub issue created
- 0 days: certification expires, badge removed from catalog (driver remains available but unlisted)
- 30 days past expiry: driver moved to "uncertified" section

## Revocation

Certification may be revoked immediately (without waiting for expiry) under these conditions:

| Condition | Action | Appeal |
|-----------|--------|--------|
| Safety vulnerability discovered | Immediate revocation + advisory published | 30 days to fix and recertify |
| Driver causes hardware damage (confirmed report) | Immediate revocation | Safety review required for reinstatement |
| Maintainer unresponsive for 90+ days on critical issue | Downgrade to Bronze (automated only) | Restore upon response |
| License violation | Immediate removal from catalog | Resolve license issue to reinstate |
| Malicious code detected | Permanent ban + security advisory | None |

### Revocation Process

1. Issue filed with `[REVOCATION]` prefix
2. Maintainer notified (email + GitHub)
3. 48 hour grace period for response (except security issues: immediate)
4. Certification removed, badge invalidated
5. Advisory published if safety related

## Machine Readable Certification

Each certified driver receives a `certification.json` in its directory:

```json
{
  "$schema": "https://khp.dev/schema/certification/v1",
  "driver": "MyThermocycler",
  "version": "1.2.0",
  "tier": "silver",
  "certified_date": "2026-08-28",
  "expires_date": "2027-08-28",
  "reviewers": ["maintainer_github_handle"],
  "safety_audit": "passed",
  "test_coverage": 87.3,
  "manifest_valid": true,
  "hardware_validated": false
}
```

## FAQ

**Q: Can I submit a driver that only implements READ (no WRITE or EXECUTE)?**
A: Yes. Read only sensors are valid. You still need emergency_stop() (it can be a no op for passive sensors) and a safety section in your manifest (it can note "read only device, no actuation safety required").

**Q: My device has no meaningful safety limits. What do I put?**
A: Document why in your README and manifest. For example, a temperature sensor with no actuation has no writable limits. The automated checker accepts an empty safety section with a justification field.

**Q: Can I certify a driver for hardware I no longer have access to?**
A: Bronze and Silver, yes (tests can use mocks and simulations). Gold requires hardware validation evidence and a maintenance commitment, so you need ongoing access.

**Q: How do I handle proprietary protocols?**
A: The driver code must be open source under an OSI license. If the device uses a proprietary protocol, you may depend on vendor provided SDKs (clearly documented as a dependency). The protocol itself need not be documented.

**Q: What if my driver supports multiple device models?**
A: Certify the driver once with the model list in the manifest. Each model should have at least one test. Gold certification requires hardware evidence for each claimed model (or a representative subset with documented justification).
