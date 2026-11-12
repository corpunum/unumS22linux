// OpenUnum skill s22-phone: lets the rig agent use the Samsung S22 through its device API.
// Config (nothing is hardcoded): env S22_DEVICE_URL or ~/.config/unum/s22-device.url,
// bearer token in ~/.config/unum/s22-device.token (mode 0600, otherwise refused).
// Optional env S22_DEVICE_TOKEN_FILE overrides the token path (used by tests).
// The token is only ever sent as the Authorization header: no child processes, no argv, no logs.
// Captures are stored in ~/.openunum/private/s22/ (env S22_PHONE_PRIVATE_DIR overrides, for tests).
import fs from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import crypto from 'node:crypto'

const ACTIONS = ['status', 'catalog', 'photo', 'forget', 'listen', 'say', 'task', 'task_get', 'uplink', 'haptic', 'sms', 'call']
// Owner gate: these act on the owner's surroundings or the phone's agent and need a proven owner turn.
const OWNER_ONLY = ['photo', 'listen', 'say', 'task', 'catalog', 'haptic']
const OWNER_MSG = "camera, listening and speaking need an owner turn (paired chat) or the owner's approval of this one action"
const isOwner = (context) => context?.turn?.ownerProven === true
const PAUSED = 'calls and SMS are paused on this phone (no bundle; owner 2026-11-12)'
const MIME_EXT = { 'image/jpeg': 'jpg', 'image/jpg': 'jpg', 'image/png': 'png', 'image/webp': 'webp', 'image/heic': 'heic' }

const fail = (code, error, extra = {}) => ({ ok: false, code, error, ...extra })
const isInt = (v, lo, hi) => Number.isInteger(v) && v >= lo && v <= hi
const isObj = (v) => v !== null && typeof v === 'object' && !Array.isArray(v)

class Refuse extends Error {
  constructor(code, message) { super(message); this.code = code }
}

async function loadConfig() {
  const cfgDir = path.join(os.homedir(), '.config', 'unum')
  let base = (process.env.S22_DEVICE_URL || '').trim()
  if (!base) {
    try { base = (await fs.readFile(path.join(cfgDir, 's22-device.url'), 'utf8')).split('\n')[0].trim() } catch { base = '' }
  }
  if (!/^https?:\/\/[^\s/]+/.test(base)) throw new Refuse('not_configured', 'phone URL not configured (env S22_DEVICE_URL or ~/.config/unum/s22-device.url)')
  base = base.replace(/\/+$/, '')
  const tokenFile = process.env.S22_DEVICE_TOKEN_FILE || path.join(cfgDir, 's22-device.token')
  let st
  try { st = await fs.lstat(tokenFile) } catch { throw new Refuse('not_configured', 'phone token file not found (~/.config/unum/s22-device.token)') }
  if (!st.isFile()) throw new Refuse('insecure_token', 'token file must be a regular file')
  if ((st.mode & 0o077) !== 0) throw new Refuse('insecure_token', 'token file must be mode 0600 (chmod 600); refusing to use it')
  const token = (await fs.readFile(tokenFile, 'utf8')).trim()
  if (!token) throw new Refuse('not_configured', 'token file is empty')
  return { base, token }
}

function testing(args) {
  return process.env.S22_PHONE_TEST === '1' && isObj(args)
}

// One HTTP call; returns {status, body} or throws Refuse for transport problems.
async function http(cfg, method, urlPath, body, timeoutMs) {
  const headers = { Authorization: `Bearer ${cfg.token}` }
  const init = { method, headers, signal: AbortSignal.timeout(timeoutMs) }
  if (method === 'POST') { headers['Content-Type'] = 'application/json'; init.body = JSON.stringify(body || {}) }
  let res, text
  try {
    res = await fetch(cfg.base + urlPath, init)
    text = await res.text()
  } catch (e) {
    if (e && (e.name === 'TimeoutError' || e.name === 'AbortError')) throw new Refuse('timeout', `phone did not answer within ${Math.round(timeoutMs / 1000)} s`)
    throw new Refuse('unreachable', 'phone API unreachable')
  }
  let parsed
  try { parsed = JSON.parse(text) } catch { parsed = null }
  if (!isObj(parsed)) return { status: res.status, body: { ok: false, code: res.ok ? 'bad_response' : `http_${res.status}`, error: 'phone answered with a non-JSON body' } }
  return { status: res.status, body: parsed }
}

// Calls a capability; returns the success body or an error object {ok:false,...}.
async function cap(cfg, id, body, timeoutMs) {
  const { status, body: b } = await http(cfg, 'POST', `/v1/device/${id}`, body, timeoutMs)
  if (status >= 200 && status < 300 && b.ok === true) return b
  const code = typeof b.code === 'string' ? b.code : `http_${status}`
  const out = fail(code, typeof b.error === 'string' ? b.error : `phone answered HTTP ${status}`)
  if (code === 'needs_confirmation' || code === 'owner_denied') out.hint = 'do not retry; the owner decides on the phone'
  return out
}

