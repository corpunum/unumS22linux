// Contract tests: the plugin against a fake s22d that is built from the daemon's own
// capabilities.json (tools/s22d/capabilities.json, checked by the s22d test suite).
import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import Plugin from '../index.mjs';
import { RISK_FLOOR, buildRequest, toolsFromCapabilities } from '../lib/capabilities.mjs';
import { sampleInput, validate } from '../lib/schema.mjs';

const here = path.dirname(fileURLToPath(import.meta.url));
const SPEC = JSON.parse(fs.readFileSync(path.join(here, '../../../../s22d/capabilities.json'), 'utf8'));
const clone = (x) => JSON.parse(JSON.stringify(x));

// ---------------------------------------------------------------- a fake s22d

function matchRoute(caps, method, pathname) {
  for (const cap of caps) {
    if (cap.method !== method) continue;
    const pattern = new RegExp(`^${cap.path.replace(/\{\w+\}/g, '([^/]+)')}$`);
    const m = pathname.match(pattern);
    if (m) return { cap, params: m.slice(1) };
  }
  return null;
}

/** Plays s22d: serves the spec, checks every request against it, and records what it saw. */
async function startDaemon(spec = SPEC, { delayMs = 0, answer } = {}) {
  const seen = [];
  const server = http.createServer(async (req, res) => {
    let raw = '';
    for await (const chunk of req) raw += chunk;
    const url = new URL(req.url, 'http://127.0.0.1');
    const send = (status, body) => {
      res.writeHead(status, { 'content-type': 'application/json' });
      res.end(JSON.stringify(body));
    };
    if (req.method === 'GET' && url.pathname === '/v1/capabilities') return send(200, spec);
    const route = matchRoute(spec.capabilities, req.method, url.pathname);
    if (!route) return send(404, { ok: false, code: 'not_found', error: 'no such endpoint' });
    // s22d reads GET parameters from the query string and POST parameters from the JSON body.
    const params = req.method === 'GET' ? Object.fromEntries(url.searchParams) : raw ? JSON.parse(raw) : {};
    if (req.method === 'GET') {
      for (const [key, value] of Object.entries(params)) {
        const type = route.cap.input_schema.properties?.[key]?.type;
        if (type === 'integer' || type === 'number') params[key] = Number(value);
      }
    }
    const pathParam = route.cap.path.match(/\{(\w+)\}/)?.[1];
    if (pathParam) params[pathParam] = decodeURIComponent(route.params[0]);
    seen.push({ id: route.cap.id, method: req.method, path: url.pathname, query: url.search, body: raw ? JSON.parse(raw) : null, params });
    const problems = validate(route.cap.input_schema, params);
    if (problems.length) return send(400, { ok: false, code: 'bad_request', error: problems.join('; ') });
    if (delayMs) await new Promise((r) => setTimeout(r, delayMs));
    if (answer) return send(...answer(route.cap, params));
    return send(200, { ok: true, id: route.cap.id });
  });
  await new Promise((r) => server.listen(0, '127.0.0.1', r));
  return {
    url: `http://127.0.0.1:${server.address().port}`,
    seen,
    close: () => new Promise((r) => { server.closeAllConnections(); server.close(r); })
  };
}

async function makePlugin(daemon, config = {}, ctx = {}) {
  const plugin = new Plugin({ name: 's22-device', version: '0.2.0', type: 'tool' }, ctx);
  await plugin.onInit({ enabled: true, daemon_url: daemon.url, mobile_marker: '/definitely/missing', ...config });
  return plugin;
}

const toolByName = (plugin, name) => plugin.getTools().find((t) => t.name === name);

// ---------------------------------------------------------------- the spec itself

test('spec: every usable capability becomes exactly one tool with the daemon schema', async () => {
  const daemon = await startDaemon();
  const plugin = await makePlugin(daemon);
  const usable = SPEC.capabilities.filter((c) => c.available);
  assert.equal(plugin.getTools().length, usable.length);
  for (const cap of usable) {
    const tool = toolByName(plugin, cap.tool);
    assert.ok(tool, `no tool for ${cap.id}`);
    assert.deepEqual(tool.parameters, cap.input_schema);
    assert.equal(tool.risk, cap.risk);
    assert.match(tool.description, new RegExp(`Risk tier: ${cap.risk}`));
  }
  await daemon.close();
});

