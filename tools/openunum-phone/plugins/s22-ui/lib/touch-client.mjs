// Client for s22-touchd's control socket: one JSON request per connection, one JSON line back.
import net from 'node:net';

export class TouchClient {
  constructor({ socketPath, timeoutMs = 20000 } = {}) {
    this.socketPath = socketPath;
    this.timeoutMs = timeoutMs;
  }

  request(req, timeoutMs = this.timeoutMs) {
    return new Promise((resolve) => {
      let buf = '';
      let done = false;
      const finish = (v) => { if (!done) { done = true; try { sock.destroy(); } catch { /* ignore */ } resolve(v); } };
      const sock = net.createConnection(this.socketPath);
      sock.setTimeout(timeoutMs, () => finish({ ok: false, error: 'touchd_timeout' }));
      sock.on('connect', () => sock.write(JSON.stringify(req) + '\n'));
      sock.on('data', (d) => {
        buf += d.toString();
        const i = buf.indexOf('\n');
        if (i >= 0) {
          try { finish(JSON.parse(buf.slice(0, i))); } catch { finish({ ok: false, error: 'bad_response', raw: buf.slice(0, 300) }); }
        }
      });
      sock.on('error', (e) => finish({ ok: false, error: 'touchd_unreachable', message: e.code || e.message, unreachable: true }));
      sock.on('end', () => finish(buf ? (() => { try { return JSON.parse(buf); } catch { return { ok: false, error: 'bad_response' }; } })() : { ok: false, error: 'empty_response' }));
    });
  }

  ui(fn, ...args) {
    return this.request({ cmd: 'ui', args: [fn, ...args.map(String)], timeout: 8 });
  }
}
