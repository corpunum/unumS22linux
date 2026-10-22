#!/usr/bin/env python3
"""s22-turnguard: hard wall-clock limits for the phone OpenUnum's turns (keepalive service "turnguard").

    s22-turnguard run        the watchdog (one instance, flock)
    s22-turnguard status     tracked turns, recent timeouts, config
    s22-turnguard off|on     kill switch (persistent flag; the daemon obeys within one poll)

Why (issue #75): a voice turn on the local CPU model once ran ~45 min (first-token cap 900 s, then
retries), holding the CPU and blocking suspend. OpenUnum has chatHardTimeoutMs/agentTurnTimeoutMs
and a stale-pending sweeper, but on the phone both are set to 20 days (owner policy: no guardrails
for the agent), they are global (cloud work too), and the sweeper spares a turn that still emits
events. So the limit is enforced from outside, per kind of session, through OpenUnum's own API:

* Every POLL_S it reads GET /api/chat/diagnostics (all pending turns: sessionId, turnId, startedAt).
* The session id picks a class (config "classes", first regex match): voice ("voice", "voice-conv-*")
  300 s, the shell/touch UI ("touch") 900 s; other sessions (missions, WebUI) have no limit here
  ("default_limit_s": 0). Their local-model requests are still capped by s22-llama-ondemand.
* Over the limit: POST /api/chat/cancel {sessionId} (aborts the generation, cancels subagents and
  a running plan, and records a durable cancellation so a restart does not resume it). Still
  pending CANCEL_GRACE_S later: cancel again (3 tries), then POST :8090/_s22/abort (the local
  model proxy drops every in-flight request).
* Retry budget: a turn that timed out is remembered by (session, hash of its message preview).
  The same message in the same session starting again within retry_window_s, after retry_max
  timeouts, is cancelled at once ("retry_budget"). A client that re-posts after an OpenUnum
  restart is caught the same way.
* Survives restarts: the tracked turns (with their start times) and the timeout history are in
  STATE/state.json. OpenUnum's startedAt wins when present; a guard restart never restarts a clock.

Events (JSON lines, STATE/turnguard.jsonl, rotated at 1 MiB): turn_start, turn_end, timeout_cancel,
retry_budget_cancel, recancel, escalate_abort, api_down, api_up, start. s22-trace copies them.
"""
import fcntl, hashlib, json, os, re, sys, time, urllib.error, urllib.request

STATE = "/srv/s22/state/turnguard"
FLAG = STATE + "/off"
CFG_FILE = STATE + "/config.json"
LOG = STATE + "/turnguard.jsonl"
STATE_FILE = STATE + "/state.json"
LOCK = "/run/s22-turnguard.lock"
API = "http://127.0.0.1:18880"
PROXY = "http://127.0.0.1:8090"
LOG_MAX = 1 << 20

CONFIG = {
    "poll_s": 10,
    "classes": [
        {"name": "voice", "match": "^voice($|-)", "limit_s": 300},
        {"name": "touch", "match": "^touch$", "limit_s": 900},
    ],
    "default_limit_s": 0,          # 0 = no limit for other sessions
    "cancel_grace_s": 20,
    "max_cancel_tries": 3,
    "retry_max": 2,                # timeouts of the same message before a new attempt is cut at once
    "retry_window_s": 1800,
    "forget_s": 86400,
}


def log(**kw):
    kw["t"] = round(time.time(), 3)
    try:
        os.makedirs(STATE, exist_ok=True)
        if os.path.exists(LOG) and os.path.getsize(LOG) > LOG_MAX:
            os.replace(LOG, LOG + ".1")
        with open(LOG, "a") as f:
            f.write(json.dumps(kw) + "\n")
    except OSError:
        pass


def load_cfg():
    c = dict(CONFIG)
    try:
        c.update(json.load(open(CFG_FILE)))
    except (OSError, ValueError):
        pass
    return c


