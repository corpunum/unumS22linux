// One agent tool, `phone_device`, over s22d's device API (GET /v1/device, POST /v1/device/<action>).
// The daemon owns the action list, the argument names and their checks (tools/s22d/DEVICE_API.md);
// this module only turns its catalog into one small schema: `action` plus the union of the flat
// argument names (on, value, muted, locked, number, text, pattern, sensor, force). Small local
// models handle one tool with an enum better than twenty-five near-identical tools.

export const DEVICE_TOOL = 'phone_device';
const ACTION = /^[a-z][a-z_]*(\.[a-z_]+)?$/;
const EFFECTS = new Set(['read', 'set', 'action']);

/** Usable rows of the device catalog, or [] when the document is not a device/1 catalog. */
export function deviceActions(doc) {
  if (!doc || doc.ok === false || doc.api !== 'device/1' || !Array.isArray(doc.capabilities)) return [];
  return doc.capabilities.filter((c) => c && c.available !== false && ACTION.test(String(c.id || ''))
    && EFFECTS.has(c.effect) && c.args?.type === 'object');
}

function argSpec(spec) {
  // Keep the type and the hard limits; descriptions live in the tool text once.
  const out = { type: spec.type };
  for (const k of ['enum', 'minimum', 'maximum', 'maxLength']) if (spec[k] !== undefined) out[k] = spec[k];
  return out;
}

/** The phone_device tool definition, or null when the daemon has no device API. */
export function deviceTool(doc) {
  const actions = deviceActions(doc);
  if (!actions.length) return null;
  const properties = { action: { type: 'string', enum: actions.map((a) => a.id) } };
  for (const a of actions) {
    for (const [name, spec] of Object.entries(a.args.properties || {})) {
      if (name === 'action') continue;
      const cur = properties[name];
      const next = argSpec(spec);
      if (!cur) properties[name] = next;
      else if (cur.type !== next.type) properties[name] = {};           // conflicting types: let the daemon check
      else {
        // Same name, different ranges (value is 0-10 for volume, 1-100 for brightness): the widest.
        if (cur.minimum !== undefined) cur.minimum = next.minimum === undefined ? undefined : Math.min(cur.minimum, next.minimum);
        if (cur.maximum !== undefined) cur.maximum = next.maximum === undefined ? undefined : Math.max(cur.maximum, next.maximum);
        if (cur.maxLength !== undefined) cur.maxLength = next.maxLength === undefined ? undefined : Math.max(cur.maxLength, next.maxLength);
        if (cur.enum && !next.enum) delete cur.enum;
        for (const k of ['minimum', 'maximum', 'maxLength']) if (cur[k] === undefined) delete cur[k];
      }
    }
  }
  const list = actions.map((a) => {
    const names = Object.keys(a.args.properties || {}).filter((n) => n !== 'force');
    return names.length ? `${a.id}(${names.join(',')})` : a.id;
  }).join('; ');
  const timeouts = new Map(actions.map((a) => [a.id, Math.min(600000, Math.max(1000, Math.round((Number(a.timeout_s) || 20) * 1000)))]));
  return {
    name: DEVICE_TOOL,
    description: 'Read or change this phone: settings (Wi-Fi, Bluetooth, mobile data, volume 0-10, '
      + 'brightness %, vibration, autosleep, screen timeout), calls, SMS, photo, screenshot. '
      + `Actions: ${list}. `
      + 'Set actions answer with the new state read back from the phone; trust that, not the request. '
      + 'Calls/SMS to numbers not on the phone allowlist wait for the owner to tap yes on the screen '
      + '(needs_confirmation if nobody does; owner_denied is final). Call audio does not work yet.',
    parameters: { type: 'object', properties, required: ['action'], additionalProperties: false },
    semantics: { actionClass: 'mutating', evidenceRole: 'mutation' },
    capability: 'device',
    risk: 'reversible',
    method: 'POST',
    path: '/v1/device/{action}',
    timeoutMs: Math.max(...timeouts.values()),
    actionTimeoutMs: (action) => timeouts.get(action) ?? 30000,
    effectOf: (action) => actions.find((a) => a.id === action)?.effect ?? null
  };
}
