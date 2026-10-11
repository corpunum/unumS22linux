import test from 'node:test'
import assert from 'node:assert/strict'
import { afterEach, beforeEach } from 'node:test'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { EventEmitter } from 'node:events'
import http from 'node:http'
import net from 'node:net'
import { createController, createProxy, DEFAULT_PORT, IDLE_MS } from '../lightpanda-ondemand.mjs'

const roots = []
const timers = []
function fixture(opts = {}) {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), 'lp-ondemand-'))
  roots.push(home)
  const childPids = new Set()
  const spawnCalls = []
  const spawnFn = (bin, args, options) => {
    spawnCalls.push([bin, args, options])
    const proc = new EventEmitter()
    proc.pid = 500000 + childPids.size + 1
    proc.unref = () => {}
    childPids.add(proc.pid)
    return proc
  }
  const killCalls = []
  const ctl = createController({ home, spawnFn, idleMs: 25, isLightpandaPid: pid => childPids.has(pid), ...opts, now: () => Date.now() })
  const kill = process.kill
  process.kill = (pid, signal) => {
    killCalls.push([pid, signal])
    if (signal === 0 && !childPids.has(pid)) { const error = new Error('not found'); error.code = 'ESRCH'; throw error }
    return true
  }
  const restore = () => { process.kill = kill }
  timers.push(restore)
  return { ctl, spawnCalls, killCalls, home, restore }
}

afterEach(() => { for (const root of roots.splice(0)) fs.rmSync(root, { recursive: true, force: true }); for (const restore of timers.splice(0)) restore() })
test('uses the CDP default port and ten-minute idle interval', () => { assert.equal(DEFAULT_PORT, 9222); assert.equal(IDLE_MS, 600000) })
test('first HTTP/CDP request starts the chroot binary and waits for the backend', async () => {
  const backendProbe = net.createServer()
  await new Promise(resolve => backendProbe.listen(0, '127.0.0.1', resolve))
  const backendPort = backendProbe.address().port
  await new Promise(resolve => backendProbe.close(resolve))
  const backend = http.createServer((req, res) => { res.writeHead(200, { 'content-type': 'application/json' }); res.end('{"Browser":"Lightpanda"}') })
  const starts = []
  const { ctl } = fixture({ backendPort, idleMs: 5000, spawnFn: (bin, args, options) => {
    starts.push([bin, args, options])
    const proc = new EventEmitter()
    proc.pid = 523456
    proc.unref = () => {}
    backend.listen(backendPort, '127.0.0.1')
    return proc
  }, isLightpandaPid: pid => pid === 523456 })
  const proxy = createProxy(ctl)
  await new Promise(resolve => proxy.listen(0, '127.0.0.1', resolve))
  const proxyPort = proxy.address().port
  try {
    assert.equal(starts.length, 0, 'idle proxy must not start Lightpanda')
    const response = await new Promise((resolve, reject) => http.get({ host: '127.0.0.1', port: proxyPort, path: '/json/version' }, res => {
      let body = ''
      res.setEncoding('utf8')
      res.on('data', chunk => { body += chunk })
      res.on('end', () => resolve({ status: res.statusCode, body }))
    }).on('error', reject))
    assert.equal(response.status, 200)
    assert.equal(response.body, '{"Browser":"Lightpanda"}')
    assert.equal(starts.length, 1)
    assert.equal(starts[0][0], '/opt/lightpanda/lightpanda')
    assert.deepEqual(starts[0][1], ['serve', '--host', '127.0.0.1', '--port', String(backendPort)])
  } finally {
    ctl.stop()
    await new Promise(resolve => proxy.close(resolve))
    if (backend.listening) await new Promise(resolve => backend.close(resolve))
  }
})
test('starts once, persists PID and treats repeated start idempotently', () => {
  const { ctl, spawnCalls } = fixture()
  const pid = ctl.start()
  assert.equal(ctl.readPid(), pid)
  assert.equal(ctl.start(), pid)
  assert.equal(spawnCalls.length, 1)
  assert.deepEqual(spawnCalls[0][1], ['serve', '--host', '127.0.0.1', '--port', '19222'])
  ctl.stop()
})
test('replaces stale PID file with new server PID', () => {
  const { ctl, spawnCalls, home } = fixture()
  fs.mkdirSync(path.join(home, 'lightpanda'), { recursive: true })
  fs.writeFileSync(path.join(home, 'lightpanda', 'lightpanda.pid'), '42\\n')
  const pid = ctl.start()
  assert.notEqual(pid, 42)
  assert.equal(spawnCalls.length, 1)
  ctl.stop()
})
test('stops only the exact recorded PID and removes its PID file', () => {
  const { ctl, killCalls } = fixture()
  const pid = ctl.start()
  assert.equal(ctl.stop(), true)
  assert.ok(killCalls.some(([calledPid, signal]) => calledPid === pid && signal === 'SIGTERM'))
  assert.equal(ctl.readPid(), null)
  assert.equal(ctl.stop(), false)
})
test('activity resets idle timer before stop', async () => {
  const { ctl, killCalls } = fixture({ idleMs: 500 })
  const pid = ctl.start()
  await new Promise(resolve => setTimeout(resolve, 100))
  ctl.touch()
  await new Promise(resolve => setTimeout(resolve, 100))
  assert.equal(ctl.readPid(), pid)
  await new Promise(resolve => setTimeout(resolve, 450))
  assert.equal(ctl.readPid(), null)
  assert.ok(killCalls.some(([calledPid, signal]) => calledPid === pid && signal === 'SIGTERM'))
})