def http(method, url, body=None, timeout=8):
    req = urllib.request.Request(url, data=None if body is None else json.dumps(body).encode(), method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b"{}")


def parse_iso(s):
    if not s:
        return None
    try:
        import datetime
        return datetime.datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def classify(session, cfg):
    """(class name, limit_s) for a session id."""
    for c in cfg.get("classes") or []:
        try:
            if re.search(c["match"], session):
                return c.get("name", c["match"]), float(c.get("limit_s") or 0)
        except (re.error, KeyError, TypeError):
            continue
    return "other", float(cfg.get("default_limit_s") or 0)


def msg_hash(session, preview):
    return hashlib.sha256(("%s\0%s" % (session, (preview or "").strip())).encode()).hexdigest()[:16]


class Guard:
    """Pure-ish core: step(pending, now) -> actions done through self.cancel / self.escalate."""

    def __init__(self, cfg, state=None, cancel=None, escalate=None, save=None):
        self.cfg = cfg
        st = state or {}
        self.turns = st.get("turns") or {}          # key -> {session, turn, started, limit, cls, h, cancelled_at, tries}
        self.timeouts = st.get("timeouts") or {}    # msg hash -> [t, ...]
        self.cancel = cancel or (lambda sid: http("POST", API + "/api/chat/cancel", {"sessionId": sid}))
        self.escalate = escalate or (lambda: http("POST", PROXY + "/_s22/abort", {}, timeout=5))
        self.save = save or (lambda st: None)

    def snapshot(self):
        return {"turns": self.turns, "timeouts": self.timeouts}

    def _cancel(self, k, tr, now, event, **extra):
        try:
            out = self.cancel(tr["session"])
            ok = bool(out.get("ok", True))
        except Exception as e:  # noqa: BLE001
            out, ok = {"error": str(e)[:120]}, False
        tr["cancelled_at"] = tr.get("cancelled_at") or now
        tr["last_cancel"] = now
        tr["tries"] = tr.get("tries", 0) + 1
        tr["why"] = tr.get("why") or event
        log(event=event, session=tr["session"], turn=tr["turn"], age_s=round(now - tr["started"], 1),
            limit_s=tr["limit"], cls=tr["cls"], ok=ok, tries=tr["tries"], answer=str(out)[:160], **extra)

    def step(self, pending, now):
        cfg = self.cfg
        seen = set()
        changed = False
        win = float(cfg["retry_window_s"])
        self.timeouts = {h: [t for t in ts if now - t < win] for h, ts in self.timeouts.items()}
        self.timeouts = {h: ts for h, ts in self.timeouts.items() if ts}
        for p in pending:
            sid = str(p.get("sessionId") or "")
            if not sid:
                continue
            k = "%s|%s" % (sid, p.get("turnId") or "")
            seen.add(k)
            tr = self.turns.get(k)
            if tr is None:
                cls, limit = classify(sid, cfg)
                started = parse_iso(p.get("startedAt"))
                if started is None:
                    started = now - float(p.get("ageMs") or 0) / 1000.0
                h = msg_hash(sid, p.get("messagePreview"))
                tr = {"session": sid, "turn": p.get("turnId"), "started": started, "limit": limit, "cls": cls, "h": h}
                self.turns[k] = tr
                changed = True
                log(event="turn_start", session=sid, turn=tr["turn"], cls=cls, limit_s=limit,
                    age_s=round(now - started, 1))
                prior = len(self.timeouts.get(h, []))
                if limit and prior >= int(cfg["retry_max"]) > 0:
                    self._cancel(k, tr, now, "retry_budget_cancel", prior_timeouts=prior)
                    continue
            if tr.get("cancelled_at"):
                if now - tr["last_cancel"] >= float(cfg["cancel_grace_s"]):
                    if tr["tries"] < int(cfg["max_cancel_tries"]):
                        self._cancel(k, tr, now, "recancel")
                    elif not tr.get("escalated"):
                        tr["escalated"] = now
                        try:
                            out = self.escalate()
                        except Exception as e:  # noqa: BLE001
                            out = {"error": str(e)[:120]}
                        log(event="escalate_abort", session=sid, turn=tr["turn"], answer=str(out)[:160])
                    changed = True
                continue
            if tr["limit"] and now - tr["started"] >= tr["limit"]:
                self._cancel(k, tr, now, "timeout_cancel")
                self.timeouts.setdefault(tr["h"], []).append(now)
                changed = True
        for k in [k for k in self.turns if k not in seen]:
            tr = self.turns.pop(k)
            changed = True
            log(event="turn_end", session=tr["session"], turn=tr["turn"], cls=tr["cls"],
                dur_s=round(now - tr["started"], 1), cancelled=bool(tr.get("cancelled_at")), why=tr.get("why"),
                after_cancel_s=round(now - tr["cancelled_at"], 1) if tr.get("cancelled_at") else None)
        if changed:
            self.save(self.snapshot())
        return changed


