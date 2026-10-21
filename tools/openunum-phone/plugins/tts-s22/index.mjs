// tts-s22: OpenUnum text_to_speech backend for the S22 native-Linux phone.
//
// The rig's backends (tts-orpheus, tts-kokoro) need servers the phone does not
// run. This one asks s22-touchd (native root) to render speech with s22-say
// (WAV under /run/s22-touch/tts) and, unless play=false, to speak it with
// s22-say --wav: firmware-graph audio path, a temporary speech level, and the
// amps back at the resting mute afterwards (owner 2026-10-21: the phone talks).
// Select it with runtime.mediaBackends.text_to_speech = ["tts-s22"].
import { execFile } from 'node:child_process';
import { PluginBase, pluginBaseSource } from './lib/plugin-base.mjs';

export { pluginBaseSource };

export const ENGINES = ['supertonic', 'paradee', 'kitten', 'piper', 'kokoro', 'el-antonis', 'el-female', 'espeak'];
export const OUTPUTS = ['speaker', 'earpiece', 'top', 'bottom'];

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
      description: 'Speak text aloud through the phone speaker with on-device neural TTS (Greek text: engine el-antonis or el-female). Returns the WAV path and whether it was played; play=false only renders the file.',
      parameters: {
        type: 'object',
        properties: {
          text: { type: 'string', description: 'Text to synthesize' },
          engine: { type: 'string', enum: ENGINES, description: 'TTS engine (default supertonic; Greek: el-antonis / el-female)' },
          play: { type: 'boolean', description: 'Speak it on the phone (default true)' },
          out: { type: 'string', enum: OUTPUTS, description: 'Output: speaker (default), earpiece, top, bottom' }
        },
        required: ['text']
      },
      actionClass: 'mutating',
      evidenceRole: 'mutation',
      execute: async ({ args } = {}) => {
        const text = String(args?.text || '').trim();
        if (!text) return { ok: false, error: 'empty_text' };
        const engine = ENGINES.includes(args?.engine) ? args.engine : (this.config.engine || 'supertonic');
        const play = typeof args?.play === 'boolean' ? args.play : this.config.play !== false;
        const out = OUTPUTS.includes(args?.out) ? args.out : (this.config.out || 'speaker');
        const argv = ['tts', '--engine', engine, ...(play ? ['--play', '--out', out] : []), text];
        const r = await runCli(this.config.ui_cli || '/usr/local/bin/s22-ui', argv, Number(this.config.timeout_ms) || 500000);
        if (!r.ok) return { ok: false, error: r.error || 'render_failed', detail: r.tail };
        const durationMs = Math.max(500, Math.round((text.split(/\s+/).length / 150) * 60000));
        const res = { ok: true, audio: { path: r.path, format: 'wav', durationMs }, engine: r.engine, renderMs: r.ms, played: !!r.played };
        if (play) {
          res.out = r.out || out; res.playMs = r.play_ms;
          if (!r.played) { res.playError = r.play_error || r.play_tail || `play rc ${r.play_rc}`; res.note = 'Rendered but playback failed.'; }
        } else res.note = 'Rendered only (play=false).';
        return res;
      }
    }];
  }
}
