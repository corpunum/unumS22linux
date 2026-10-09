// s22-ui: OpenUnum plugin exposing the S22 touch UI's actions as agent tools.
//
// The owner (tiles, gestures, Power key) and the agent use ONE action surface:
// s22-touchd's control socket (default /run/s22-touch/ctl.sock inside the Arch
// chroot, where the phone's OpenUnum runs). Tools:
//   ui_open_app, ui_home, ui_show_card, ui_confirm, ui_keyboard, ui_screen,
//   ui_state, ui_screenshot.
// The untrusted phone session (messages from unknown senders) may only show a
// notification card: it cannot open apps, ask the owner to confirm (that would
// let a stranger's SMS put words in the agent's mouth), or switch the screen.
//
// Config: plugin.json "config", overridden by $OPENUNUM_HOME/plugins.json
// {"s22-ui": {...}}, overridden by env S22_UI_SOCKET, S22_UI_RUN_DIR.
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { execFile } from 'node:child_process';
import { PluginBase, pluginBaseSource } from './lib/plugin-base.mjs';
import { TouchClient } from './lib/touch-client.mjs';

export { pluginBaseSource };

export const APPS = ['chat', 'phone', 'camera', 'files', 'settings', 'switcher', 'terminal', 'agent'];
const SCREEN_ACTIONS = ['on', 'off', 'lock', 'unlock', 'status'];
const MUTATING = { actionClass: 'mutating', evidenceRole: 'mutation' };
const OBSERVING = { actionClass: 'observational', evidenceRole: 'observation' };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function num(v, dflt) {
  const n = Number(v);
  return Number.isFinite(n) ? n : dflt;
}

export function resolveConfig(raw = {}, env = process.env) {
  const c = { ...raw };
  if (env.S22_UI_SOCKET) c.socket_path = env.S22_UI_SOCKET;
  if (env.S22_UI_RUN_DIR) c.run_dir = env.S22_UI_RUN_DIR;
  return {
    socketPath: String(c.socket_path || '/run/s22-touch/ctl.sock'),
    runDir: String(c.run_dir || '/run/s22-touch'),
    timeoutMs: num(c.request_timeout_ms, 20000),
    untrustedSessionId: String(c.untrusted_session_id || 'phone:untrusted'),
    screenshotDir: String(c.screenshot_dir || '/tmp'),
    waylandDisplay: String(c.wayland_display || 'wayland-1'),
    xdgRuntimeDir: String(c.xdg_runtime_dir || '/run/user/0'),
    confirmPollMs: num(c.confirm_poll_ms, 500)
  };
}

export default class S22UiPlugin extends PluginBase {
  constructor(manifest, ctx = {}) {
    super(manifest, ctx);
    this.settings = resolveConfig({});
    this.client = new TouchClient({ socketPath: this.settings.socketPath, timeoutMs: this.settings.timeoutMs });
  }

  async onInit(config) {
    this.config = { ...this.config, ...config };
    this.settings = resolveConfig(this.config, process.env);
    this.client = new TouchClient({ socketPath: this.settings.socketPath, timeoutMs: this.settings.timeoutMs });
    try { this.ctx?.log?.info?.('init', { socket: this.settings.socketPath, pluginBase: pluginBaseSource }); } catch { /* ignore */ }
  }

  async onHealth() {
    const st = await this.client.request({ cmd: 'status' }, 8000);
    return { ok: true, touchd_reachable: st.ok === true, touchd: st };
  }

  _untrusted(input) {
    const sid = String(input?.context?.sessionId || '');
    return sid && sid === this.settings.untrustedSessionId;
  }

  _blocked(name) {
    return {
      ok: false, error: 'untrusted_session_blocked',
      message: `${name} is blocked in the ${this.settings.untrustedSessionId} session (messages from unknown senders). Only ui_show_card is allowed there.`
    };
  }