def save_state(st):
    try:
        os.makedirs(STATE, exist_ok=True)
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(st, f)
        os.replace(tmp, STATE_FILE)
    except OSError:
        pass


def load_state():
    try:
        st = json.load(open(STATE_FILE))
        return st if isinstance(st, dict) else {}
    except (OSError, ValueError):
        return {}


def run():
    os.makedirs(STATE, exist_ok=True)
    lockf = open(LOCK, "w")
    try:
        fcntl.flock(lockf, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("s22-turnguard: already running", file=sys.stderr)
        return 1
    cfg = load_cfg()
    g = Guard(cfg, load_state(), save=save_state)
    log(event="start", pid=os.getpid(), tracked=len(g.turns), cfg=cfg)
    api_ok = None
    while True:
        cfg = load_cfg()
        g.cfg = cfg
        if not os.path.exists(FLAG):
            try:
                d = http("GET", API + "/api/chat/diagnostics?limit=80", timeout=8)
                pending = d.get("pending") or []
                if api_ok is not True:
                    log(event="api_up")
                    api_ok = True
                g.step(pending, time.time())
            except (urllib.error.URLError, OSError, ValueError) as e:
                # OpenUnum down/restarting: keep the tracked turns; they end (turn_end) when it answers
                if api_ok is not False:
                    log(event="api_down", error=str(e)[:120])
                    api_ok = False
            except Exception as e:  # noqa: BLE001
                log(event="error", error=repr(e)[:200])
        time.sleep(float(cfg["poll_s"]))


def status():
    st = load_state()
    cfg = load_cfg()
    print("turnguard:", "OFF (kill switch)" if os.path.exists(FLAG) else "active")
    print("classes:", [(c.get("name"), c.get("match"), c.get("limit_s")) for c in cfg["classes"]],
          "default", cfg["default_limit_s"])
    now = time.time()
    for k, tr in (st.get("turns") or {}).items():
        print("  %s  age %.0fs  limit %s  cancelled=%s" % (k, now - tr["started"], tr["limit"],
                                                          bool(tr.get("cancelled_at"))))
    print("recent timeouts:", {h: len(v) for h, v in (st.get("timeouts") or {}).items()})
    try:
        for line in open(LOG).read().splitlines()[-6:]:
            print(line)
    except OSError:
        pass
    return 0


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "status"
    if cmd == "run":
        return run() or 0
    if cmd == "off":
        os.makedirs(STATE, exist_ok=True)
        open(FLAG, "w").close()
        log(event="disabled")
        print("turnguard off (persistent). Back on: s22-turnguard on")
        return 0
    if cmd == "on":
        try:
            os.unlink(FLAG)
        except FileNotFoundError:
            pass
        log(event="enabled")
        print("turnguard on")
        return 0
    if cmd == "status":
        return status()
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
