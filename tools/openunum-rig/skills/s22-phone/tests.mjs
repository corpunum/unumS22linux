// Run: node tools/openunum-rig/skills/s22-phone/tests.mjs  (fake phone on 127.0.0.1; no real phone or token)
import assert from 'node:assert/strict'
import http from 'node:http'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import crypto from 'node:crypto'
import { execute } from './execute.mjs'

const TOKEN = 'tok-' + crypto.randomBytes(12).toString('hex')
const OWNER = { turn: { ownerProven: true, source: 'webui', sessionKind: 'chat' } }
const work = fs.mkdtempSync(path.join(os.tmpdir(), 's22-test-'))
const tokenFile = path.join(work, 'token')
const privDir = path.join(work, 'private')
fs.writeFileSync(tokenFile, TOKEN + '\n', { mode: 0o600 })
fs.chmodSync(tokenFile, 0o600)
fs.mkdirSync(privDir, { mode: 0o700 })
fs.chmodSync(privDir, 0o700)
Object.assign(process.env, { S22_PHONE_TEST: '1', S22_DEVICE_TOKEN_FILE: tokenFile, S22_PHONE_PRIVATE_DIR: privDir })

let reqs = [], handler = null, taskPolls = 0, passed = 0
const outputs = []
const JPEG = Buffer.from('fake-jpeg-bytes-0123456789')
const server = http.createServer((req, res) => {
  let body = ''
  req.on('data', (c) => (body += c))
  req.on('end', () => {
    const r = { method: req.method, url: req.url, auth: req.headers.authorization, ct: req.headers['content-type'], body: body ? JSON.parse(body) : null }
    reqs.push(r)
    const send = (code, o) => { res.writeHead(code, { 'Content-Type': 'application/json' }); res.end(JSON.stringify(o)) }
    if (handler) return handler(r, send, res)
    const cap = r.url.replace('/v1/device/', '')
    if (r.method === 'GET') return send(200, { capabilities: [{ id: 'speak', available: true, switch: 'on' }, { id: 'audio.listen', available: false }] })
    switch (cap) {
      case 'device.status': return send(200, { ok: true, cap, state: { battery: 81, charging: true, screen: 'off', wifi: 'up' } })
      case 'camera.capture': return send(200, { ok: true, cap, artifact: { mime: 'image/jpeg', base64: JPEG.toString('base64'), bytes: JPEG.length } })
      case 'audio.listen': return send(200, { ok: true, cap, state: { transcript: 'hello room' } })
      case 'speak': return send(200, { ok: true, cap, state: {} })
      case 'task.submit': return send(200, { ok: true, cap, state: { id: 't1' } })
      case 'task.get': taskPolls++; return send(200, { ok: true, cap, state: taskPolls >= 3 ? { reply: 'done it' } : {} })
      case 'uplink.status': return send(200, { ok: true, cap, state: { up: 'wifi' } })
      case 'haptic.play': return send(200, { ok: true, cap, state: {} })
    }
    send(404, { ok: false, code: 'bad_request', error: 'no cap' })
  })
})
server.unref()
await new Promise((r) => server.listen(0, '127.0.0.1', r))
const base = `http://127.0.0.1:${server.address().port}`
process.env.S22_DEVICE_URL = base

const ok = (name) => { passed++; console.log('ok', name) }
const run = async (args, ...c) => { reqs = []; const r = await execute(args, c.length ? c[0] : OWNER); outputs.push(r); return r }
const uid = process.getuid()

