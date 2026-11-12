// Reply-confirm for actions an SMS asked for (owner decision 11-12, option B). SMS sender IDs can be spoofed,
// so a destructive or irreversible request that arrived by SMS runs only after the agent texts a one-time code
// to the allowlisted number (sms_confirm_request) and that number answers "YES <code>" within the TTL.
// A spoofer never sees the code: it goes to the real number. One use per code; stored 0600 in the data dir.
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { numbersMatch } from './policy.mjs';

const ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789';   // no 0/O, 1/I
export const CONFIRM_RE = /^\s*YES\s+([A-Z0-9]{6})\s*[.!]?\s*$/i;

export function newCode(rand = crypto.randomBytes) {
  const b = rand(6);
  return Array.from(b, (x) => ALPHABET[x % ALPHABET.length]).join('');
}

export class ConfirmStore {
  constructor(file, { ttlMs = 10 * 60 * 1000, max = 20, now = () => Date.now(), rand } = {}) {
    this.file = file;
    this.ttlMs = ttlMs;
    this.max = max;
    this.now = now;
    this.rand = rand;
  }

  _load() {
    try {
      const d = JSON.parse(fs.readFileSync(this.file, 'utf8'));
      return d && typeof d === 'object' ? d : {};
    } catch { return {}; }
  }

  _save(d) {
    fs.mkdirSync(path.dirname(this.file), { recursive: true });
    const tmp = `${this.file}.tmp`;
    fs.writeFileSync(tmp, JSON.stringify(d), { mode: 0o600 });
    fs.renameSync(tmp, this.file);
  }

  _live(d) {
    const t = this.now();
    for (const [k, v] of Object.entries(d)) if (!v || v.expiresAt <= t) delete d[k];
    return d;
  }

  /** Register a pending action for `to`; returns { code, expiresAt }. */
  create(to, summary, { kind = 'action', maxChars = 300 } = {}) {
    const d = this._live(this._load());
    const keys = Object.keys(d);
    if (keys.length >= this.max) delete d[keys.sort((a, b) => d[a].createdAt - d[b].createdAt)[0]];
    let code;
    do { code = newCode(this.rand); } while (d[code]);
    const expiresAt = this.now() + this.ttlMs;
    d[code] = { to, kind, summary: String(summary).slice(0, maxChars), createdAt: this.now(), expiresAt };
    this._save(d);
    return { code, expiresAt };
  }

  /** Redeem "YES <code>" from `from`: the pending record, or null (unknown, expired, used, other number). */
  redeem(code, from) {
    const d = this._live(this._load());
    const key = String(code || '').toUpperCase();
    const rec = d[key];
    if (!rec || !numbersMatch(rec.to, from)) { this._save(d); return null; }
    delete d[key];
    this._save(d);
    return { code: key, ...rec };
  }
}
