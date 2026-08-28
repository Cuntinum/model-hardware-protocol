/**
 * KHP Driver base class — the foundation for all TypeScript hardware drivers.
 */

import { randomUUID } from 'node:crypto';
import type {
  PropertyMeta, ProcedureMeta, SafetyLimit,
  ReadResult, WriteResult, ExecuteResult, Job, ManifestSchema, KHPEvent,
} from './types.js';
import { PropertyNotFoundError, SafetyBlockedError, ConfirmationRequiredError, PreconditionFailedError } from './errors.js';

export enum DeviceStatus {
  ONLINE = 'online',
  OFFLINE = 'offline',
  BUSY = 'busy',
  MAINTENANCE = 'maintenance',
  ERROR = 'error',
}

export enum ConnectionType {
  REST = 'REST',
  SERIAL = 'serial',
  USB = 'USB',
  TCP = 'TCP',
  FILE_DROP = 'file_drop',
  COM = 'COM',
  SDK = 'SDK',
  GUI = 'GUI',
  MQTT = 'MQTT',
  MODBUS = 'modbus',
  GPIO = 'GPIO',
}

export abstract class Driver {
  abstract name: string;
  abstract deviceType: string;
  version: string = '1.0.0';
  description: string = '';
  connectionType: ConnectionType = ConnectionType.REST;

  deviceId: string;
  status: DeviceStatus = DeviceStatus.OFFLINE;
  config: Record<string, unknown>;

  protected _readableProps: Map<string, PropertyMeta> = new Map();
  protected _writableProps: Map<string, PropertyMeta> = new Map();
  protected _procedures: Map<string, ProcedureMeta> = new Map();
  protected _safetyLimits: SafetyLimit[] = [];
  protected _jobs: Map<string, Job> = new Map();
  protected _eventHandlers: Map<string, Array<(event: KHPEvent) => void>> = new Map();
  protected _tags: Record<string, string> = {};
  protected _auditLog: Array<Record<string, unknown>> = [];

  constructor(deviceId?: string, config: Record<string, unknown> = {}) {
    this.deviceId = deviceId || `${this.deviceType}_${randomUUID().slice(0, 8)}`;
    this.config = config;
  }

  async connect(): Promise<void> {
    this.status = DeviceStatus.ONLINE;
  }

  async disconnect(): Promise<void> {
    this.status = DeviceStatus.OFFLINE;
  }

  async healthCheck(): Promise<boolean> {
    return this.status === DeviceStatus.ONLINE;
  }

  async emergencyStop(): Promise<void> {
    for (const [, job] of this._jobs) {
      if (job.status === 'running') job.status = 'aborted';
    }
    this.status = DeviceStatus.ERROR;
    this.emitEvent('emergency_stop', { deviceId: this.deviceId });
  }

  read(propertyName: string): ReadResult {
    const meta = this._readableProps.get(propertyName);
    if (!meta) {
      throw new PropertyNotFoundError(propertyName, this.deviceId);
    }
    const method = (this as any)[propertyName];
    if (typeof method !== 'function') {
      throw new PropertyNotFoundError(propertyName, this.deviceId);
    }
    const value = method.call(this);
    const result: ReadResult = {
      value,
      type: meta.type,
      unit: meta.unit,
      timestamp: new Date().toISOString(),
    };
    this.logOperation('READ', propertyName, { result });
    return result;
  }

  write(propertyName: string, value: unknown): WriteResult {
    const meta = this._writableProps.get(propertyName);
    if (!meta) {
      throw new PropertyNotFoundError(propertyName, this.deviceId);
    }

    if (meta.requiresConfirmation) {
      throw new ConfirmationRequiredError(
        `Writing '${propertyName}' requires human confirmation`,
        this.deviceId,
        propertyName,
        randomUUID().slice(0, 12),
      );
    }

    const safetyResult = this.checkSafety(propertyName, value as number);

    if (safetyResult === 'blocked') {
      const limit = this.getLimit(propertyName);
      throw new SafetyBlockedError(
        `Value ${value} exceeds hard limit for '${propertyName}'`,
        this.deviceId,
        propertyName,
        value,
        limit,
      );
    }

    let actualValue = value;
    if (safetyResult === 'clamped') {
      actualValue = this.clampValue(propertyName, value as number);
    }

    const method = (this as any)[`set_${propertyName}`] || (this as any)[propertyName];
    if (typeof method === 'function') {
      method.call(this, actualValue);
    }

    const result: WriteResult = {
      success: true,
      actualValue,
      safetyCheck: safetyResult,
    };
    this.logOperation('WRITE', propertyName, { value: actualValue, result });
    return result;
  }