try {
  // per-action request shape
  let r = await run({ action: 'status' }, undefined)
  assert.equal(r.ok, true); assert.equal(r.battery, 81); assert.equal(r.charging, true); assert.equal(r.network.wifi, 'up')
  assert.equal(reqs[0].method, 'POST'); assert.equal(reqs[0].url, '/v1/device/device.status')
  assert.equal(reqs[0].auth, `Bearer ${TOKEN}`); assert.match(reqs[0].ct, /application\/json/); ok('status (no owner needed)')

  r = await run({ action: 'catalog' })
  assert.equal(reqs[0].method, 'GET'); assert.equal(reqs[0].url, '/v1/device'); assert.equal(reqs[0].auth, `Bearer ${TOKEN}`)
  assert.deepEqual(r.capabilities.map((c) => [c.id, c.available]), [['speak', true], ['audio.listen', false]]); ok('catalog')

  r = await run({ action: 'photo', sensor: 'front' })
  assert.equal(reqs[0].url, '/v1/device/camera.capture'); assert.deepEqual(reqs[0].body, { sensor: 'front' })
  assert.equal(r.ok, true); assert.equal(r.mime, 'image/jpeg'); assert.equal(r.bytes, JPEG.length)
  assert.equal(r.sha256, crypto.createHash('sha256').update(JPEG).digest('hex'))
  assert.equal(path.dirname(r.path), privDir); assert.match(r.path, /\.jpg$/)
  assert.equal(fs.statSync(r.path).mode & 0o777, 0o600); assert.equal(fs.statSync(privDir).mode & 0o777, 0o700)
  assert.equal(JSON.stringify(r).includes(JPEG.toString('base64')), false); assert.equal('base64' in r, false)
  assert.deepEqual(fs.readFileSync(r.path), JPEG)
  const photo = r.path
  r = await run({ action: 'photo' }); assert.deepEqual(reqs[0].body, { sensor: 'rear' })
  assert.notEqual(r.path, photo); fs.unlinkSync(r.path); ok('photo')

  // forget
  const outside = path.join(work, 'outside.txt'); fs.writeFileSync(outside, 'x')
  r = await run({ action: 'forget', path: outside }, undefined); assert.equal(r.ok, false); assert.ok(fs.existsSync(outside))
  const link = path.join(privDir, 'link.jpg'); fs.symlinkSync(outside, link)
  r = await run({ action: 'forget', path: link }); assert.equal(r.ok, false); assert.ok(fs.existsSync(outside)); assert.ok(fs.lstatSync(link).isSymbolicLink())
  fs.unlinkSync(link)
  fs.mkdirSync(path.join(privDir, 'sub')); fs.writeFileSync(path.join(privDir, 'sub', 'f'), 'x')
  r = await run({ action: 'forget', path: path.join(privDir, 'sub', 'f') }); assert.equal(r.ok, false)
  r = await run({ action: 'forget', path: path.join(privDir, 'sub') }); assert.equal(r.ok, false)
  fs.rmSync(path.join(privDir, 'sub'), { recursive: true })
  r = await run({ action: 'forget', path: path.join(privDir, '..', 'private', 'sub', '..', '..', 'outside.txt') }); assert.equal(r.ok, false)
  r = await run({ action: 'forget', path: photo }, { turn: { ownerProven: false } })
  assert.equal(r.ok, true); assert.equal(fs.existsSync(photo), false); assert.equal(reqs.length, 0); ok('forget (inside only, no symlink/subdir, no owner needed)')

  // private dir checks
  const linkDir = path.join(work, 'linkdir'); fs.symlinkSync(privDir, linkDir)
  process.env.S22_PHONE_PRIVATE_DIR = linkDir
  r = await run({ action: 'photo' }); assert.equal(r.code, 'insecure_dir'); assert.equal(reqs.length, 0)
  const open = path.join(work, 'open'); fs.mkdirSync(open); fs.chmodSync(open, 0o755)
  process.env.S22_PHONE_PRIVATE_DIR = open
  r = await run({ action: 'photo' }); assert.equal(r.code, 'insecure_dir'); assert.equal(reqs.length, 0)
  r = await run({ action: 'forget', path: path.join(open, 'x') }); assert.equal(r.code, 'insecure_dir')
  const fresh = path.join(work, 'a', 'fresh')
  process.env.S22_PHONE_PRIVATE_DIR = fresh
  r = await run({ action: 'photo' }); assert.equal(r.ok, true); assert.equal(fs.statSync(fresh).mode & 0o777, 0o700); assert.equal(fs.statSync(fresh).uid, uid)
  process.env.S22_PHONE_PRIVATE_DIR = privDir; ok('private dir: symlink refused, 0755 refused, created 0700')

  // listen
  r = await run({ action: 'listen', seconds: 7 })
  assert.deepEqual(reqs[0].body, { seconds: 7 }); assert.equal(reqs[0].url, '/v1/device/audio.listen'); assert.equal(r.transcript, 'hello room'); assert.equal(r.seconds, 7)
  await run({ action: 'listen' }); assert.deepEqual(reqs[0].body, { seconds: 15 })
  handler = (q, send) => send(403, { ok: false, code: 'switch_off', error: 'listening is off' })
  r = await run({ action: 'listen' }); handler = null
  assert.equal(r.ok, false); assert.equal(r.code, 'switch_off'); assert.match(r.hint, /Settings → Rig access/); ok('listen + switch-off hint')

  r = await run({ action: 'say', text: 'hi there', level: 3 })
  assert.equal(reqs[0].url, '/v1/device/speak'); assert.deepEqual(reqs[0].body, { text: 'hi there', level: 3 }); assert.equal(r.ok, true)
  await run({ action: 'say', text: 'hi' }); assert.deepEqual(reqs[0].body, { text: 'hi' }); ok('say')

  r = await run({ action: 'haptic', pattern: 'buzz' }); assert.equal(reqs[0].url, '/v1/device/haptic.play'); assert.deepEqual(reqs[0].body, { pattern: 'buzz' }); ok('haptic')
  r = await run({ action: 'uplink' }, undefined); assert.equal(reqs[0].url, '/v1/device/uplink.status'); assert.equal(r.ok, true); ok('uplink (no owner needed)')

  // task
  taskPolls = 0
  r = await run({ action: 'task', text: 'what time', wait_s: 5, _poll_ms: 10 })
  assert.deepEqual(r, { ok: true, id: 't1', reply: 'done it', done: true })
  assert.equal(reqs[0].url, '/v1/device/task.submit'); assert.deepEqual(reqs[0].body, { text: 'what time' })
  assert.deepEqual(reqs.slice(1).map((q) => [q.url, q.body.id]), [['/v1/device/task.get', 't1'], ['/v1/device/task.get', 't1'], ['/v1/device/task.get', 't1']])
  r = await run({ action: 'task', text: 'x', wait_s: 0 }); assert.deepEqual(r, { ok: true, id: 't1', reply: null, done: false }); assert.equal(reqs.length, 1)
  taskPolls = 10
  r = await run({ action: 'task_get', id: 't1' }, undefined); assert.equal(r.reply, 'done it'); assert.deepEqual(reqs[0].body, { id: 't1' }); ok('task polling, task_get')

  // validation: no request on bad args
  const bad = [
    { action: 'photo', sensor: 'side' }, { action: 'listen', seconds: 2 }, { action: 'listen', seconds: 121 }, { action: 'listen', seconds: '9' },
    { action: 'say' }, { action: 'say', text: '' }, { action: 'say', text: 'x'.repeat(1001) }, { action: 'say', text: 'x', level: 9 }, { action: 'say', text: 'x', level: 0 },
    { action: 'task', text: '' }, { action: 'task', text: 'x'.repeat(4001) }, { action: 'task', text: 'x', wait_s: 301 },
    { action: 'task_get' }, { action: 'task_get', id: {} }, { action: 'haptic', pattern: 'zap' }, { action: 'haptic' }, { action: 'forget' }, { action: 'nope' }, {}, null,
  ]
  for (const b of bad) { r = await run(b); assert.equal(r.ok, false); assert.equal(r.code, 'bad_request'); assert.equal(reqs.length, 0, JSON.stringify(b)) }
  assert.match((await run({ action: 'nope' })).error, /status, catalog, photo/); ok('argument validation')

  // owner gating
  const gated = [{ action: 'photo' }, { action: 'listen' }, { action: 'say', text: 'x' }, { action: 'task', text: 'x' }, { action: 'catalog' }, { action: 'haptic', pattern: 'tick' }]
  const nonOwner = [undefined, {}, { turn: {} }, { turn: { ownerProven: false } }, { turn: { ownerProven: 'true' } }, { turn: { ownerProven: 1 } }, { owner: true }]
  for (const g of gated) {
    for (const c of nonOwner) { r = await run(g, c); assert.equal(r.code, 'owner_only', JSON.stringify([g, c])); assert.equal(reqs.length, 0) }
    r = await run({ ...g, owner: true, ownerProven: true }, undefined); assert.equal(r.code, 'owner_only'); assert.equal(reqs.length, 0)
    r = await run({ ...g, _poll_ms: 5, wait_s: 0 }, OWNER); assert.equal(r.ok, true, JSON.stringify([g, r])); assert.ok(reqs.length >= 1)
    if (g.action === 'photo') fs.unlinkSync(r.path)
  }
  for (const g of [{ action: 'status' }, { action: 'uplink' }, { action: 'task_get', id: 'a' }]) { r = await run(g, undefined); assert.equal(r.ok, true); assert.equal(reqs.length, 1) }
  ok('owner gating')

  // paused / forbidden never reach the server
  for (const a of ['sms', 'call', 'sms.send', 'call.dial', 'call.hangup', 'sms_send']) {
    for (const c of [OWNER, undefined]) { r = await run({ action: a, text: 'x', number: '1' }, c); assert.equal(r.code, 'paused'); assert.match(r.error, /paused on this phone/); assert.equal(reqs.length, 0) }
  }
  for (const a of ['uplink.share', 'uplink_share']) { r = await run({ action: a }); assert.equal(r.ok, false); assert.equal(reqs.length, 0) }
  ok('sms/call/uplink.share never reach the phone')

  // needs_confirmation hint; error echoing the token is scrubbed
  handler = (q, send) => send(409, { ok: false, code: 'needs_confirmation', error: 'owner must confirm' })
  r = await run({ action: 'say', text: 'x' }); assert.equal(r.code, 'needs_confirmation'); assert.match(r.hint, /do not retry/)
  handler = (q, send) => send(500, { ok: false, code: 'unavailable', error: `leak ${TOKEN}` })
  r = await run({ action: 'uplink' }); assert.equal(r.ok, false); assert.equal(JSON.stringify(r).includes(TOKEN), false)
  handler = (q, send) => send(200, { ok: true, state: { echo: TOKEN } })
  r = await run({ action: 'status' }); assert.equal(JSON.stringify(r).includes(TOKEN), false); handler = null; ok('needs_confirmation hint, token scrubbed')

  // token file mode and config
  fs.chmodSync(tokenFile, 0o644)
  r = await run({ action: 'status' }); assert.equal(r.code, 'insecure_token'); assert.equal(reqs.length, 0)
  fs.chmodSync(tokenFile, 0o600); ok('0644 token refused')

  // timeout and unreachable
  handler = () => {}
  const t0 = Date.now(); r = await run({ action: 'status', _timeout_ms: 150 }); handler = null
  assert.equal(r.code, 'timeout'); assert.ok(Date.now() - t0 < 3000)
  process.env.S22_PHONE_TEST = '0'; handler = (q, send) => send(200, { ok: true, state: {} })
  r = await run({ action: 'status', _timeout_ms: 1 }); assert.equal(r.ok, true); process.env.S22_PHONE_TEST = '1'; handler = null
  ok('timeout (override only under S22_PHONE_TEST=1)')
  const tmp = http.createServer(); await new Promise((x) => tmp.listen(0, '127.0.0.1', x)); const dead = tmp.address().port; await new Promise((x) => tmp.close(x))
  process.env.S22_DEVICE_URL = `http://127.0.0.1:${dead}`
  r = await run({ action: 'status' }); assert.equal(r.code, 'unreachable'); assert.equal(JSON.stringify(r).includes(TOKEN), false)
  process.env.S22_DEVICE_URL = base; ok('unreachable')

  assert.equal(JSON.stringify(outputs).includes(TOKEN), false); ok('token never in any returned object')
  console.log(`\n${passed} test groups passed`)
} finally {
  server.closeAllConnections?.()
  server.close()
  fs.rmSync(work, { recursive: true, force: true })
}
