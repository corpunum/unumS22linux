// Incoming-event bridge: long-polls s22-phoned /events and starts OpenUnum
// agent turns (POST /api/chat) for incoming SMS and incoming calls.
//
// Guarantees:
//  - never throws out of the loop (OpenUnum must not crash because of the phone);
//  - the last processed event seq is persisted after every event, so restarts
//    do not replay; a turn that could not be delivered is retried (seq is not
//    advanced) with exponential backoff, up to maxDispatchAttempts;
//  - exponential backoff (with jitter) while the daemon is down;
//  - daemon seq going backwards (daemon restarted) resets the cursor to 0.
import fs from 'node:fs';
import fsp from 'node:fs/promises';
import path from 'node:path';
import crypto from 'node:crypto';
import { anySignal } from './daemon-client.mjs';
import {
  isOwner, ownerSmsPrompt, untrustedSmsPrompt, ownerCallPrompt, untrustedCallPrompt
} from './policy.mjs';

export function backoffDelay(attempt, initialMs, maxMs, rand = Math.random) {
  const base = Math.min(maxMs, initialMs * 2 ** Math.max(0, attempt - 1));
  const jitter = base * 0.2 * (rand() * 2 - 1);
  return Math.max(0, Math.min(maxMs, Math.round(base + jitter)));
}

function pick(ev, ...keys) {
  for (const key of keys) {
    for (const src of [ev, ev?.sms, ev?.message, ev?.call, ev?.data]) {
      if (src && typeof src === 'object' && src[key] !== undefined && src[key] !== null) return src[key];
    }
  }
  return undefined;
}

export class ChatPoster {
  constructor({ baseUrl, userId = '', timeoutMs = 60000, fetchImpl = globalThis.fetch } = {}) {
    this.baseUrl = String(baseUrl || '').replace(/\/+$/, '');
    this.userId = String(userId || '');
    this.timeoutMs = Number(timeoutMs) > 0 ? Number(timeoutMs) : 60000;
    this.fetch = fetchImpl;
  }

  // -> { outcome: 'delivered'|'delivered_unknown'|'rejected'|'retry', status?, error? }
  async post(sessionId, message, signal) {
    const headers = { 'content-type': 'application/json' };
    if (this.userId) headers['x-openunum-user-id'] = this.userId;
    const timeout = AbortSignal.timeout(this.timeoutMs);
    let res;
    try {
      res = await this.fetch(`${this.baseUrl}/api/chat`, {
        method: 'POST',
        headers,
        body: JSON.stringify({ sessionId, message }),
        signal: signal ? anySignal([signal, timeout]) : timeout
      });
    } catch (err) {
      if (signal?.aborted) return { outcome: 'retry', error: 'stopped' };
      if (err?.name === 'TimeoutError' || err?.name === 'AbortError') {
        // The request was sent; OpenUnum answers 202 after ~20s for long turns,
        // so a timeout here most likely means the turn is running. Do not
        // re-post (a duplicate turn could send a duplicate SMS).
        return { outcome: 'delivered_unknown', error: 'chat_timeout' };
      }
      return { outcome: 'retry', error: String(err?.cause?.code || err?.message || err) };
    }
    let body = '';
    try { body = await res.text(); } catch { /* ignore */ }
    if (res.status >= 200 && res.status < 300) return { outcome: 'delivered', status: res.status };
    if (res.status === 429 || res.status >= 500) return { outcome: 'retry', status: res.status, error: body.slice(0, 300) };
    return { outcome: 'rejected', status: res.status, error: body.slice(0, 300) };
  }
}

export class EventBridge {
  constructor({ client, poster, statePath, config = {}, log = {}, rand = Math.random } = {}) {
    this.client = client;
    this.poster = poster;
    this.statePath = statePath;
    this.cfg = {
      ownerNumbers: config.ownerNumbers || [],
      sessionId: config.sessionId || 'phone:inbox',
      untrustedSessionId: config.untrustedSessionId || 'phone:untrusted',
      pollTimeoutS: Number(config.pollTimeoutS ?? 25),
      backoffInitialMs: Number(config.backoffInitialMs ?? 1000),
      backoffMaxMs: Number(config.backoffMaxMs ?? 60000),
      startDelayMs: Number(config.startDelayMs ?? 0),
      minPollIntervalMs: Number(config.minPollIntervalMs ?? 500),
      replayBacklogOnFirstRun: Boolean(config.replayBacklogOnFirstRun),
      answerUnknownCalls: Boolean(config.answerUnknownCalls),
      maxDispatchAttempts: Number(config.maxDispatchAttempts ?? 10)
    };
    this.log = { info: log.info || (() => {}), error: log.error || (() => {}) };
    this.rand = rand;
    this.lastSeq = null;
    this.running = false;
    this._abort = null;
    this._loop = null;
    this.stats = {
      daemonReachable: null,
      consecutiveFailures: 0,
      lastError: null,
      lastPollAt: null,
      lastEventAt: null,
      turnsPosted: 0,
      eventsSeen: 0,
      eventsDropped: 0,
      nextRetryInMs: null,
      recentEvents: []
    };
  }

