/**
 * KHP Decorators — TypeScript decorators for marking driver methods.
 *
 * Note: Requires experimentalDecorators or the TC39 Stage 3 decorator syntax.
 */

import type { PropertyMeta, ProcedureMeta, SafetyLimit } from './types.js';

interface ReadableOptions {
  type?: string;
  description?: string;
  unit?: string;
  minValue?: number;
  maxValue?: number;
  pollIntervalMs?: number;
}

interface WritableOptions {
  type?: string;
  description?: string;
  unit?: string;
  minValue?: number;
  maxValue?: number;
  step?: number;
  enumValues?: string[];
  requiresConfirmation?: boolean;
}

interface ProcedureOptions {
  description?: string;
  preconditions?: string[];
  postconditions?: string[];
  estimatedDurationS?: number;
  requiresConfirmation?: boolean;
  idempotent?: boolean;
  reversible?: boolean;
}

interface SafetyOptions {
  min?: number;
  max?: number;
  reason?: string;
  hard?: boolean;
  requireConfirmation?: boolean;
}

interface MonitorOptions {
  intervalMs?: number;
  alertAbove?: number;
  alertBelow?: number;
  action?: string;
}

/**
 * Mark a method as a readable property.
 */
export function readable(options: ReadableOptions = {}) {
  return function (target: any, propertyKey: string, descriptor: PropertyDescriptor) {
    if (!target._khpReadable) target._khpReadable = new Map();
    const meta: PropertyMeta = {
      name: propertyKey,
      type: (options.type || 'float') as any,
      description: options.description || '',
      unit: options.unit,
      minValue: options.minValue,
      maxValue: options.maxValue,
      pollIntervalMs: options.pollIntervalMs,
    };
    target._khpReadable.set(propertyKey, meta);
    return descriptor;
  };
}

/**
 * Mark a method as a writable property.
 */
export function writable(options: WritableOptions = {}) {
  return function (target: any, propertyKey: string, descriptor: PropertyDescriptor) {
    if (!target._khpWritable) target._khpWritable = new Map();
    const meta: PropertyMeta = {
      name: propertyKey,
      type: (options.type || 'float') as any,
      description: options.description || '',
      unit: options.unit,
      minValue: options.minValue,
      maxValue: options.maxValue,
      step: options.step,
      enumValues: options.enumValues,
      requiresConfirmation: options.requiresConfirmation || false,
    };
    target._khpWritable.set(propertyKey, meta);
    return descriptor;
  };
}

/**
 * Mark a method as an executable procedure.
 */
export function procedure(options: ProcedureOptions = {}) {
  return function (target: any, propertyKey: string, descriptor: PropertyDescriptor) {
    if (!target._khpProcedures) target._khpProcedures = new Map();
    const meta: ProcedureMeta = {
      name: propertyKey,
      description: options.description || '',
      params: {},
      preconditions: options.preconditions || [],
      postconditions: options.postconditions || [],
      estimatedDurationS: options.estimatedDurationS,
      requiresConfirmation: options.requiresConfirmation || false,
      idempotent: options.idempotent || false,
      reversible: options.reversible || false,
    };
    target._khpProcedures.set(propertyKey, meta);
    return descriptor;
  };
}

/**
 * Attach safety limits to a writable property or procedure.
 */
export function safety(options: SafetyOptions = {}) {
  return function (target: any, propertyKey: string, descriptor: PropertyDescriptor) {
    if (!target._khpSafety) target._khpSafety = [];
    const limit: SafetyLimit = {
      propertyName: propertyKey,
      min: options.min,
      max: options.max,
      reason: options.reason || '',
      hard: options.hard !== false,
    };
    target._khpSafety.push(limit);
    return descriptor;
  };
}

/**
 * Mark a readable property for continuous monitoring.
 */
export function monitor(options: MonitorOptions = {}) {
  return function (target: any, propertyKey: string, descriptor: PropertyDescriptor) {
    if (!target._khpMonitors) target._khpMonitors = new Map();
    target._khpMonitors.set(propertyKey, {
      intervalMs: options.intervalMs || 1000,
      alertAbove: options.alertAbove,
      alertBelow: options.alertBelow,
      action: options.action || 'emit_event',
    });
    return descriptor;
  };
}
