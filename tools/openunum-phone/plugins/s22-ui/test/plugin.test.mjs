// Hardware-free tests for the s22-ui OpenUnum plugin.
// Run: node --test tools/openunum-phone/plugins/s22-ui/test/plugin.test.mjs
// A fake s22-touchd listens on a temp unix socket and answers confirm requests
// by writing the result file the way the QML shell does.
process.env.S22_PHONE_PLUGIN_BASE = 'builtin';
delete process.env.S22_UI_SOCKET;
delete process.env.S22_UI_RUN_DIR;

import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import net from 'node:net';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const { default: S22UiPlugin, resolveConfig, APPS } = await import(path.join(HERE, '..', 'index.mjs'));

const dir = fs.mkdtempSync(path.join(os.tmpdir(), 's22-ui-test-'));
const sock = path.join(dir, 'ctl.sock');
const requests = [];
let confirmAnswer = 'yes';
let server;

function fakeTouchd(req) {
  requests.push(req);
  if (req.cmd === 'status') return { ok: true, muted: true, display: 'on', volume_level: 0, amps: [0, 0] };
  if (req.cmd === 'display') return { ok: true, display: req.arg === 'off' ? 'off' : 'on' };
  if (req.cmd !== 'ui') return { ok: false, error: 'unknown_command' };
  const [fn, ...a] = req.args;
  if (fn === 'confirm') {
    const [id] = a;
    if (confirmAnswer !== null) {
      setTimeout(() => {
        fs.mkdirSync(path.join(dir, 'confirm'), { recursive: true });
        fs.writeFileSync(path.join(dir, 'confirm', `${id}.json`), JSON.stringify({ id, answer: confirmAnswer }));
      }, 50);
    }
    return { ok: true, result: 'asking' };
  }
  if (fn === 'state') return { ok: true, result: JSON.stringify({ home: true, page: '', locked: false }) };
  if (fn === 'open') return { ok: true, result: 'ok' };
  return { ok: true, result: 'ok' };
}

before(async () => {
  server = net.createServer((c) => {
    let buf = '';
    c.on('data', (d) => {
      buf += d;
      if (buf.includes('\n')) c.end(JSON.stringify(fakeTouchd(JSON.parse(buf))) + '\n');
    });
  });
  await new Promise((r) => server.listen(sock, r));
});
after(async () => {
  await new Promise((r) => server.close(r));
  fs.rmSync(dir, { recursive: true, force: true });
});

async function plugin(extra = {}) {
  const p = new S22UiPlugin({ name: 's22-ui', version: '0', type: 'tool', capabilities: [] }, {});
  await p.init({ socket_path: sock, run_dir: dir, confirm_poll_ms: 20, ...extra });
  return p;
}
const tool = (p, name) => p.getTools().find((t) => t.name === name);

test('resolveConfig defaults and env override', () => {
  const c = resolveConfig({}, {});
  assert.equal(c.socketPath, '/run/s22-touch/ctl.sock');
  assert.equal(resolveConfig({}, { S22_UI_SOCKET: '/x.sock' }).socketPath, '/x.sock');
});

test('tool names match the manifest capabilities', async () => {
  const p = await plugin();
  const manifest = JSON.parse(fs.readFileSync(path.join(HERE, '..', 'plugin.json'), 'utf8'));
  assert.deepEqual(p.getTools().map((t) => t.name).sort(), [...manifest.capabilities].sort());
});

test('ui_open_app validates and forwards', async () => {
  const p = await plugin();
  assert.equal((await tool(p, 'ui_open_app').execute({ args: { app: 'nope' } })).error, 'unknown_app');
  const r = await tool(p, 'ui_open_app').execute({ args: { app: 'settings' } });
  assert.equal(r.ok, true);
  assert.deepEqual(requests.at(-1).args, ['open', 'settings']);
  assert.ok(APPS.includes('terminal'));
});

test('ui_confirm returns the tapped answer', async () => {
  const p = await plugin();
  confirmAnswer = 'yes';
  assert.equal((await tool(p, 'ui_confirm').execute({ args: { question: 'Send it?', timeout_s: 5 } })).answer, 'yes');
  confirmAnswer = 'no';
  assert.equal((await tool(p, 'ui_confirm').execute({ args: { question: 'Send it?', timeout_s: 5 } })).answer, 'no');
});

test('ui_confirm times out when nobody answers', async () => {
  const p = await plugin();
  confirmAnswer = null;
  // shortest allowed timeout is 5 s (+5 s grace); keep this test bounded by faking the clock-free path
  const t0 = Date.now();
  const r = await p.confirm('q', 5);
  assert.equal(r.answer, 'timeout');
  assert.ok(Date.now() - t0 >= 5000);
  confirmAnswer = 'yes';
});

test('untrusted session may only show cards', async () => {
  const p = await plugin();
  const ctx = { context: { sessionId: 'phone:untrusted' } };
  for (const [name, args] of [['ui_open_app', { app: 'chat' }], ['ui_confirm', { question: 'x' }], ['ui_home', {}], ['ui_screen', { action: 'off' }], ['ui_screenshot', {}], ['ui_keyboard', { visible: true }]]) {
    const r = await tool(p, name).execute({ args, ...ctx });
    assert.equal(r.error, 'untrusted_session_blocked', name);
  }
  const card = await tool(p, 'ui_show_card').execute({ args: { title: 'Hi', body: 'b' }, ...ctx });
  assert.equal(card.ok, true);
  assert.match(requests.at(-1).args[1], /^\[unknown sender\] Hi/);
  assert.equal((await tool(p, 'ui_screen').execute({ args: { action: 'status' }, ...ctx })).ok, true);
});

test('ui_state merges UI and host status', async () => {
  const p = await plugin();
  const r = await tool(p, 'ui_state').execute({});
  assert.equal(r.ok, true);
  assert.equal(r.ui.home, true);
  assert.equal(r.host.muted, true);
});

test('unreachable touchd gives a clear error', async () => {
  const p = await plugin({ socket_path: path.join(dir, 'missing.sock') });
  const r = await tool(p, 'ui_home').execute({});
  assert.equal(r.ok, false);
  assert.equal(r.error, 'touchd_unreachable');
  assert.ok(r.hint);
});
