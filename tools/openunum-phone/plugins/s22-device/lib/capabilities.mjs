// Turns s22d's /v1/capabilities into agent tools. The daemon owns the tool names, descriptions and
// input schemas (tools/s22d/API.md); this module only validates what the daemon advertises and
// refuses anything that looks wrong.

const METHODS = new Set(['GET', 'POST']);
const RISKS = new Set(['read', 'reversible', 'risky']);
const TOOL_NAME = /^[a-z][a-z0-9_]{1,48}$/;

// Capabilities that must always be risky. If the daemon ever advertises one of these with a lower
// tier the tool is not exposed at all. The daemon itself asks the owner; this is a second lock.
export const RISK_FLOOR = {
  'sms.send': 'risky',
  'call.dial': 'risky',
  'system.reboot-recovery': 'risky'
};

const SEMANTICS = {
  read: { actionClass: 'observational', evidenceRole: 'observation' },
  reversible: { actionClass: 'mutating', evidenceRole: 'mutation' },
  risky: { actionClass: 'mutating', evidenceRole: 'mutation' }
};

/** Why a capability row cannot become a tool, or null if it can. */
export function rejectReason(cap) {
  if (!cap || typeof cap !== 'object') return 'not an object';
  if (cap.available === false) return 'unavailable';
  if (!TOOL_NAME.test(String(cap.tool || ''))) return 'bad tool name';
  if (!METHODS.has(String(cap.method).toUpperCase())) return 'unsupported method';
  if (typeof cap.path !== 'string' || !cap.path.startsWith('/v1/') || cap.path.includes('..')) return 'bad path';
  if (!RISKS.has(cap.risk)) return 'unknown risk tier';
  if (RISK_FLOOR[cap.id] && cap.risk !== RISK_FLOOR[cap.id]) return `${cap.id} must be ${RISK_FLOOR[cap.id]}`;
  if (!cap.input_schema || cap.input_schema.type !== 'object') return 'missing input schema';
  return null;
}

export function toolDefinition(cap) {
  const risky = cap.risk === 'risky';
  const note = risky
    ? ' Risk tier: risky. The phone asks its owner to tap yes; the call waits for the answer and a refusal is final.'
    : ` Risk tier: ${cap.risk}.`;
  return {
    name: cap.tool,
    description: `${cap.description}${note}`,
    parameters: cap.input_schema,
    semantics: SEMANTICS[cap.risk],
    capability: cap.id,
    risk: cap.risk,
    method: String(cap.method).toUpperCase(),
    path: cap.path,
    timeoutMs: Math.min(600000, Math.max(500, Math.round((Number(cap.timeout_s) || 15) * 1000)))
  };
}

/** Tools for every usable capability row, plus the rows that were skipped and why. */
export function toolsFromCapabilities(doc) {
  const rows = Array.isArray(doc) ? doc : doc?.capabilities;
  const tools = [];
  const skipped = [];
  if (!Array.isArray(rows)) return { tools, skipped: [{ id: null, reason: 'no capabilities array' }] };
  const seen = new Set();
  for (const cap of rows) {
    const reason = rejectReason(cap);
    if (reason) {
      skipped.push({ id: cap?.id ?? null, reason });
    } else if (seen.has(cap.tool)) {
      skipped.push({ id: cap.id, reason: 'duplicate tool name' });
    } else {
      seen.add(cap.tool);
      tools.push(toolDefinition(cap));
    }
  }
  return { tools, skipped };
}

const SERVICE_NAME = /^[a-zA-Z0-9_.@-]{1,64}$/;

/** Split validated input into the request path, query string and JSON body. */
export function buildRequest(tool, input) {
  const args = { ...input };
  let path = tool.path;
  for (const [, key] of tool.path.matchAll(/\{(\w+)\}/g)) {
    const value = String(args[key] ?? '');
    if (!SERVICE_NAME.test(value)) return { error: `invalid_${key}` };
    path = path.replace(`{${key}}`, encodeURIComponent(value));
    delete args[key];
  }
  if (tool.method === 'GET') {
    const query = new URLSearchParams();
    for (const [key, value] of Object.entries(args)) {
      if (value !== undefined) query.set(key, String(value));
    }
    const qs = query.toString();
    return { path: qs ? `${path}?${qs}` : path };
  }
  return { path, body: args };
}