test('spec: unavailable capabilities (Bluetooth power and scan) get no tool', async () => {
  const daemon = await startDaemon();
  const plugin = await makePlugin(daemon);
  const names = plugin.getTools().map((t) => t.name);
  assert.ok(!names.includes('bt_power') && !names.includes('bt_scan'));
  assert.ok(names.includes('bt_status'));
  await daemon.close();
});

test('spec: the three risky capabilities are exactly the ones the plugin locks to risky', () => {
  const risky = SPEC.capabilities.filter((c) => c.risk === 'risky').map((c) => c.id).sort();
  assert.deepEqual(risky, Object.keys(RISK_FLOOR).sort());
  for (const cap of SPEC.capabilities.filter((c) => c.risk === 'risky')) assert.equal(cap.confirm, 'owner');
});

test('contract: every tool calls exactly its method and path with the parameters the schema names', async () => {
  const daemon = await startDaemon();
  const plugin = await makePlugin(daemon);
  for (const cap of SPEC.capabilities.filter((c) => c.available)) {
    const input = sampleInput(cap.input_schema);
    const before = daemon.seen.length;
    const out = await toolByName(plugin, cap.tool).execute(input);
    assert.equal(out.ok, true, `${cap.id}: ${JSON.stringify(out)}`);
    assert.equal(daemon.seen.length, before + 1, cap.id);
    const call = daemon.seen.at(-1);
    assert.equal(call.id, cap.id);
    assert.equal(call.method, cap.method);
    if (cap.method === 'POST') {
      const pathParams = [...cap.path.matchAll(/\{(\w+)\}/g)].map((m) => m[1]);
      const expected = Object.fromEntries(Object.entries(input).filter(([k]) => !pathParams.includes(k)));
      assert.deepEqual(call.body, expected, cap.id);
    }
  }
  await daemon.close();
});

// ---------------------------------------------------------------- the three old mismatches

test('device_sms_send takes number and text (the daemon field is "number", not "to")', async () => {
  const daemon = await startDaemon();
  const plugin = await makePlugin(daemon);
  const tool = toolByName(plugin, 'device_sms_send');
  assert.deepEqual(tool.parameters.required, ['number', 'text']);
  assert.equal(tool.parameters.properties.to, undefined);
  const out = await tool.execute({ number: '+306900000000', text: 'hi' });
  assert.equal(out.ok, true);
  assert.deepEqual(daemon.seen.at(-1).body, { number: '+306900000000', text: 'hi' });
  const wrong = await tool.execute({ to: '+306900000000', text: 'hi' });
  assert.equal(wrong.error, 'invalid_input');
  assert.equal(daemon.seen.length, 1, 'the wrong field name never reaches the daemon');
  await daemon.close();
});

test('display is split into display_on / display_off / display_brightness', async () => {
  const daemon = await startDaemon();
  const plugin = await makePlugin(daemon);
  await toolByName(plugin, 'display_on').execute({});
  await toolByName(plugin, 'display_off').execute({});
  await toolByName(plugin, 'display_brightness').execute({ percent: 40 });
  assert.deepEqual(daemon.seen.map((s) => s.path), ['/v1/display/on', '/v1/display/off', '/v1/display/brightness']);
  assert.deepEqual(daemon.seen[2].body, { percent: 40 });
  assert.equal(toolByName(plugin, 'display_set'), undefined);
  await daemon.close();
});