const st = (b) => (isObj(b.state) ? b.state : {})
const res = (b) => (isObj(b.result) ? b.result : {})
const pick = (...vals) => vals.find((v) => v !== undefined && v !== null)

function validate(action, a) {
  switch (action) {
    case 'photo': {
      const sensor = a.sensor ?? 'rear'
      if (sensor !== 'rear' && sensor !== 'front') throw new Refuse('bad_request', "sensor must be 'rear' or 'front'")
      return { sensor }
    }
    case 'forget':
      if (typeof a.path !== 'string' || !a.path) throw new Refuse('bad_request', 'path (string) is required')
      return { path: a.path }
    case 'listen': {
      const seconds = a.seconds ?? 15
      if (!isInt(seconds, 3, 120)) throw new Refuse('bad_request', 'seconds must be an integer 3..120')
      return { seconds }
    }
    case 'say': {
      if (typeof a.text !== 'string' || a.text.length < 1 || a.text.length > 1000) throw new Refuse('bad_request', 'text must be a string of 1..1000 characters')
      if (a.level !== undefined && !isInt(a.level, 1, 8)) throw new Refuse('bad_request', 'level must be an integer 1..8')
      return a.level === undefined ? { text: a.text } : { text: a.text, level: a.level }
    }
    case 'task': {
      if (typeof a.text !== 'string' || a.text.length < 1 || a.text.length > 4000) throw new Refuse('bad_request', 'text must be a string of 1..4000 characters')
      const wait_s = a.wait_s ?? 60
      if (!isInt(wait_s, 0, 300)) throw new Refuse('bad_request', 'wait_s must be an integer 0..300')
      return { text: a.text, wait_s }
    }
    case 'task_get':
      if (!(typeof a.id === 'string' && a.id.length >= 1 && a.id.length <= 200) && !isInt(a.id, 0, Number.MAX_SAFE_INTEGER)) throw new Refuse('bad_request', 'id must be a task id (string or integer)')
      return { id: a.id }
    case 'haptic':
      if (!['tick', 'buzz', 'stop'].includes(a.pattern)) throw new Refuse('bad_request', "pattern must be 'tick', 'buzz' or 'stop'")
      return { pattern: a.pattern }
    default:
      return {}
  }
}

async function doStatus(cfg, a, t) {
  const b = await cap(cfg, 'device.status', {}, t(20000))
  if (!b.ok) return b
  const s = st(b)
  const network = {}
  for (const [k, v] of Object.entries(s)) if (/net|wifi|cell|signal|ssid|uplink/i.test(k)) network[k] = v
  return { ok: true, battery: s.battery, charging: s.charging, screen: s.screen, network, state: s }
}

async function doCatalog(cfg, a, t) {
  const { status, body } = await http(cfg, 'GET', '/v1/device', null, t(20000))
  if (status < 200 || status >= 300) return fail(typeof body.code === 'string' ? body.code : `http_${status}`, typeof body.error === 'string' ? body.error : `phone answered HTTP ${status}`)
  const raw = pick(body.capabilities, body.caps, body.catalog, [])
  const list = Array.isArray(raw) ? raw : Object.entries(raw).map(([id, v]) => (isObj(v) ? { id, ...v } : { id, available: v }))
  const capabilities = list.filter(isObj).map((c) => {
    const o = { id: c.id ?? c.cap ?? c.name, available: c.available }
    if (c.switch !== undefined) o.switch = c.switch
    if (c.switches !== undefined) o.switches = c.switches
    return o
  })
  const out = { ok: true, capabilities }
  if (body.switches !== undefined) out.switches = body.switches
  return out
}

async function privateDir(create) {
  const dir = path.resolve(process.env.S22_PHONE_PRIVATE_DIR || path.join(os.homedir(), '.openunum', 'private', 's22'))
  if (create) await fs.mkdir(dir, { recursive: true, mode: 0o700 })
  let s
  try { s = await fs.lstat(dir) } catch { throw new Refuse('not_found', 'private capture dir does not exist') }
  if (s.isSymbolicLink() || !s.isDirectory()) throw new Refuse('insecure_dir', 'private capture dir must be a real directory, not a symlink')
  if (s.uid !== process.getuid()) throw new Refuse('insecure_dir', 'private capture dir is not owned by the current user')
  if ((s.mode & 0o077) !== 0) throw new Refuse('insecure_dir', 'private capture dir must be mode 0700 (no group/other access)')
  return dir
}

async function doPhoto(cfg, a, t, v) {
  const dir = await privateDir(true)
  const b = await cap(cfg, 'camera.capture', { sensor: v.sensor }, t(330000))
  if (!b.ok) return b
  const art = b.artifact
  if (!isObj(art) || typeof art.base64 !== 'string' || typeof art.mime !== 'string') return fail('bad_response', 'phone answered without a photo artifact')
  const buf = Buffer.from(art.base64, 'base64')
  if (!buf.length) return fail('bad_response', 'photo artifact is empty')
  const file = path.join(dir, `photo-${Date.now()}-${crypto.randomBytes(6).toString('hex')}.${MIME_EXT[art.mime.toLowerCase()] || 'bin'}`)
  await fs.writeFile(file, buf, { mode: 0o600, flag: 'wx' })
  return { ok: true, path: file, mime: art.mime, bytes: buf.length, sha256: crypto.createHash('sha256').update(buf).digest('hex'), sensor: v.sensor }
}

