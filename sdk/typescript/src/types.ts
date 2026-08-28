/**
 * KHP Type definitions — shared across the SDK.
 */

export type PropertyType = 'float' | 'int' | 'bool' | 'string' | 'object' | 'array' | 'image' | 'timeseries' | 'waveform';

export interface PropertyMeta {
  name: string;
  type: PropertyType;
  description: string;
  unit?: string;
  minValue?: number;
  maxValue?: number;
  step?: number;
  enumValues?: string[];
  pattern?: string;
  default?: unknown;
  pollIntervalMs?: number;
  requiresConfirmation?: boolean;
}

export interface ProcedureMeta {
  name: string;
  description: string;
  params: Record<string, ParamDef>;
  preconditions: string[];
  postconditions: string[];
  estimatedDurationS?: number;
  requiresConfirmation?: boolean;
  idempotent?: boolean;
  reversible?: boolean;
}

export interface ParamDef {
  type: string;
  required: boolean;
  default?: unknown;
  description?: string;
}

export interface SafetyLimit {
  propertyName: string;
  min?: number;
  max?: number;
  reason: string;
  hard: boolean;
}

export interface ReadResult {
  value: unknown;
  type: string;
  unit?: string;
  timestamp: string;
}

export interface WriteResult {
  success: boolean;
  actualValue: unknown;
  safetyCheck: 'passed' | 'clamped' | 'blocked';
}

export interface ExecuteResult {
  jobId: string;
  status: 'running' | 'completed' | 'failed' | 'awaiting_confirmation';
  result?: unknown;
  error?: string;
}

export interface DiscoveredDevice {
  deviceId: string;
  name: string;
  type: string;
  host: string;
  port: number;
  status: string;
  driver?: string;
  manifestUrl?: string;
  tags?: Record<string, string>;
}

export interface ManifestSchema {
  $schema: string;
  device_id: string;
  name: string;
  type: string;
  driver: string;
  version: string;
  description: string;
  readable: Record<string, PropertyMeta>;
  writable: Record<string, PropertyMeta>;
  procedures: Record<string, ProcedureMeta>;
  safety: {
    hard_limits: Record<string, { property: string; max?: number; min?: number; reason: string }>;
    soft_limits: Record<string, { property: string; recommended_max?: number; recommended_min?: number; reason: string }>;
    emergency_stop: { supported: boolean; method?: string; recovery?: string };
  };
  metadata: {
    manufacturer?: string;
    model?: string;
    connection: { type: string; endpoint?: string; [key: string]: unknown };
    tags: Record<string, string>;
  };
}

export interface Job {
  jobId: string;
  deviceId: string;
  procedure: string;
  params: Record<string, unknown>;
  status: string;
  startedAt: string;
  completedAt?: string;
  result?: unknown;
  error?: string;
}

export interface SlotData {
  slotId: string;
  value: unknown;
  type: string;
  unit?: string;
  lastUpdated?: string;
}

export interface TransformDef {
  transformId: string;
  inputSlot: string;
  operation: string;
  params: Record<string, unknown>;
  outputSlot?: string;
  outputEvent?: string;
}

export interface KHPEvent {
  event: string;
  deviceId?: string;
  timestamp: string;
  [key: string]: unknown;
}
