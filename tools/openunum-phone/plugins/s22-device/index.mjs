// s22-device: the phone's hardware as OpenUnum tools, backed by s22d.
//
// There is no tool list in this file. The tools are generated from s22d's /v1/capabilities, so the
// daemon's API contract (tools/s22d/API.md) is the only place that names an endpoint, a parameter
// or a risk tier. Risky actions (SMS, calls, recovery reboot) are confirmed by s22d itself, on the
// phone's screen, before it acts; this plugin never answers that question on the owner's behalf.
//
// The device API (/v1/device) becomes one tool, phone_device; legacy tools the daemon marks
// superseded_by "phone_device" are then hidden (config legacy_tools: "uncovered" | "all").
//
// Enabled only on the phone: the /srv/s22 marker exists, or config {"enabled": true}.
// Config: plugin.json "config", overridden by $OPENUNUM_HOME/plugins.json {"s22-device": {...}}.
import fs from 'node:fs';
import { PluginBase, pluginBaseSource } from './lib/plugin-base.mjs';
import { buildRequest, toolsFromCapabilities } from './lib/capabilities.mjs';
import { validate } from './lib/schema.mjs';
import { DEVICE_TOOL, deviceTool } from './lib/device.mjs';

export { pluginBaseSource };

const TRUTHY = /^(1|true|yes|on)$/i;

export function isMobileHost({ marker = '/srv/s22', enabled = false, exists = fs.existsSync } = {}) {
  return enabled === true || TRUTHY.test(String(enabled)) || exists(marker);
}

export function resolveConfig(raw = {}) {
  return {
    daemonUrl: String(raw.daemon_url || 'http://127.0.0.1:8766').replace(/\/$/, ''),
    discoveryTimeoutMs: Math.min(60000, Math.max(500, Number(raw.discovery_timeout_ms) || 8000)),
    refreshIntervalMs: Math.max(0, Number(raw.refresh_interval_s ?? 60) * 1000),
    marker: raw.mobile_marker || '/srv/s22',
    enabled: raw.enabled,
    legacyTools: raw.legacy_tools === 'all' ? 'all' : 'uncovered'
  };
}

export class S22DevicePlugin extends PluginBase {
  constructor(manifest, ctx = {}) {
    super(manifest, ctx);
    this.settings = resolveConfig({});
    this.tools = [];
    this.skipped = [];
    this.enabled = false;
    this.reachable = null;
    this.timer = null;
    this.devTool = null;
  }

  async onInit(config = {}) {
    this.settings = resolveConfig({ ...this.config, ...config });
    this.enabled = isMobileHost({ marker: this.settings.marker, enabled: this.settings.enabled });
    if (this.enabled) await this.refresh();
  }

  async onStart() {
    if (!this.enabled || this.settings.refreshIntervalMs === 0) return;
    // Backends come and go (phoned starts late, the camera client is installed later): re-read
    // the capability list so tools appear and disappear with the daemon's own view.
    this.timer = setInterval(() => this.refresh(), this.settings.refreshIntervalMs);
    this.timer.unref?.();
  }

  async onStop() {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
  }

  async onHealth() {
    if (this.enabled) await this.refresh();
    return {
      ok: true,
      mobile_host: this.enabled,
      daemon_reachable: this.reachable === true,
      generated_tools: this.tools.length,
      skipped_capabilities: this.skipped.length
    };
  }

  getTools() {
    return this.enabled ? this.tools : [];
  }