  _map(r, extra = {}) {
    if (r?.ok) return { ok: true, ...extra, ...(r.result !== undefined ? { result: r.result } : r) };
    const out = { ok: false, error: r?.error || 'touch_error', ...extra };
    if (r?.message || r?.stderr) out.message = r.message || r.stderr;
    if (r?.unreachable || r?.error === 'touchd_unreachable') out.hint = 's22-touchd is not running on the phone; the touch UI is unavailable. Report it, do not retry in a loop.';
    if (r?.error === 'no_session') out.hint = 'No graphical session (Hyprland) is running on the phone.';
    return out;
  }

  async confirm(question, timeoutS) {
    const id = 'a' + crypto.randomBytes(6).toString('hex');
    const t = Math.max(5, Math.min(600, Math.trunc(num(timeoutS, 60))));
    const r = await this.client.ui('confirm', id, question, t);
    if (!r?.ok || r.result !== 'asking') {
      return this._map(r?.ok ? { ok: false, error: r.result === 'busy' ? 'confirm_busy' : 'confirm_not_shown', message: String(r.result) } : r);
    }
    const file = path.join(this.settings.runDir, 'confirm', `${id}.json`);
    const deadline = Date.now() + (t + 5) * 1000;
    while (Date.now() < deadline) {
      try {
        const ans = JSON.parse(fs.readFileSync(file, 'utf8'));
        try { fs.unlinkSync(file); } catch { /* ignore */ }
        return { ok: true, id, answer: ans.answer === 'yes' ? 'yes' : ans.answer === 'no' ? 'no' : 'timeout' };
      } catch { /* not answered yet */ }
      await sleep(this.settings.confirmPollMs);
    }
    return { ok: true, id, answer: 'timeout' };
  }

  screenshot() {
    const file = path.join(this.settings.screenshotDir, `s22-screen-${Date.now()}.png`);
    const env = { ...process.env, WAYLAND_DISPLAY: this.settings.waylandDisplay, XDG_RUNTIME_DIR: this.settings.xdgRuntimeDir };
    return new Promise((resolve) => {
      execFile('grim', ['-l', '1', file], { env, timeout: 30000 }, (err, _out, stderr) => {
        if (err) resolve({ ok: false, error: 'screenshot_failed', message: String(stderr || err.message).slice(0, 300) });
        else resolve({ ok: true, path: file, format: 'png' });
      });
    });
  }

