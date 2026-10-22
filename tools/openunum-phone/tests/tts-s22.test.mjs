// node --test tools/openunum-phone/tests/tts-s22.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, writeFileSync, chmodSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
process.env.S22_PHONE_PLUGIN_BASE = 'builtin';
const { default: TtsS22Plugin } = await import('../plugins/tts-s22/index.mjs');
const manifest = JSON.parse(readFileSync(new URL('../plugins/tts-s22/plugin.json', import.meta.url), 'utf8'));

function fakeCli(reply) {
  const d = mkdtempSync(path.join(tmpdir(), 'tts-s22-'));
  const argsFile = path.join(d, 'args.json');
  const cli = path.join(d, 's22-ui');
  writeFileSync(cli, `#!/usr/bin/env node\nrequire('fs').writeFileSync(${JSON.stringify(argsFile)}, JSON.stringify(process.argv.slice(2)));\nprocess.stdout.write(${JSON.stringify(JSON.stringify(reply))});\n`);
  chmodSync(cli, 0o755);
  return { cli, args: () => JSON.parse(readFileSync(argsFile, 'utf8')) };
}

async function tool(cfg) {
  const p = new TtsS22Plugin(manifest);
  await p.onInit(cfg);
  return p.getTools()[0];
}

test('plays by default through s22-ui tts --play', async () => {
  const f = fakeCli({ ok: true, path: '/run/s22-touch/tts/a.wav', engine: 'supertonic', ms: 900, played: true, out: 'speaker', play_ms: 2100 });
  const t = await tool({ ui_cli: f.cli, play: true });
  const r = await t.execute({ args: { text: 'Hello there' } });
  assert.deepEqual(f.args(), ['tts', '--engine', 'supertonic', '--play', '--out', 'speaker', 'Hello there']);
  assert.equal(r.played, true);
  assert.equal(r.playMs, 2100);
});

test('Greek engine and play=false render only', async () => {
  const f = fakeCli({ ok: true, path: '/run/s22-touch/tts/b.wav', engine: 'el-antonis', ms: 700, played: false });
  const t = await tool({ ui_cli: f.cli });
  const r = await t.execute({ args: { text: 'Καλημέρα', engine: 'el-antonis', play: false } });
  assert.deepEqual(f.args(), ['tts', '--engine', 'el-antonis', 'Καλημέρα']);
  assert.equal(r.played, false);
});

test('reports a playback failure', async () => {
  const f = fakeCli({ ok: true, path: '/x.wav', engine: 'supertonic', played: false, play_rc: 1, play_tail: 'aplay EIO' });
  const t = await tool({ ui_cli: f.cli });
  const r = await t.execute({ args: { text: 'x' } });
  assert.equal(r.played, false);
  assert.match(r.playError, /EIO/);
});

test('filler option adds --filler (and --for) to s22-ui tts only when playing', async () => {
  const f = fakeCli({ ok: true, path: '/x.wav', engine: 'supertonic', played: true, out: 'speaker', filler: { ok: true, category: 'general' } });
  const t = await tool({ ui_cli: f.cli });
  const r = await t.execute({ args: { text: 'It is noon.', filler: true, thinking_for: 'what time is it' } });
  assert.deepEqual(f.args(), ['tts', '--engine', 'supertonic', '--play', '--out', 'speaker', '--filler', '--for', 'what time is it', 'It is noon.']);
  assert.equal(r.filler.ok, true);
  await t.execute({ args: { text: 'x', filler: true, play: false } });
  assert.deepEqual(f.args(), ['tts', '--engine', 'supertonic', 'x']);
});