  // ---- state ---------------------------------------------------------------
  loadState() {
    try {
      const raw = JSON.parse(fs.readFileSync(this.statePath, 'utf8'));
      const seq = Number(raw?.lastSeq);
      this.lastSeq = Number.isFinite(seq) && seq >= 0 ? seq : null;
    } catch {
      this.lastSeq = null;
    }
    return this.lastSeq;
  }

  async saveState() {
    try {
      await fsp.mkdir(path.dirname(this.statePath), { recursive: true });
      const tmp = `${this.statePath}.${crypto.randomBytes(4).toString('hex')}.tmp`;
      await fsp.writeFile(tmp, JSON.stringify({ lastSeq: this.lastSeq, updatedAt: new Date().toISOString() }));
      await fsp.rename(tmp, this.statePath);
    } catch (err) {
      this.log.error('bridge_state_save_failed', { error: err.message, path: this.statePath });
    }
  }

  // ---- lifecycle -----------------------------------------------------------
  start() {
    if (this.running) return;
    this.running = true;
    this._abort = new AbortController();
    this._loop = this._run().catch((err) => {
      this.log.error('bridge_loop_crashed', { error: err?.message || String(err) });
    }).finally(() => { this.running = false; });
  }

  async stop() {
    if (!this._abort) return;
    this.running = false;
    this._abort.abort();
    const loop = this._loop;
    this._abort = null;
    if (loop) await Promise.race([loop, new Promise((r) => setTimeout(r, 2000).unref?.())]);
  }

  _sleep(ms) {
    const signal = this._abort?.signal;
    return new Promise((resolve) => {
      if (!signal || signal.aborted) return resolve();
      const t = setTimeout(() => { signal.removeEventListener('abort', onAbort); resolve(); }, ms);
      const onAbort = () => { clearTimeout(t); resolve(); };
      signal.addEventListener('abort', onAbort, { once: true });
    });
  }

  async _backoff(reason) {
    this.stats.consecutiveFailures += 1;
    const delay = backoffDelay(this.stats.consecutiveFailures, this.cfg.backoffInitialMs, this.cfg.backoffMaxMs, this.rand);
    this.stats.nextRetryInMs = delay;
    this.stats.lastError = reason;
    if (this.stats.consecutiveFailures === 1 || this.stats.consecutiveFailures % 10 === 0) {
      this.log.error('bridge_backoff', { reason, attempt: this.stats.consecutiveFailures, delayMs: delay });
    }
    await this._sleep(delay);
  }

  _ok() {
    if (this.stats.consecutiveFailures > 0) this.log.info('bridge_recovered', { after: this.stats.consecutiveFailures });
    this.stats.consecutiveFailures = 0;
    this.stats.nextRetryInMs = null;
    this.stats.daemonReachable = true;
  }

  async _run() {
    if (this.cfg.startDelayMs > 0) await this._sleep(this.cfg.startDelayMs);
    this.loadState();
    this.log.info('bridge_started', { lastSeq: this.lastSeq, statePath: this.statePath });
    const signal = () => this._abort?.signal;
    while (this.running && !signal()?.aborted) {
      try {
        await this._iteration(signal());
      } catch (err) {
        // Defensive: _iteration should not throw, but never let it end the loop.
        await this._backoff(`iteration_error: ${err?.message || err}`);
      }
    }
  }

