/**
 * KHP Device Discovery — find hardware on the network or local system.
 */

import { readFileSync, writeFileSync, readdirSync, unlinkSync, mkdirSync, existsSync } from 'node:fs';
import { join } from 'node:path';
import { homedir } from 'node:os';
import { Driver } from './core.js';
import type { DiscoveredDevice, ManifestSchema } from './types.js';

const KHP_CONFIG_DIR = join(homedir(), '.khp', 'devices');

export class DeviceRegistry {
  private configDir: string;
  private drivers: Map<string, Driver> = new Map();

  constructor(configDir?: string) {
    this.configDir = configDir || KHP_CONFIG_DIR;
    if (!existsSync(this.configDir)) {
      mkdirSync(this.configDir, { recursive: true });
    }
  }

  register(driver: Driver, host = 'localhost', port = 7400): void {
    this.drivers.set(driver.deviceId, driver);
    const manifest = driver.getManifest();
    const data = { ...manifest, _connection: { host, port } };
    const path = join(this.configDir, `${driver.deviceId}.json`);
    writeFileSync(path, JSON.stringify(data, null, 2));
  }

  deregister(deviceId: string): void {
    this.drivers.delete(deviceId);
    const path = join(this.configDir, `${deviceId}.json`);
    if (existsSync(path)) unlinkSync(path);
  }

  listDevices(deviceType?: string, status?: string): DiscoveredDevice[] {
    const devices: DiscoveredDevice[] = [];
    let files: string[];
    try {
      files = readdirSync(this.configDir).filter(f => f.endsWith('.json'));
    } catch {
      return devices;
    }

    for (const file of files) {
      try {
        const content = readFileSync(join(this.configDir, file), 'utf8');
        const manifest = JSON.parse(content);
        const conn = manifest._connection || {};
        const device: DiscoveredDevice = {
          deviceId: manifest.device_id || file.replace('.json', ''),
          name: manifest.name || 'Unknown',
          type: manifest.type || 'custom',
          host: conn.host || 'localhost',
          port: conn.port || 7400,
          status: this.drivers.has(manifest.device_id) ? 'online' : 'registered',
          driver: manifest.driver,
          tags: manifest.metadata?.tags,
        };
        if (deviceType && device.type !== deviceType) continue;
        if (status && device.status !== status) continue;
        devices.push(device);
      } catch {
        continue;
      }
    }
    return devices;
  }

  getManifest(deviceId: string): ManifestSchema | null {
    const driver = this.drivers.get(deviceId);
    if (driver) return driver.getManifest();
    const path = join(this.configDir, `${deviceId}.json`);
    if (existsSync(path)) {
      return JSON.parse(readFileSync(path, 'utf8'));
    }
    return null;
  }

  getDriver(deviceId: string): Driver | undefined {
    return this.drivers.get(deviceId);
  }
}

const _registry = new DeviceRegistry();

export function discover(deviceType?: string, capability?: string): DiscoveredDevice[] {
  let devices = _registry.listDevices(deviceType);
  if (capability) {
    devices = devices.filter(d => {
      const manifest = _registry.getManifest(d.deviceId);
      if (!manifest) return false;
      const allCaps = new Set([
        ...Object.keys(manifest.readable || {}),
        ...Object.keys(manifest.writable || {}),
        ...Object.keys(manifest.procedures || {}),
      ]);
      return allCaps.has(capability);
    });
  }
  return devices;
}

export function register(driver: Driver, host = 'localhost', port = 7400): void {
  _registry.register(driver, host, port);
}

export function deregister(deviceId: string): void {
  _registry.deregister(deviceId);
}

export function getRegistry(): DeviceRegistry {
  return _registry;
}
