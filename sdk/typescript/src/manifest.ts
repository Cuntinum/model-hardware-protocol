/**
 * KHP Manifest utilities — generate, validate, and load manifests.
 */

import { readFileSync, writeFileSync } from 'node:fs';
import { Driver } from './core.js';
import type { ManifestSchema } from './types.js';

export class Manifest {
  data: ManifestSchema;
  deviceId: string;
  name: string;
  deviceType: string;
  version: string;
  description: string;
  readable: Record<string, any>;
  writable: Record<string, any>;
  procedures: Record<string, any>;
  safety: any;
  metadata: any;

  constructor(data: ManifestSchema) {
    this.data = data;
    this.deviceId = data.device_id;
    this.name = data.name;
    this.deviceType = data.type;
    this.version = data.version;
    this.description = data.description;
    this.readable = data.readable || {};
    this.writable = data.writable || {};
    this.procedures = data.procedures || {};
    this.safety = data.safety || {};
    this.metadata = data.metadata || {};
  }

  static fromFile(path: string): Manifest {
    const content = readFileSync(path, 'utf8');
    return new Manifest(JSON.parse(content));
  }

  static fromDriver(driver: Driver): Manifest {
    return new Manifest(driver.getManifest());
  }

  toFile(path: string): void {
    writeFileSync(path, JSON.stringify(this.data, null, 2));
  }

  validate(): string[] {
    const errors: string[] = [];
    if (!this.deviceId) errors.push('Missing required field: device_id');
    if (!this.name) errors.push('Missing required field: name');
    if (!this.deviceType) errors.push('Missing required field: type');

    for (const [propName, prop] of Object.entries(this.writable)) {
      if (!prop.type) errors.push(`Writable '${propName}' missing type`);
    }
    for (const [procName, proc] of Object.entries(this.procedures)) {
      if (!proc.description && !proc.params) {
        errors.push(`Procedure '${procName}' needs description or params`);
      }
    }

    const hardLimits = this.safety?.hard_limits || {};
    for (const [limitName, limit] of Object.entries(hardLimits) as [string, any][]) {
      if (!limit.property) errors.push(`Hard limit '${limitName}' missing property`);
      if (limit.max == null && limit.min == null) {
        errors.push(`Hard limit '${limitName}' must have min or max`);
      }
    }
    return errors;
  }

  get allCapabilities(): Set<string> {
    return new Set([
      ...Object.keys(this.readable),
      ...Object.keys(this.writable),
      ...Object.keys(this.procedures),
    ]);
  }

  hasCapability(name: string): boolean {
    return this.allCapabilities.has(name);
  }
}

export function generateManifest(driver: Driver, outputPath?: string): ManifestSchema {
  const data = driver.getManifest();
  if (outputPath) writeFileSync(outputPath, JSON.stringify(data, null, 2));
  return data;
}

export function validateManifest(path: string): string[] {
  const manifest = Manifest.fromFile(path);
  return manifest.validate();
}
