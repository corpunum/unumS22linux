#!/usr/bin/env node
// Loopback CDP proxy; Lightpanda is started only for actual browser traffic.
import fs from 'node:fs'
import path from 'node:path'
import http from 'node:http'
import net from 'node:net'
import { spawn } from 'node:child_process'
import { fileURLToPath } from 'node:url'

export const IDLE_MS = 600000
export const DEFAULT_PORT = 9222
export const BACKEND_PORT = 19222

export function createController({ binary = process.env.LIGHTPANDA_BIN || '/opt/lightpanda/lightpanda', port = Number(process.env.LIGHTPANDA_PORT || DEFAULT_PORT), backendPort = Number(process.env.LIGHTPANDA_BACKEND_PORT || BACKEND_PORT), idleMs = Number(process.env.LIGHTPANDA_IDLE_MS || IDLE_MS), home = process.env.OPENUNUM_HOME || path.join(process.env.HOME || '/root', '.openunum'), spawnFn = spawn, now = Date.now, isLightpandaPid = null } = {}) {
  const stateDir = path.join(home, 'lightpanda')
  const pidFile = path.join(stateDir, 'lightpanda.pid')
  const lockDir = path.join(stateDir, 'lock')
  let idleTimer = null
  let lastUse = now()
  const clearTimer = () => { if (idleTimer) clearTimeout(idleTimer); idleTimer = null }
  const readPid = () => { try { const pid = Number(fs.readFileSync(pidFile, 'utf8').trim()); return Number.isSafeInteger(pid) && pid > 1 ? pid : null } catch { return null } }
  function matchesProcess(pid) {
    if (isLightpandaPid) return isLightpandaPid(pid)
    try {
      const args = fs.readFileSync(`/proc/${pid}/cmdline`, 'utf8').split(String.fromCharCode(0)).filter(Boolean)
      return args[0] === binary && args[1] === 'serve' && args.includes(String(backendPort))
    } catch { return false }
  }
  function stop() {
    clearTimer()
    const pid = readPid()
    if (!pid) return false
    if (matchesProcess(pid)) {
      try { process.kill(pid, 'SIGTERM') } catch (error) { if (error.code !== 'ESRCH') throw error }
    }
    try { fs.unlinkSync(pidFile) } catch {}
    return true
  }
  function scheduleStop() {
    clearTimer()
    idleTimer = setTimeout(() => { if (now() - lastUse >= idleMs) stop(); else scheduleStop() }, idleMs)
    idleTimer.unref?.()
  }
  function start() {
    fs.mkdirSync(stateDir, { recursive: true })
    try { fs.mkdirSync(lockDir) } catch (error) {
      if (error.code !== 'EEXIST') throw error
      const current = readPid()
      if (current && matchesProcess(current)) { scheduleStop(); return current }
      let lockOwner = null
      try { lockOwner = Number(fs.readFileSync(path.join(lockDir, 'owner'), 'utf8')) } catch {}
      if (lockOwner > 1) { try { process.kill(lockOwner, 0) } catch (e) { if (e.code === 'ESRCH') { try { fs.rmSync(lockDir, { recursive: true }) } catch {} return start() } } }
      throw new Error('Lightpanda start is already in progress; retry')
    }
    fs.writeFileSync(path.join(lockDir, 'owner'), `${process.pid}\n`, { mode: 0o600 })
    try {
      const current = readPid()
      if (current && matchesProcess(current)) { scheduleStop(); return current }
      if (current) { try { fs.unlinkSync(pidFile) } catch {} }
      const child = spawnFn(binary, ['serve', '--host', '127.0.0.1', '--port', String(backendPort)], { stdio: 'ignore', detached: true })
      fs.writeFileSync(pidFile, `${child.pid}\n`, { mode: 0o600 })
      child.unref?.()
      child.once?.('exit', () => { if (readPid() === child.pid) { try { fs.unlinkSync(pidFile) } catch {} } })
      lastUse = now(); scheduleStop()
      return child.pid
    } finally { try { fs.rmdirSync(lockDir) } catch {} }
  }
  function touch() { lastUse = now(); if (readPid()) scheduleStop() }
  return { start, stop, touch, readPid, get lastUse() { return lastUse }, port, backendPort, idleMs }
}

export function createProxy(controller) {
  const server = http.createServer((req, res) => {
    controller.touch(); controller.start()
    const upstream = http.request({ host: '127.0.0.1', port: controller.backendPort, method: req.method, path: req.url, headers: { ...req.headers, host: `127.0.0.1:${controller.backendPort}` } }, response => { res.writeHead(response.statusCode || 502, response.headers); response.pipe(res) })
    upstream.on('error', () => { if (!res.headersSent) res.writeHead(502); res.end('Lightpanda starting') })
    req.pipe(upstream)
  })
  server.on('upgrade', (req, socket, head) => {
    controller.touch(); controller.start()
    const upstream = net.connect(controller.backendPort, '127.0.0.1', () => {
      const headers = Object.entries(req.headers).map(([key, value]) => `${key}: ${value}`).join('\r\n')
      upstream.write(`${req.method} ${req.url} HTTP/${req.httpVersion}\r\n${headers}\r\n\r\n`)
      if (head.length) upstream.write(head)
      socket.pipe(upstream).pipe(socket)
    })
    upstream.on('error', () => socket.destroy()); socket.on('error', () => upstream.destroy())
  })
  return server
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const controller = createController()
  const command = process.argv[2] || 'serve'
  if (command === 'start') { console.log(controller.start()); process.exit(0) }
  if (command === 'stop') { console.log(controller.stop() ? 'stopped' : 'not running'); process.exit(0) }
  if (command === 'status') { console.log(controller.readPid() || 'stopped'); process.exit(0) }
  if (command !== 'serve') { console.error('usage: lightpanda-ondemand.mjs [serve|start|stop|status]'); process.exit(2) }
  const proxy = createProxy(controller)
  proxy.listen(controller.port, '127.0.0.1')
  const quit = () => { proxy.close(() => process.exit(0)); controller.stop() }
  process.on('SIGTERM', quit); process.on('SIGINT', quit)
}