  async execute(procedureName: string, params: Record<string, unknown> = {}): Promise<ExecuteResult> {
    const meta = this._procedures.get(procedureName);
    if (!meta) {
      throw new PropertyNotFoundError(procedureName, this.deviceId);
    }

    if (meta.requiresConfirmation) {
      throw new ConfirmationRequiredError(
        `Procedure '${procedureName}' requires human confirmation`,
        this.deviceId,
        procedureName,
        randomUUID().slice(0, 12),
      );
    }

    for (const precondition of meta.preconditions) {
      const checkMethod = (this as any)[`_check_${precondition}`];
      if (checkMethod && !checkMethod.call(this)) {
        throw new PreconditionFailedError(
          `Precondition '${precondition}' not met for ${procedureName}`,
          this.deviceId,
        );
      }
    }

    const job: Job = {
      jobId: `j_${randomUUID().slice(0, 12)}`,
      deviceId: this.deviceId,
      procedure: procedureName,
      params,
      status: 'running',
      startedAt: new Date().toISOString(),
    };
    this._jobs.set(job.jobId, job);

    try {
      const method = (this as any)[procedureName];
      const result = await method.call(this, ...Object.values(params));
      job.status = 'completed';
      job.result = result;
      job.completedAt = new Date().toISOString();
    } catch (e: any) {
      job.status = 'failed';
      job.error = e.message;
      job.completedAt = new Date().toISOString();
    }

    this.logOperation('EXECUTE', procedureName, { params, jobId: job.jobId, status: job.status });
    return { jobId: job.jobId, status: job.status as any, result: job.result };
  }

  getManifest(): ManifestSchema {
    const readable: Record<string, any> = {};
    for (const [name, meta] of this._readableProps) {
      readable[name] = { type: meta.type, description: meta.description, unit: meta.unit, range: { min: meta.minValue, max: meta.maxValue } };
    }
    const writable: Record<string, any> = {};
    for (const [name, meta] of this._writableProps) {
      writable[name] = { type: meta.type, description: meta.description, unit: meta.unit, min: meta.minValue, max: meta.maxValue };
    }
    const procedures: Record<string, any> = {};
    for (const [name, meta] of this._procedures) {
      procedures[name] = { description: meta.description, params: meta.params, preconditions: meta.preconditions, estimated_duration_s: meta.estimatedDurationS };
    }

    return {
      $schema: 'https://khp.dev/schema/manifest/v1',
      device_id: this.deviceId,
      name: this.name,
      type: this.deviceType,
      driver: this.constructor.name,
      version: this.version,
      description: this.description,
      readable,
      writable,
      procedures,
      safety: {
        hard_limits: Object.fromEntries(
          this._safetyLimits.filter(l => l.hard).map(l => [
            `${l.propertyName}_limit`,
            { property: l.propertyName, max: l.max, min: l.min, reason: l.reason },
          ]),
        ),
        soft_limits: Object.fromEntries(
          this._safetyLimits.filter(l => !l.hard).map(l => [
            `${l.propertyName}_soft`,
            { property: l.propertyName, recommended_max: l.max, recommended_min: l.min, reason: l.reason },
          ]),
        ),
        emergency_stop: { supported: true },
      },
      metadata: {
        connection: { type: this.connectionType, ...this.config },
        tags: this._tags,
      },
    } as ManifestSchema;
  }

  setTags(tags: Record<string, string>): void {
    Object.assign(this._tags, tags);
  }

  onEvent(eventType: string, handler: (event: KHPEvent) => void): void {
    if (!this._eventHandlers.has(eventType)) {
      this._eventHandlers.set(eventType, []);
    }
    this._eventHandlers.get(eventType)!.push(handler);
  }

  protected emitEvent(eventType: string, data: Record<string, unknown>): void {
    const event: KHPEvent = { event: eventType, deviceId: this.deviceId, timestamp: new Date().toISOString(), ...data };
    for (const handler of this._eventHandlers.get(eventType) || []) handler(event);
    for (const handler of this._eventHandlers.get('*') || []) handler(event);
  }

  protected checkSafety(propertyName: string, value: number): 'passed' | 'clamped' | 'blocked' {
    for (const limit of this._safetyLimits) {
      if (limit.propertyName !== propertyName) continue;
      if (limit.hard) {
        if (limit.max !== undefined && value > limit.max) return 'blocked';
        if (limit.min !== undefined && value < limit.min) return 'blocked';
      } else {
        if (limit.max !== undefined && value > limit.max) return 'clamped';
        if (limit.min !== undefined && value < limit.min) return 'clamped';
      }
    }
    return 'passed';
  }

  protected clampValue(propertyName: string, value: number): number {
    for (const limit of this._safetyLimits) {
      if (limit.propertyName !== propertyName || limit.hard) continue;
      if (limit.max !== undefined && value > limit.max) value = limit.max;
      if (limit.min !== undefined && value < limit.min) value = limit.min;
    }
    return value;
  }

  protected getLimit(propertyName: string): unknown {
    for (const limit of this._safetyLimits) {
      if (limit.propertyName === propertyName && limit.hard) {
        return { min: limit.min, max: limit.max };
      }
    }
    return null;
  }

  protected logOperation(op: string, target: string, details: Record<string, unknown> = {}): void {
    this._auditLog.push({ timestamp: new Date().toISOString(), operation: op, deviceId: this.deviceId, target, ...details });
    if (this._auditLog.length > 10000) this._auditLog = this._auditLog.slice(-5000);
  }
}
