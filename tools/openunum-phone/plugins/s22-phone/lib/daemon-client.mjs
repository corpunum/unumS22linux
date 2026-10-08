// Minimal JSON client for the s22-phoned HTTP API (node built-in fetch only).
//
// Every call resolves (never throws) to either the daemon's JSON body or
// { ok:false, error, message, unreachable? }. Non-2xx responses carrying the
// daemon's {"ok":false,"error"} body are passed through unchanged.

// AbortSignal.any() needs node >= 20.3; combine by hand on older runtimes.
export function anySignal(signals) {
  if (typeof AbortSignal.any === 'function') return AbortSignal.any(signals);
  const ctl = new AbortController();
  for (const s of signals) {
    if (s.aborted) { ctl.abort(s.reason); break; }
    s.addEventListener('abort', () => ctl.abort(s.reason), { once: true });
  }
  return ctl.signal;
}

export class DaemonClient {
  constructor({ baseUrl = 'http://127.0.0.1:8095', timeoutMs = 15000, fetchImpl = globalThis.fetch } = {}) {
    this.baseUrl = String(baseUrl || 'http://127.0.0.1:8095').replace(/\/+$/, '');
    this.timeoutMs = Number(timeoutMs) > 0 ? Number(timeoutMs) : 15000;
    this.fetch = fetchImpl;
  }

  async request(method, pathname, { body, query, timeoutMs, signal } = {}) {
    let url = this.baseUrl + pathname;
    if (query) {
      const qs = new URLSearchParams();
      for (const [k, v] of Object.entries(query)) {
        if (v !== undefined && v !== null && v !== '') qs.set(k, String(v));
      }
      const s = qs.toString();
      if (s) url += `?${s}`;
    }
    const timeout = AbortSignal.timeout(Number(timeoutMs) > 0 ? Number(timeoutMs) : this.timeoutMs);
    const sig = signal ? anySignal([signal, timeout]) : timeout;
    let res;
    try {
      res = await this.fetch(url, {
        method,
        headers: body !== undefined ? { 'content-type': 'application/json' } : undefined,
        body: body !== undefined ? JSON.stringify(body) : undefined,
        signal: sig
      });
    } catch (err) {
      const aborted = err?.name === 'AbortError' || err?.name === 'TimeoutError';
      return {
        ok: false,
        error: aborted ? 'daemon_timeout' : 'daemon_unreachable',
        unreachable: true,
        message: `s22-phoned at ${this.baseUrl} ${aborted ? 'did not answer in time' : 'is not reachable'}: ${err?.cause?.code || err?.message || err}`
      };
    }
    let text = '';
    try { text = await res.text(); } catch { /* empty body */ }
    let data = null;
    try { data = text ? JSON.parse(text) : null; } catch { data = null; }
    if (data && typeof data === 'object' && !Array.isArray(data)) {
      if (!res.ok && data.ok !== false) data = { ...data, ok: false, error: data.error || `http_${res.status}` };
      return { ...data, httpStatus: res.status };
    }
    return {
      ok: false,
      error: res.ok ? 'daemon_bad_response' : `http_${res.status}`,
      httpStatus: res.status,
      message: `s22-phoned returned a non-JSON response (${res.status}): ${text.slice(0, 200)}`
    };
  }

  status() { return this.request('GET', '/status'); }
  calls() { return this.request('GET', '/calls'); }
  smsSend(to, text) { return this.request('POST', '/sms/send', { body: { to, text } }); }
  smsInbox({ since, limit } = {}) { return this.request('GET', '/sms/inbox', { query: { since, limit } }); }
  smsOutbox({ limit } = {}) { return this.request('GET', '/sms/outbox', { query: { limit } }); }
  callDial(number) { return this.request('POST', '/call/dial', { body: { number } }); }
  callAnswer() { return this.request('POST', '/call/answer', { body: {} }); }
  callHangup(id) { return this.request('POST', '/call/hangup', { body: id === undefined || id === null || id === '' ? {} : { id } }); }
  callDtmf(digits) { return this.request('POST', '/call/dtmf', { body: { digits } }); }
  events({ since = 0, timeoutS = 25, signal } = {}) {
    return this.request('GET', '/events', {
      query: { since, timeout: timeoutS },
      timeoutMs: (Number(timeoutS) + 10) * 1000,
      signal
    });
  }
}
