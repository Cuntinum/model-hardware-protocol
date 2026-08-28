/**
 * KHP MCP Server — Exposes all connected devices as MCP tools (TypeScript).
 */

import { Driver } from './core.js';
import { getRegistry, discover, register } from './discovery.js';
import { StateBus } from './state-bus.js';
import { DeviceNotFoundError } from './errors.js';

interface MCPTool {
  name: string;
  description: string;
  inputSchema: Record<string, unknown>;
}

export class KHPMCPServer {
  private registry = getRegistry();
  private bus: StateBus;

  constructor(stateBus?: StateBus) {
    this.bus = stateBus || new StateBus();
  }

  getTools(): MCPTool[] {
    return [
      {
        name: 'khp_discover',
        description: 'Discover available hardware devices. Returns list with type, status, capabilities.',
        inputSchema: {
          type: 'object',
          properties: {
            device_type: { type: 'string', description: 'Filter by type (e.g., thermocycler)' },
            capability: { type: 'string', description: 'Filter by capability name' },
          },
        },
      },
      {
        name: 'khp_read',
        description: 'Read current value of a device property. Returns value with type, unit, timestamp.',
        inputSchema: {
          type: 'object',
          properties: {
            device_id: { type: 'string', description: 'Device ID' },
            property: { type: 'string', description: 'Property name to read' },
          },
          required: ['device_id', 'property'],
        },
      },
      {
        name: 'khp_write',
        description: 'Set a device property. Safety limits enforced — blocked if exceeds hard limits.',
        inputSchema: {
          type: 'object',
          properties: {
            device_id: { type: 'string', description: 'Device ID' },
            property: { type: 'string', description: 'Property to set' },
            value: { description: 'Value to set' },
          },
          required: ['device_id', 'property', 'value'],
        },
      },
      {
        name: 'khp_execute',
        description: 'Execute a procedure on a device. May require human confirmation.',
        inputSchema: {
          type: 'object',
          properties: {
            device_id: { type: 'string', description: 'Device ID' },
            procedure: { type: 'string', description: 'Procedure name' },
            params: { type: 'object', description: 'Procedure parameters' },
          },
          required: ['device_id', 'procedure'],
        },
      },
      {
        name: 'khp_manifest',
        description: 'Get full capabilities manifest for a device.',
        inputSchema: {
          type: 'object',
          properties: { device_id: { type: 'string' } },
          required: ['device_id'],
        },
      },
      {
        name: 'khp_emergency_stop',
        description: 'EMERGENCY STOP — halt all devices immediately.',
        inputSchema: {
          type: 'object',
          properties: { device_id: { type: 'string', description: 'Specific device (omit for all)' } },
        },
      },
    ];
  }

  async handleToolCall(toolName: string, args: Record<string, unknown>): Promise<unknown> {
    switch (toolName) {
      case 'khp_discover':
        return { devices: discover(args.device_type as string, args.capability as string) };

      case 'khp_read': {
        const driver = this.registry.getDriver(args.device_id as string);
        if (!driver) throw new DeviceNotFoundError(args.device_id as string);
        return driver.read(args.property as string);
      }

      case 'khp_write': {
        const driver = this.registry.getDriver(args.device_id as string);
        if (!driver) throw new DeviceNotFoundError(args.device_id as string);
        return driver.write(args.property as string, args.value);
      }

      case 'khp_execute': {
        const driver = this.registry.getDriver(args.device_id as string);
        if (!driver) throw new DeviceNotFoundError(args.device_id as string);
        return driver.execute(args.procedure as string, (args.params as Record<string, unknown>) || {});
      }

      case 'khp_manifest': {
        const manifest = this.registry.getManifest(args.device_id as string);
        if (!manifest) throw new DeviceNotFoundError(args.device_id as string);
        return manifest;
      }

      case 'khp_emergency_stop': {
        const stopped: string[] = [];
        if (args.device_id) {
          const driver = this.registry.getDriver(args.device_id as string);
          if (driver) { await driver.emergencyStop(); stopped.push(driver.deviceId); }
        } else {
          for (const dev of this.registry.listDevices()) {
            const driver = this.registry.getDriver(dev.deviceId);
            if (driver) { await driver.emergencyStop(); stopped.push(dev.deviceId); }
          }
        }
        return { emergency_stop: true, devices_stopped: stopped };
      }

      default:
        return { error: `Unknown tool: ${toolName}` };
    }
  }
}

export function createMCPServer(drivers?: Driver[], stateBus?: StateBus): KHPMCPServer {
  const server = new KHPMCPServer(stateBus);
  if (drivers) {
    for (const driver of drivers) {
      register(driver);
    }
  }
  return server;
}