  /** Re-read /v1/capabilities. A daemon that cannot be reached leaves the current tools alone. */
  async refresh() {
    const doc = await this._request('GET', '/v1/capabilities', undefined, this.settings.discoveryTimeoutMs);
    this.reachable = doc?.ok !== false;
    if (!this.reachable) return;
    const { tools, skipped } = toolsFromCapabilities(doc);
    const devDoc = await this._request('GET', '/v1/device', undefined, this.settings.discoveryTimeoutMs);
    const blip = devDoc?.error === 'daemon_timeout' || devDoc?.error === 'daemon_unreachable';
    const dev = blip ? this.devTool : deviceTool(devDoc);
    this.devTool = dev;
    const superseded = new Set((doc.capabilities || []).filter((c) => c?.superseded_by === DEVICE_TOOL).map((c) => c.tool));
    const keep = dev && this.settings.legacyTools === 'uncovered' ? tools.filter((t) => !superseded.has(t.name)) : tools;
    this.skipped = skipped;
    this.tools = keep.map((tool) => ({ ...tool, execute: (input) => this._execute(tool, input) }));
    if (dev) this.tools.unshift({ ...dev, execute: (input) => this._device(dev, input) });
  }

  /** phone_device: POST /v1/device/<action> with the remaining arguments; the daemon checks them. */
  async _device(tool, input = {}) {
    const args = { ...(input?.args && typeof input.args === 'object' ? input.args : input) };
    // Small models send "40" and "true"; coerce to the declared type before checking.
    for (const [k, v] of Object.entries(args)) {
      const type = tool.parameters.properties[k]?.type;
      if (typeof v !== 'string') continue;
      if (type === 'integer' && /^\s*\d+\s*%?\s*$/.test(v)) args[k] = parseInt(v, 10);
      if (type === 'boolean' && /^(true|false|on|off)$/i.test(v)) args[k] = /^(true|on)$/i.test(v);
    }
    const problems = validate(tool.parameters, args);
    if (problems.length) return { ok: false, error: 'invalid_input', details: problems };
    const action = args.action;
    delete args.action;
    const out = await this._request('POST', `/v1/device/${encodeURIComponent(action)}`, args, tool.actionTimeoutMs(action));
    if (tool.effectOf(action) !== 'read') {
      try {
        this.ctx?.log?.info?.('s22-device action', { tool: tool.name, action, ok: out?.ok !== false, code: out?.code });
      } catch { /* logging must never fail the action */ }
    }
    return out;
  }

  async _request(method, path, body, timeoutMs) {
    const abort = new AbortController();
    const timer = setTimeout(() => abort.abort(), timeoutMs);
    try {
      const res = await fetch(this.settings.daemonUrl + path, {
        method,
        signal: abort.signal,
        // The daemon's audit log records who asked.
        headers: body === undefined ? { 'x-s22-caller': 'agent' } : { 'content-type': 'application/json', 'x-s22-caller': 'agent' },
        ...(body === undefined ? {} : { body: JSON.stringify(body) })
      });
      const text = await res.text();
      let data;
      try {
        data = text ? JSON.parse(text) : {};
      } catch {
        data = { raw: text.slice(0, 1000) };
      }
      // Keep the daemon's own {ok:false, error, code}: the code (policy_denied, owner_denied, ...) is the answer.
      return res.ok ? data : { ok: false, status: res.status, ...data };
    } catch (err) {
      return {
        ok: false,
        error: err.name === 'AbortError' ? 'daemon_timeout' : 'daemon_unreachable',
        message: err.message
      };
    } finally {
      clearTimeout(timer);
    }
  }

  async _execute(tool, input = {}) {
    // OpenUnum passes either the bare arguments or {args, context}.
    const args = input?.args && typeof input.args === 'object' ? input.args : input;
    const problems = validate(tool.parameters, args);
    if (problems.length) return { ok: false, error: 'invalid_input', details: problems };

    const request = buildRequest(tool, args);
    if (request.error) return { ok: false, error: request.error };

    const out = await this._request(tool.method, request.path, request.body, tool.timeoutMs);
    if (tool.risk !== 'read') {
      try {
        this.ctx?.log?.info?.('s22-device action', {
          tool: tool.name,
          risk: tool.risk,
          path: tool.path,
          ok: out?.ok !== false,
          code: out?.code
        });
      } catch { /* logging must never fail the action */ }
    }
    return out;
  }
}

export default S22DevicePlugin;