async function doForget(cfg, a, t, v) {
  const dir = await privateDir(false)
  const file = path.resolve(v.path)
  if (path.dirname(file) !== dir) return fail('refused', 'only files directly inside the private capture dir can be forgotten')
  let fst
  try { fst = await fs.lstat(file) } catch { return fail('not_found', 'no such file') }
  if (!fst.isFile() || fst.isSymbolicLink()) return fail('refused', 'not a plain file')
  await fs.unlink(file)
  return { ok: true, forgotten: true }
}

async function doListen(cfg, a, t, v) {
  const b = await cap(cfg, 'audio.listen', { seconds: v.seconds }, t(260000))
  if (!b.ok) {
    if (/switch|forbidden|disabled|listen_off/i.test(b.code)) b.hint = 'the owner turns listening on in the phone Settings → Rig access'
    return b
  }
  const r = res(b), s = st(b)
  const transcript = pick(s.transcript, r.transcript, s.text, r.text, typeof b.result === 'string' ? b.result : undefined, '')
  return { ok: true, transcript, seconds: v.seconds }
}

async function doSay(cfg, a, t, v) {
  const b = await cap(cfg, 'speak', v, t(200000))
  return b.ok ? { ok: true, spoken: true, state: st(b) } : b
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

async function doTask(cfg, a, t, v) {
  const b = await cap(cfg, 'task.submit', { text: v.text }, t(40000))
  if (!b.ok) return b
  const get = (x) => pick(st(x).id, res(x).id, st(x).task_id, res(x).task_id)
  const reply = (x) => pick(st(x).reply, res(x).reply)
  const id = get(b)
  let rep = reply(b)
  if (rep === undefined && id === undefined) return fail('bad_response', 'phone returned neither a task id nor a reply')
  const pollMs = testing(a) && isInt(a._poll_ms, 1, 5000) ? a._poll_ms : 5000
  const deadline = Date.now() + v.wait_s * 1000
  while (rep === undefined && Date.now() + pollMs <= deadline) {
    await sleep(pollMs)
    const g = await cap(cfg, 'task.get', { id }, t(40000))
    if (!g.ok) return g
    rep = reply(g)
  }
  return { ok: true, id: id ?? null, reply: rep ?? null, done: rep != null }
}

async function doTaskGet(cfg, a, t, v) {
  const g = await cap(cfg, 'task.get', { id: v.id }, t(40000))
  if (!g.ok) return g
  const rep = pick(st(g).reply, res(g).reply)
  return { ok: true, id: v.id, reply: rep ?? null, done: rep != null }
}

async function doUplink(cfg, a, t) {
  const b = await cap(cfg, 'uplink.status', {}, t(30000))
  return b.ok ? { ok: true, state: st(b), result: b.result } : b
}

async function doHaptic(cfg, a, t, v) {
  const b = await cap(cfg, 'haptic.play', { pattern: v.pattern }, t(30000))
  return b.ok ? { ok: true, played: v.pattern } : b
}

const HANDLERS = { status: doStatus, catalog: doCatalog, photo: doPhoto, forget: doForget, listen: doListen, say: doSay, task: doTask, task_get: doTaskGet, uplink: doUplink, haptic: doHaptic }

function scrub(value, token) {
  const s = JSON.stringify(value)
  return token && s.includes(token) ? fail('internal', 'response withheld (it contained a secret)') : value
}

export async function execute(args, context) {
  let token = null
  try {
    const a = isObj(args) ? args : {}
    const action = typeof a.action === 'string' ? a.action : ''
    if (/^(sms|call)([._]|$)/.test(action)) return fail('paused', PAUSED)
    if (/^uplink[._]share$/.test(action)) return fail('forbidden', 'uplink.share belongs to the failover daemon; this skill never calls it')
    if (!ACTIONS.includes(action)) return fail('bad_request', `unknown action; use one of: ${ACTIONS.filter((x) => x !== 'sms' && x !== 'call').join(', ')}`)
    if (OWNER_ONLY.includes(action) && !isOwner(context)) return fail('owner_only', OWNER_MSG)
    const v = validate(action, a)
    if (action === 'forget') return await doForget(null, a, null, v)
    const cfg = await loadConfig()
    token = cfg.token
    const override = testing(a) && isInt(a._timeout_ms, 1, 60000) ? a._timeout_ms : null
    const t = (ms) => override ?? ms
    return scrub(await HANDLERS[action](cfg, a, t, v), token)
  } catch (e) {
    if (e instanceof Refuse) return scrub(fail(e.code, e.message), token)
    return fail('internal', 'unexpected error in s22-phone')
  }
}