  getTools() {
    const self = this;
    const argsOf = (input) => (input && typeof input === 'object' && input.args && typeof input.args === 'object' ? input.args : (input || {}));
    return [
      {
        name: 'ui_open_app',
        description: `Open an app or page on the S22 phone's screen for the owner: ${APPS.join(', ')}. (chat = typed chat with you; terminal = shell; agent = Pi terminal.)`,
        parameters: { type: 'object', properties: { app: { type: 'string', enum: APPS } }, required: ['app'] },
        semantics: MUTATING,
        execute: async (input) => {
          if (self._untrusted(input)) return self._blocked('ui_open_app');
          const { app } = argsOf(input);
          if (!APPS.includes(app)) return { ok: false, error: 'unknown_app', message: `app must be one of ${APPS.join(', ')}` };
          const r = self._map(await self.client.ui('open', app), { app });
          if (r.ok && r.result !== 'ok') return { ok: false, error: 'open_failed', message: String(r.result), app };
          return r;
        }
      },
      {
        name: 'ui_home',
        description: 'Show the S22 phone home screen (big tiles + agent status cards).',
        parameters: { type: 'object', properties: {} },
        semantics: MUTATING,
        execute: async (input) => (self._untrusted(input) ? self._blocked('ui_home') : self._map(await self.client.ui('home')))
      },
      {
        name: 'ui_show_card',
        description: 'Show a notification card at the top of the S22 phone screen (title + short text). Silent: the phone is muted. Use it to tell the owner something they should see on the phone.',
        parameters: {
          type: 'object',
          properties: {
            title: { type: 'string', description: 'Short title (max 80 chars)' },
            body: { type: 'string', description: 'Card text (max 400 chars)' },
            ttl_s: { type: 'integer', description: 'Seconds to keep it visible (3-120, default 8)' }
          },
          required: ['title']
        },
        semantics: MUTATING,
        execute: async (input) => {
          const { title, body, ttl_s } = argsOf(input);
          const t = String(title ?? '').trim();
          if (!t) return { ok: false, error: 'empty_title' };
          const prefix = self._untrusted(input) ? '[unknown sender] ' : '';
          return self._map(await self.client.ui('card', (prefix + t).slice(0, 80), String(body ?? '').slice(0, 400), Math.max(3, Math.min(120, Math.trunc(num(ttl_s, 8))))));
        }
      },
      {
        name: 'ui_confirm',
        description: 'Ask the owner a yes/no question on the S22 phone screen and WAIT for the tap. Returns answer "yes", "no" or "timeout". Treat anything but "yes" as no.',
        parameters: {
          type: 'object',
          properties: {
            question: { type: 'string', description: 'The question (max 500 chars)' },
            timeout_s: { type: 'integer', description: 'How long to wait (5-600, default 60)' }
          },
          required: ['question']
        },
        semantics: MUTATING,
        execute: async (input) => {
          if (self._untrusted(input)) return self._blocked('ui_confirm');
          const { question, timeout_s } = argsOf(input);
          const q = String(question ?? '').trim();
          if (!q) return { ok: false, error: 'empty_question' };
          return self.confirm(q.slice(0, 500), timeout_s);
        }
      },
      {
        name: 'ui_keyboard',
        description: 'Show or hide the on-screen keyboard on the S22 phone.',
        parameters: { type: 'object', properties: { visible: { type: 'boolean' } }, required: ['visible'] },
        semantics: MUTATING,
        execute: async (input) => {
          if (self._untrusted(input)) return self._blocked('ui_keyboard');
          const { visible } = argsOf(input);
          return self._map(await self.client.ui('keyboard', visible === false || visible === 'false' ? 'off' : 'on'));
        }
      },
      {
        name: 'ui_screen',
        description: 'S22 screen power and lock: on, off (shows the lock cover, then turns the panel off), lock, unlock, status.',
        parameters: { type: 'object', properties: { action: { type: 'string', enum: SCREEN_ACTIONS } }, required: ['action'] },
        semantics: MUTATING,
        execute: async (input) => {
          const { action } = argsOf(input);
          if (!SCREEN_ACTIONS.includes(action)) return { ok: false, error: 'bad_action', message: `action must be one of ${SCREEN_ACTIONS.join(', ')}` };
          if (action !== 'status' && self._untrusted(input)) return self._blocked('ui_screen');
          if (action === 'lock' || action === 'unlock') return self._map(await self.client.ui(action), { action });
          return self._map(await self.client.request({ cmd: 'display', arg: action }), { action });
        }
      },
      {
        name: 'ui_state',
        description: 'What the S22 touch UI shows now (home/page/locked/cards/pending confirm, agent status cards) plus mute and display state.',
        parameters: { type: 'object', properties: {} },
        semantics: OBSERVING,
        execute: async () => {
          const ui = await self.client.ui('state');
          const host = await self.client.request({ cmd: 'status' });
          let state = ui?.result;
          if (typeof state === 'string') { try { state = JSON.parse(state); } catch { /* keep string */ } }
          if (!ui?.ok && !host?.ok) return self._map(ui);
          return { ok: true, ui: ui?.ok ? state : { error: ui?.error }, host: host?.ok ? host : { error: host?.error } };
        }
      },
      {
        name: 'ui_screenshot',
        description: 'Take a screenshot of the S22 phone screen (PNG, 1080x2340). Returns the file path on the phone.',
        parameters: { type: 'object', properties: {} },
        semantics: OBSERVING,
        execute: async (input) => (self._untrusted(input) ? self._blocked('ui_screenshot') : self.screenshot())
      }
    ];
  }

  getRoutes() {
    return [{
      method: 'GET',
      path: '/status',
      handler: async (req, res) => {
        let body;
        try { body = await this.onHealth(); } catch (err) { body = { ok: false, error: err.message }; }
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify(body));
      }
    }];
  }
}
