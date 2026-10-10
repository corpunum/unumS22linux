// tts-s22: OpenUnum text_to_speech backend for the S22 native-Linux phone.
//
// The rig's backends (tts-orpheus, tts-kokoro) need servers the phone does not
// run. This one asks s22-touchd (native root) to render speech with s22-say in
// render-only mode: the WAV is written under /run/s22-touch/tts and nothing is
// played (no mixer route, no amp), so the owner's mute rule holds.
// Select it with runtime.mediaBackends.text_to_speech = ["tts-s22"].
import { execFile } from 'node:child_process';
import { PluginBase, pluginBaseSource } from './lib/plugin-base.mjs';

export { pluginBaseSource };

export const ENGINES = ['supertonic', 'paradee', 'kitten', 'piper', 'kokoro', 'espeak'];

export function runCli(cli, args, timeoutMs) {
  return new Promise((resolve) => {
    execFile(cli, args, { timeout: timeoutMs, maxBuffer: 1 << 20 }, (err, stdout, stderr) => {
      if (err && !stdout) {
        resolve({ ok: false, error: err.code === 'ENOENT' ? 'tts backend not installed (s22-ui missing)' : `tts unavailable: ${String(stderr || err.message).slice(-300)}` });
        return;
      }
      try { resolve(JSON.parse(stdout)); } catch { resolve({ ok: false, error: `bad reply: ${String(stdout).slice(0, 200)}` }); }
    });
  });
}

export default class TtsS22Plugin extends PluginBase {
  async onInit(config) {
    this.config = { ...this.config, ...config };
  }

  getTools() {
    return [{
      name: 'text_to_speech',
      description: 'Synthesize speech on the phone (on-device neural TTS). Returns a WAV file path; audio is NOT played because the phone speaker is muted by the owner.',
      parameters: {
        type: 'object',
        properties: {
          text: { type: 'string', description: 'Text to synthesize' },
          engine: { type: 'string', enum: ENGINES, description: 'TTS engine (default supertonic)' }
        },
        required: ['text']
      },
      actionClass: 'mutating',
      evidenceRole: 'mutation',
      execute: async ({ args } = {}) => {
        const text = String(args?.text || '').trim();
        if (!text) return { ok: false, error: 'empty_text' };
        const engine = ENGINES.includes(args?.engine) ? args.engine : (this.config.engine || 'supertonic');
        const r = await runCli(this.config.ui_cli || '/usr/local/bin/s22-ui', ['tts', '--engine', engine, text], Number(this.config.timeout_ms) || 200000);
        if (!r.ok) return { ok: false, error: r.error || 'render_failed', detail: r.tail };
        const durationMs = Math.max(500, Math.round((text.split(/\s+/).length / 150) * 60000));
        return { ok: true, audio: { path: r.path, format: 'wav', durationMs }, engine: r.engine, renderMs: r.ms, played: false,
          note: 'Rendered on the phone; not played (speaker muted by owner rule).' };
      }
    }];
  }
}
