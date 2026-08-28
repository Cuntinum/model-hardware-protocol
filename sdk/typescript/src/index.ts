/**
 * Kinetic Hardware Protocol (KHP) — TypeScript SDK
 *
 * Build drivers for physical hardware that any AI agent can control.
 *
 * @example
 * ```typescript
 * import { Driver, readable, writable, procedure, safety } from '@khp/sdk';
 *
 * class MyThermocycler extends Driver {
 *   name = 'BioRad CFX96';
 *   deviceType = 'thermocycler';
 *
 *   @readable({ type: 'float', unit: 'celsius' })
 *   async temperature(): Promise<number> { ... }
 *
 *   @safety({ max: 100, reason: 'Block damage' })
 *   @writable({ type: 'float', unit: 'celsius' })
 *   async setTemperature(value: number): Promise<void> { ... }
 * }
 * ```
 */

export { Driver, DeviceStatus, ConnectionType } from './core.js';
export { readable, writable, procedure, safety, monitor } from './decorators.js';
export { StateBus, Slot, Transform } from './state-bus.js';
export { discover, register, deregister, DeviceRegistry } from './discovery.js';
export { Manifest, generateManifest, validateManifest } from './manifest.js';
export { KHPMCPServer, createMCPServer } from './mcp-server.js';
export * from './errors.js';
export * from './types.js';