test('audio: reading is a GET, setting is a POST, and the daemon refusal is passed back', async () => {
  const daemon = await startDaemon(SPEC, {
    answer: (cap, params) =>
      cap.id === 'audio.volume.set' && params.value !== 0
        ? [403, { ok: false, code: 'policy_denied', error: 'phone audio is muted by owner policy' }]
        : [200, { ok: true }]
  });
  const plugin = await makePlugin(daemon);
  await toolByName(plugin, 'audio_volume').execute({});
  assert.deepEqual([daemon.seen[0].method, daemon.seen[0].path], ['GET', '/v1/audio/volume']);
  const refused = await toolByName(plugin, 'audio_mute').execute({ value: 30 });
  assert.deepEqual([refused.ok, refused.code, refused.status], [false, 'policy_denied', 403]);
  const muted = await toolByName(plugin, 'audio_mute').execute({ value: 0 });
  assert.equal(muted.ok, true);
  assert.deepEqual([daemon.seen[1].method, daemon.seen[1].path], ['POST', '/v1/audio/volume']);
  await daemon.close();
});

// ---------------------------------------------------------------- behaviour

test('GET parameters go in the query string', async () => {
  const daemon = await startDaemon();
  const plugin = await makePlugin(daemon);
  await toolByName(plugin, 'processes_top').execute({ sort: 'mem', limit: 3 });
  assert.equal(daemon.seen.at(-1).query, '?sort=mem&limit=3');
  assert.equal(daemon.seen.at(-1).params.limit, 3);
  await daemon.close();
});

test('service_restart puts the name in the path and rejects odd names before the network', async () => {
  const daemon = await startDaemon();
  const plugin = await makePlugin(daemon);
  const tool = toolByName(plugin, 'service_restart');
  await tool.execute({ name: 'phoned' });
  assert.equal(daemon.seen.at(-1).path, '/v1/services/phoned/restart');
  assert.deepEqual(daemon.seen.at(-1).body, {});
  const before = daemon.seen.length;
  for (const bad of ['sshd', '../etc', 'a/b', '']) {
    const out = await tool.execute({ name: bad });
    assert.equal(out.ok, false, bad);
  }
  assert.equal(daemon.seen.length, before);
  assert.equal(buildRequest({ method: 'POST', path: '/v1/services/{name}/restart' }, { name: 'a/b' }).error, 'invalid_name');
  await daemon.close();
});

test('risky tools wait for the daemon (which asks the owner) and pass its answer back unchanged', async () => {
  const answers = {
    yes: [200, { ok: true, status: 'sent' }],
    no: [403, { ok: false, code: 'owner_denied', error: 'the owner declined on the phone' }],
    late: [408, { ok: false, code: 'owner_timeout', error: 'the owner did not answer in time' }]
  };
  let next = 'yes';
  const daemon = await startDaemon(SPEC, { answer: () => answers[next] });
  const plugin = await makePlugin(daemon);
  const tool = toolByName(plugin, 'device_sms_send');
  const input = { number: '+306900000000', text: 'hello' };
  assert.equal((await tool.execute(input)).status, 'sent');
  next = 'no';
  const denied = await tool.execute(input);
  assert.deepEqual([denied.ok, denied.code], [false, 'owner_denied']);
  next = 'late';
  assert.equal((await tool.execute(input)).code, 'owner_timeout');
  assert.equal(daemon.seen.length, 3, 'each attempt is exactly one request: no retry around a refusal');
  assert.equal(tool.timeoutMs, 95000);
  await daemon.close();
});

test('the plugin has no way to confirm on the owner\'s behalf', async () => {
  const daemon = await startDaemon();
  const plugin = await makePlugin(daemon);
  const tool = toolByName(plugin, 'recovery_mode');
  assert.deepEqual(tool.parameters.properties, {});
  assert.equal(plugin._confirm, undefined);
  const out = await tool.execute({ confirmed: true });
  assert.equal(out.error, 'invalid_input', 'extra fields such as confirmed are not forwarded');
  await daemon.close();
});

test('input is validated against the daemon schema before any request', async () => {
  const daemon = await startDaemon();
  const plugin = await makePlugin(daemon);
  const connect = toolByName(plugin, 'wifi_connect');
  for (const bad of [{}, { ssid: '' }, { ssid: 'x'.repeat(33) }, { ssid: 'ok', password: 'short' }, { ssid: 5 }, { ssid: 'ok', extra: 1 }]) {
    const out = await connect.execute(bad);
    assert.equal(out.error, 'invalid_input', JSON.stringify(bad));
  }
  const cam = toolByName(plugin, 'camera_capture');
  assert.equal((await cam.execute({ sensor: 'top' })).error, 'invalid_input');
  assert.equal((await cam.execute({ sensor: 'rear', exposure_us: 5 })).error, 'invalid_input');
  assert.equal(daemon.seen.length, 0);
  assert.equal((await cam.execute({ sensor: 'front', exposure_us: 12000, gain: 2 })).ok, true);
  await daemon.close();
});