  async _iteration(signal) {
    // First run without persisted state: start from "now" unless replay asked.
    if (this.lastSeq === null) {
      if (this.cfg.replayBacklogOnFirstRun) {
        this.lastSeq = 0;
        await this.saveState();
        return;
      }
      const r = await this.client.events({ since: 0, timeoutS: 0, signal });
      if (signal?.aborted) return;
      if (!r.ok) { this.stats.daemonReachable = !r.unreachable; return this._backoff(r.error || 'events_failed'); }
      this._ok();
      const maxEv = Math.max(0, ...(r.events || []).map((e) => Number(e.seq) || 0));
      this.lastSeq = Number.isFinite(Number(r.seq)) ? Math.max(Number(r.seq), maxEv) : maxEv;
      this.log.info('bridge_first_run_skipped_backlog', { skipped: (r.events || []).length, lastSeq: this.lastSeq });
      await this.saveState();
      return;
    }

    const startedAt = Date.now();
    const r = await this.client.events({ since: this.lastSeq, timeoutS: this.cfg.pollTimeoutS, signal });
    if (signal?.aborted) return;
    this.stats.lastPollAt = new Date().toISOString();
    if (!r.ok) {
      this.stats.daemonReachable = !r.unreachable ? true : false;
      return this._backoff(r.error || 'events_failed');
    }
    this._ok();

    const daemonSeq = Number(r.seq);
    if (Number.isFinite(daemonSeq) && daemonSeq < this.lastSeq) {
      this.log.info('bridge_daemon_seq_reset', { daemonSeq, lastSeq: this.lastSeq });
      this.lastSeq = 0;
      await this.saveState();
      return;
    }

    const events = (Array.isArray(r.events) ? r.events : [])
      .filter((e) => Number.isFinite(Number(e?.seq)))
      .sort((a, b) => Number(a.seq) - Number(b.seq));
    for (const ev of events) {
      if (signal?.aborted) return;
      const seq = Number(ev.seq);
      if (seq <= this.lastSeq) continue;
      const result = await this._dispatchWithRetry(ev, signal);
      if (result === 'stopped') return;
      this.lastSeq = seq;
      await this.saveState();
    }
    if (events.length === 0 && Date.now() - startedAt < this.cfg.minPollIntervalMs) {
      await this._sleep(this.cfg.minPollIntervalMs);
    }
  }

  async _dispatchWithRetry(ev, signal) {
    for (let attempt = 1; ; attempt += 1) {
      const res = await this.dispatch(ev, signal);
      if (signal?.aborted) return 'stopped';
      if (res.outcome !== 'retry') {
        if (this.stats.consecutiveFailures > 0) this._ok();
        return res.outcome;
      }
      if (attempt >= this.cfg.maxDispatchAttempts) {
        this.stats.eventsDropped += 1;
        this.log.error('bridge_event_dropped', { seq: ev.seq, type: ev.type, error: res.error, attempts: attempt });
        return 'dropped';
      }
      await this._backoff(`chat_post_failed: ${res.error || res.status}`);
      if (signal?.aborted) return 'stopped';
    }
  }

  _remember(ev) {
    this.stats.eventsSeen += 1;
    this.stats.lastEventAt = new Date().toISOString();
    this.stats.recentEvents.push({ seq: ev.seq, type: ev.type, ts: ev.ts ?? null });
    if (this.stats.recentEvents.length > 20) this.stats.recentEvents.shift();
  }

  // Build the turn for one event. Returns null for events that start no turn.
  buildTurn(ev) {
    const owners = this.cfg.ownerNumbers;
    if (ev?.type === 'sms_received') {
      const from = String(pick(ev, 'from', 'number', 'sender') ?? '');
      const msg = { from, text: pick(ev, 'text', 'body') ?? '', ts: pick(ev, 'ts'), id: pick(ev, 'id') };
      return isOwner(from, owners)
        ? { sessionId: this.cfg.sessionId, trusted: true, message: ownerSmsPrompt(msg) }
        : { sessionId: this.cfg.untrustedSessionId, trusted: false, message: untrustedSmsPrompt(msg, owners) };
    }
    if (ev?.type === 'call_incoming') {
      const number = String(pick(ev, 'number', 'from') ?? '');
      const call = { number, id: pick(ev, 'id', 'call_id') };
      return isOwner(number, owners)
        ? { sessionId: this.cfg.sessionId, trusted: true, message: ownerCallPrompt(call) }
        : { sessionId: this.cfg.untrustedSessionId, trusted: false, message: untrustedCallPrompt(call, { answerAllowed: this.cfg.answerUnknownCalls }) };
    }
    return null;
  }

  async dispatch(ev, signal) {
    if (!ev.__seen) { this._remember(ev); Object.defineProperty(ev, '__seen', { value: true }); }
    const turn = this.buildTurn(ev);
    if (!turn) return { outcome: 'skipped' };
    const res = await this.poster.post(turn.sessionId, turn.message, signal);
    if (res.outcome === 'delivered' || res.outcome === 'delivered_unknown') {
      this.stats.turnsPosted += 1;
      this.log.info('bridge_turn_posted', { seq: ev.seq, type: ev.type, sessionId: turn.sessionId, trusted: turn.trusted, outcome: res.outcome });
    } else if (res.outcome === 'rejected') {
      this.stats.eventsDropped += 1;
      this.log.error('bridge_turn_rejected', { seq: ev.seq, type: ev.type, status: res.status, error: res.error });
    }
    return res;
  }
}
