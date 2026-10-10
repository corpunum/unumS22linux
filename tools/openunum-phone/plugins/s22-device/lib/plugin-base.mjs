// Resolve OpenUnum's PluginBase for a plugin installed OUTSIDE the source tree.
//
// The v2.10.0 registry (src/plugins/plugin-registry.mjs, load()) rejects any
// plugin whose default export is not `prototype instanceof PluginBase`, where
// PluginBase is the registry's own module instance. A user plugin lives in
// $OPENUNUM_HOME/plugins/<name>/, so the bundled plugins' `../plugin-base.mjs`
// import does not resolve there. We locate the server's copy and import it by
// its real path: ESM caches by URL and the server's own imports are realpath
// URLs, so this yields the SAME class object the registry checks against.
//
// Lookup order (first existing file wins):
//   S22_PHONE_PLUGIN_BASE=<file> | "builtin"  (explicit override; tests use "builtin")
//   $OPENUNUM_SRC_DIR/plugins/plugin-base.mjs
//   dirname(process.argv[1])/plugins/plugin-base.mjs  (argv[1] = .../src/server.mjs)
//   $PWD/src/plugins/plugin-base.mjs                    (phone: cd /opt/openunum)
//   /opt/openunum/src/plugins/plugin-base.mjs
// If none is found a compatible local class is used; the plugin then works for
// direct use and tests, but the real registry will refuse it (logged as
// "must export a class extending PluginBase") - set OPENUNUM_SRC_DIR then.
import fs from 'node:fs';
import path from 'node:path';
import { EventEmitter } from 'node:events';
import { pathToFileURL } from 'node:url';

class FallbackPluginBase extends EventEmitter {
  constructor(manifest, ctx = {}) {
    super();
    this.name = manifest.name;
    this.version = manifest.version;
    this.type = manifest.type;
    this.capabilities = manifest.capabilities || [];
    this.pluginDir = manifest.dir || '';
    this.config = {};
    this.ctx = ctx;
    this.state = 'idle';
    this._error = null;
  }
  async init(pluginConfig = {}) {
    this.state = 'initializing';
    this.config = pluginConfig;
    try { await this.onInit(pluginConfig); this.state = 'initialized'; } catch (err) { this.state = 'error'; this._error = err; }
  }
  async start() {
    if (this.state === 'error' || this.state === 'idle') return;
    this.state = 'starting';
    try { await this.onStart(); this.state = 'running'; } catch (err) { this.state = 'error'; this._error = err; }
  }
  async stop() {
    if (this.state === 'idle' || this.state === 'stopped') return;
    try { await this.onStop(); this.state = 'stopped'; } catch (err) { this.state = 'error'; this._error = err; }
    this.removeAllListeners();
  }
  async health() {
    try {
      const result = await this.onHealth();
      const ok = result.ok !== undefined ? result.ok : true;
      return { ok, plugin: this.name, state: this.state, ...result };
    } catch (err) {
      return { ok: false, plugin: this.name, state: this.state, error: err.message };
    }
  }
  getStatus() {
    return { name: this.name, version: this.version, type: this.type, state: this.state, capabilities: this.capabilities, error: this._error?.message || null };
  }
  async onInit() {}
  async onStart() {}
  async onStop() {}
  async onHealth() { return {}; }
  getTools() { return []; }
  getHooks() { return []; }
  getRoutes() { return []; }
}

export function pluginBaseCandidates(env = process.env, argv = process.argv, cwd = process.cwd()) {
  const out = [];
  const explicit = String(env.S22_PHONE_PLUGIN_BASE || '').trim();
  if (explicit) return explicit === 'builtin' ? [] : [explicit];
  if (env.OPENUNUM_SRC_DIR) out.push(path.join(env.OPENUNUM_SRC_DIR, 'plugins', 'plugin-base.mjs'));
  if (argv && argv[1]) out.push(path.join(path.dirname(path.resolve(argv[1])), 'plugins', 'plugin-base.mjs'));
  out.push(path.join(cwd, 'src', 'plugins', 'plugin-base.mjs'));
  out.push('/opt/openunum/src/plugins/plugin-base.mjs');
  return out;
}

async function resolvePluginBase() {
  for (const candidate of pluginBaseCandidates()) {
    try {
      if (!fs.existsSync(candidate)) continue;
      const real = fs.realpathSync(candidate);
      const mod = await import(pathToFileURL(real).href);
      if (typeof mod.PluginBase === 'function') return { PluginBase: mod.PluginBase, source: real };
    } catch { /* try next candidate */ }
  }
  return { PluginBase: FallbackPluginBase, source: 'builtin-fallback' };
}

const resolved = await resolvePluginBase();
export const PluginBase = resolved.PluginBase;
export const pluginBaseSource = resolved.source;