test('OpenUnum may pass {args, context}; args are what is sent', async () => {
  const daemon = await startDaemon();
  const plugin = await makePlugin(daemon);
  await toolByName(plugin, 'wifi_connect').execute({ args: { ssid: 'Lab' }, context: { sessionId: 's' } });
  assert.deepEqual(daemon.seen.at(-1).body, { ssid: 'Lab' });
  await daemon.close();
});

test('reversible and risky actions are logged, reads are not, and passwords are not', async () => {
  const daemon = await startDaemon();
  const logs = [];
  const plugin = await makePlugin(daemon, {}, { log: { info: (...x) => logs.push(x) } });
  await toolByName(plugin, 'device_status').execute({});
  assert.equal(logs.length, 0);
  await toolByName(plugin, 'wifi_connect').execute({ ssid: 'Lab', password: 'hunter2hunter2' });
  assert.equal(logs.length, 1);
  assert.equal(logs[0][1].risk, 'reversible');
  assert.equal(JSON.stringify(logs).includes('hunter2'), false);
  await daemon.close();
});

// ---------------------------------------------------------------- discovery

test('a daemon advertising a lower tier for a risky capability gets no such tool', () => {
  for (const id of Object.keys(RISK_FLOOR)) {
    const doc = clone(SPEC);
    doc.capabilities.find((c) => c.id === id).risk = 'reversible';
    const { tools, skipped } = toolsFromCapabilities(doc);
    assert.ok(!tools.some((t) => t.capability === id), id);
    assert.ok(skipped.some((s) => s.id === id && /must be risky/.test(s.reason)), id);
  }
});

test('malformed rows are skipped, not trusted', () => {
  const doc = clone(SPEC);
  const good = doc.capabilities[0];
  doc.capabilities.push(
    { ...good, id: 'x1', tool: 'Bad Name' },
    { ...good, id: 'x2', tool: 'x_two', method: 'DELETE' },
    { ...good, id: 'x3', tool: 'x_three', path: '/etc/passwd' },
    { ...good, id: 'x4', tool: 'x_four', path: '/v1/../etc' },
    { ...good, id: 'x5', tool: 'x_five', risk: 'harmless' },
    { ...good, id: 'x6', tool: 'x_six', input_schema: undefined },
    { ...good, id: 'x7', tool: good.tool },
    null
  );
  const { tools, skipped } = toolsFromCapabilities(doc);
  assert.equal(tools.length, SPEC.capabilities.filter((c) => c.available).length);
  assert.equal(skipped.filter((s) => /^x\d$/.test(s.id)).length, 7);
  assert.equal(toolsFromCapabilities({}).tools.length, 0);
  assert.equal(toolsFromCapabilities({ capabilities: 'nope' }).tools.length, 0);
});

test('desktop without marker or opt-in exposes no tools and never contacts the daemon', async () => {
  const daemon = await startDaemon();
  const plugin = new Plugin({ name: 's22-device', version: '0.2.0', type: 'tool' }, {});
  await plugin.onInit({ enabled: false, daemon_url: daemon.url, mobile_marker: '/definitely/missing' });
  assert.deepEqual(plugin.getTools(), []);
  assert.equal(daemon.seen.length, 0);
  assert.equal((await plugin.onHealth()).mobile_host, false);
  await daemon.close();
});

test('refresh follows the daemon: tools appear when a backend becomes available', async () => {
  const spec = clone(SPEC);
  const sms = spec.capabilities.find((c) => c.id === 'sms.send');
  sms.available = false;
  sms.reason = 's22-phoned socket missing';
  const daemon = await startDaemon(spec);
  const plugin = await makePlugin(daemon);
  assert.equal(toolByName(plugin, 'device_sms_send'), undefined);
  sms.available = true;
  sms.reason = null;
  const health = await plugin.onHealth();
  assert.equal(health.daemon_reachable, true);
  assert.ok(toolByName(plugin, 'device_sms_send'));
  await daemon.close();
});

