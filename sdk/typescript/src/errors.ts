/**
 * KHP Error types — maps to protocol error codes.
 */

export class KHPError extends Error {
  readonly code: string = 'KHP_ERROR';
  readonly deviceId: string;
  readonly details: Record<string, unknown>;

  constructor(message: string, deviceId: string = '', details: Record<string, unknown> = {}) {
    super(message);
    this.name = 'KHPError';
    this.deviceId = deviceId;
    this.details = details;
  }

  toJSON() {
    return {
      success: false,
      error: {
        code: this.code,
        message: this.message,
        device_id: this.deviceId,
        ...this.details,
      },
    };
  }
}

export class DeviceNotFoundError extends KHPError {
  readonly code = 'DEVICE_NOT_FOUND';
  constructor(deviceId: string) {
    super(`Device '${deviceId}' not found`, deviceId);
    this.name = 'DeviceNotFoundError';
  }
}

export class PropertyNotFoundError extends KHPError {
  readonly code = 'PROPERTY_NOT_FOUND';
  constructor(property: string, deviceId: string) {
    super(`Property '${property}' not found on ${deviceId}`, deviceId, { property });
    this.name = 'PropertyNotFoundError';
  }
}

export class SafetyBlockedError extends KHPError {
  readonly code = 'SAFETY_BLOCKED';
  constructor(
    message: string,
    deviceId: string,
    public readonly property: string,
    public readonly requestedValue: unknown,
    public readonly limitValue: unknown,
  ) {
    super(message, deviceId, { property, requested_value: requestedValue, limit_value: limitValue });
    this.name = 'SafetyBlockedError';
  }
}

export class SafetyClampedError extends KHPError {
  readonly code = 'SAFETY_CLAMPED';
  constructor(
    message: string,
    deviceId: string,
    public readonly property: string,
    public readonly requestedValue: unknown,
    public readonly actualValue: unknown,
  ) {
    super(message, deviceId, { property, requested_value: requestedValue, actual_value: actualValue });
    this.name = 'SafetyClampedError';
  }
}

export class PreconditionFailedError extends KHPError {
  readonly code = 'PRECONDITION_FAILED';
  constructor(message: string, deviceId: string) {
    super(message, deviceId);
    this.name = 'PreconditionFailedError';
  }
}

export class ConfirmationRequiredError extends KHPError {
  readonly code = 'CONFIRMATION_REQUIRED';
  constructor(
    message: string,
    deviceId: string,
    public readonly procedureName: string,
    public readonly confirmationId: string,
  ) {
    super(message, deviceId, { procedure: procedureName, confirmation_id: confirmationId });
    this.name = 'ConfirmationRequiredError';
  }
}

export class DeviceBusyError extends KHPError {
  readonly code = 'DEVICE_BUSY';
  constructor(deviceId: string) {
    super(`Device '${deviceId}' is busy`, deviceId);
    this.name = 'DeviceBusyError';
  }
}

export class DeviceOfflineError extends KHPError {
  readonly code = 'DEVICE_OFFLINE';
  constructor(deviceId: string) {
    super(`Device '${deviceId}' is offline`, deviceId);
    this.name = 'DeviceOfflineError';
  }
}

export class TimeoutError extends KHPError {
  readonly code = 'TIMEOUT';
  constructor(message: string, deviceId: string) {
    super(message, deviceId);
    this.name = 'TimeoutError';
  }
}

export class HardwareError extends KHPError {
  readonly code = 'HARDWARE_ERROR';
  constructor(message: string, deviceId: string) {
    super(message, deviceId);
    this.name = 'HardwareError';
  }
}
