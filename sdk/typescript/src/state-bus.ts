/**
 * KHP State Bus — shared data layer for inter-device and agent communication.
 */

import type { SlotData, TransformDef, KHPEvent } from './types.js';

export class Slot {
  slotId: string;
  type: string;
  unit?: string;
  retentionS: number;
  value: unknown = null;
  lastUpdated?: string;
  history: Array<{ value: unknown; timestamp: string }> = [];
  subscribers: string[] = [];
  maxHistory: number;

  constructor(slotId: string, type = 'float', unit?: string, retentionS = 3600, maxHistory = 1000) {
    this.slotId = slotId;
    this.type = type;
    this.unit = unit;
    this.retentionS = retentionS;
    this.maxHistory = maxHistory;
  }

  write(value: unknown): void {
    this.value = value;
    this.lastUpdated = new Date().toISOString();
    this.history.push({ value, timestamp: this.lastUpdated });
    if (this.history.length > this.maxHistory) {
      this.history = this.history.slice(-this.maxHistory);
    }
  }

  read(): SlotData {
    return { slotId: this.slotId, value: this.value, type: this.type, unit: this.unit, lastUpdated: this.lastUpdated };
  }

  getHistory(lastN = 100): Array<{ value: unknown; timestamp: string }> {
    return this.history.slice(-lastN);
  }
}

export class Transform {
  transformId: string;
  inputSlot: string;
  operation: string;
  params: Record<string, unknown>;
  outputSlot?: string;
  outputEvent?: string;

  constructor(def: TransformDef) {
    this.transformId = def.transformId;
    this.inputSlot = def.inputSlot;
    this.operation = def.operation;
    this.params = def.params;
    this.outputSlot = def.outputSlot;
    this.outputEvent = def.outputEvent;
  }

  apply(value: unknown): unknown {
    if (this.operation === 'threshold') {
      const above = this.params.above as number | undefined;
      const below = this.params.below as number | undefined;
      const numValue = value as number;
      if (above !== undefined && numValue > above) return { triggered: true, direction: 'above', value };
      if (below !== undefined && numValue < below) return { triggered: true, direction: 'below', value };
      return { triggered: false, value };
    }
    if (this.operation === 'scale') {
      const factor = (this.params.factor as number) || 1.0;
      const offset = (this.params.offset as number) || 0.0;
      return (value as number) * factor + offset;
    }
    if (this.operation === 'clamp') {
      const min = (this.params.min as number) ?? -Infinity;
      const max = (this.params.max as number) ?? Infinity;
      return Math.max(min, Math.min(max, value as number));
    }
    return value;
  }
}

export class StateBus {
  private slots: Map<string, Slot> = new Map();
  private transforms: Map<string, Transform> = new Map();
  private eventHandlers: Map<string, Array<(event: KHPEvent) => void>> = new Map();

  createSlot(slotId: string, type = 'float', unit?: string, retentionS = 3600): Slot {
    const slot = new Slot(slotId, type, unit, retentionS);
    this.slots.set(slotId, slot);
    return slot;
  }

  getSlot(slotId: string): Slot | undefined {
    return this.slots.get(slotId);
  }

  writeSlot(slotId: string, value: unknown): void {
    let slot = this.slots.get(slotId);
    if (!slot) slot = this.createSlot(slotId);
    slot.write(value);
    this.applyTransforms(slotId, value);
    this.emit('slot_updated', { slotId, value });
  }

  readSlot(slotId: string): SlotData | null {
    const slot = this.slots.get(slotId);
    return slot ? slot.read() : null;
  }

  addTransform(def: TransformDef): void {
    this.transforms.set(def.transformId, new Transform(def));
  }

  on(event: string, handler: (event: KHPEvent) => void): void {
    if (!this.eventHandlers.has(event)) this.eventHandlers.set(event, []);
    this.eventHandlers.get(event)!.push(handler);
  }

  listSlots(): SlotData[] {
    return Array.from(this.slots.values()).map(s => s.read());
  }

  private applyTransforms(slotId: string, value: unknown): void {
    for (const t of this.transforms.values()) {
      if (t.inputSlot !== slotId) continue;
      const result = t.apply(value);
      if (t.outputSlot) this.writeSlot(t.outputSlot, result);
      if (t.outputEvent && typeof result === 'object' && result !== null && (result as any).triggered) {
        this.emit(t.outputEvent, result as Record<string, unknown>);
      }
    }
  }

  private emit(event: string, data: Record<string, unknown>): void {
    const payload: KHPEvent = { event, timestamp: new Date().toISOString(), ...data };
    for (const handler of this.eventHandlers.get(event) || []) handler(payload);
    for (const handler of this.eventHandlers.get('*') || []) handler(payload);
  }
}