test('an unreachable daemon yields no tools, then recovers; known tools survive a blip', async () => {
  const dead = http.createServer();
  await new Promise((r) => dead.listen(0, '127.0.0.1', r));
  const url = `http://127.0.0.1:${dead.address().port}`;
  await new Promise((r) => dead.close(r));
  const plugin = new Plugin({ name: 's22-device', version: '0.2.0', type: 'tool' }, {});
  await plugin.onInit({ enabled: true, daemon_url: url, discovery_timeout_ms: 800 });
  assert.deepEqual(plugin.getTools(), []);
  assert.equal((await plugin.onHealth()).daemon_reachable, false);

  const daemon = await startDaemon();
  plugin.settings.daemonUrl = daemon.url;
  await plugin.refresh();
  const count = plugin.getTools().length;
  assert.ok(count > 10);
  await daemon.close();
  await plugin.refresh();
  assert.equal(plugin.getTools().length, count);
  const out = await toolByName(plugin, 'device_status').execute({});
  assert.deepEqual([out.ok, out.error], [false, 'daemon_unreachable']);
});

test('a slow daemon times out per capability', async () => {
  const spec = clone(SPEC);
  spec.capabilities.find((c) => c.id === 'status').timeout_s = 0.6;
  const daemon = await startDaemon(spec, { delayMs: 1500 });
  const plugin = await makePlugin(daemon);
  const out = await toolByName(plugin, 'device_status').execute({});
  assert.equal(out.error, 'daemon_timeout');
  await daemon.close();
});

test('periodic refresh is started and stopped with the plugin', async () => {
  const daemon = await startDaemon();
  const plugin = await makePlugin(daemon, { refresh_interval_s: 3600 });
  await plugin.onStart();
  assert.ok(plugin.timer);
  await plugin.onStop();
  assert.equal(plugin.timer, null);
  await daemon.close();
});

// ---------------------------------------------------------------- the schema checker

test('schema checker covers the keywords s22d uses', () => {
  const s = {
    type: 'object',
    properties: {
      a: { type: 'string', minLength: 2, maxLength: 3 },
      b: { type: 'integer', minimum: 1, maximum: 5 },
      c: { type: 'string', enum: ['x', 'y'] },
      d: { type: 'boolean' },
      e: { type: 'number' }
    },
    required: ['a'],
    additionalProperties: false
  };
  assert.deepEqual(validate(s, { a: 'ab', b: 2, c: 'x', d: true, e: 1.5 }), []);
  assert.equal(validate(s, {}).length, 1);
  assert.equal(validate(s, { a: 'a' }).length, 1);
  assert.equal(validate(s, { a: 'abcd' }).length, 1);
  assert.equal(validate(s, { a: 'ab', b: 0 }).length, 1);
  assert.equal(validate(s, { a: 'ab', b: 6 }).length, 1);
  assert.equal(validate(s, { a: 'ab', b: 1.5 }).length, 1);
  assert.equal(validate(s, { a: 'ab', c: 'z' }).length, 1);
  assert.equal(validate(s, { a: 'ab', d: 'yes' }).length, 1);
  assert.equal(validate(s, { a: 'ab', e: NaN }).length, 1);
  assert.equal(validate(s, { a: 'ab', f: 1 }).length, 1);
  assert.equal(validate(s, null).length, 1);
});

test('no tool name collides with another phone plugin (OpenUnum runs the first match, so a clash shadows its policy)', () => {
  const plugins = path.join(here, '../../');
  const others = ['s22-phone', 's22-ui']
    .flatMap((p) => JSON.parse(fs.readFileSync(path.join(plugins, p, 'plugin.json'), 'utf8')).capabilities);
  assert.ok(others.includes('sms_send'), 'fixture sanity: s22-phone exposes sms_send');
  const mine = SPEC.capabilities.map((c) => c.tool);
  assert.deepEqual(mine.filter((t) => others.includes(t)), []);
});
