#!/usr/bin/env python3
"""Fault injection for issue #75 (run ON the phone, detached; writes JSON lines to OUT).

  python3 fault-inject.py proxy  OUT   isolated: a test s22-llama-ondemand on :18090 in front of a fake
                                       hung backend on :18091 that burns one core until its client
                                       disconnects (mode 'hang') or even after (mode 'stubborn').
  python3 fault-inject.py real   OUT   the real proxy :8090 + real llama-server: one streamed request
                                       whose prefill takes far longer than FIRST_S (150 s), then a short
                                       normal request.
  python3 fault-inject.py turn   OUT   the real OpenUnum + s22-turnguard: a turn in a test session class
                                       with a 45 s limit that runs a 150 s shell command.

Never touches :8090/:8091 in 'proxy' mode. CPU = utime+stime of a process group from /proc.
"""
import json, os, signal, socket, subprocess, sys, threading, time, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
PROXY_BIN = os.environ.get("S22_PROXY_BIN", "/srv/s22/hardware/bin/s22-llama-ondemand")
FAKE = r'''
import http.server, os, select, sys, time
port = int(sys.argv[1]); mode_file = sys.argv[2]
class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        self.send_response(200); self.send_header('Content-Length', '15'); self.end_headers()
        self.wfile.write(b'{"status":"ok"}')
    def do_POST(self):
        n = int(self.headers.get('Content-Length', 0)); self.rfile.read(n)
        mode = open(mode_file).read().strip() if os.path.exists(mode_file) else 'hang'
        t0 = time.time(); closed = False
        while time.time() - t0 < 600:            # "prefill": one core at 100 %
            x = 0
            for i in range(50000): x += i
            if not closed:
                r, _, _ = select.select([self.connection], [], [], 0)
                if r and not self.connection.recv(1, 2):
                    closed = True
                    sys.stderr.write('client closed after %.1f s\n' % (time.time() - t0)); sys.stderr.flush()
                    if mode != 'stubborn':
                        return
http.server.ThreadingHTTPServer(('127.0.0.1', port), H).serve_forever()
'''


def out(rec):
    rec["t"] = round(time.time(), 2)
    with open(OUT, "a") as f:
        f.write(json.dumps(rec) + "\n")
    print(json.dumps(rec), flush=True)


def pg_ticks(pgid):
    tot = 0
    for p in os.listdir("/proc"):
        if p.isdigit():
            try:
                f = open("/proc/%s/stat" % p).read().rsplit(")", 1)[1].split()
                if int(f[2]) == pgid:
                    tot += int(f[11]) + int(f[12])
            except (OSError, IndexError, ValueError):
                pass
    return tot


def pg_alive(pgid):
    for p in os.listdir("/proc"):
        if p.isdigit():
            try:
                if int(open("/proc/%s/stat" % p).read().rsplit(")", 1)[1].split()[2]) == pgid:
                    return True
            except (OSError, IndexError, ValueError):
                pass
    return False


def cpu_pct(pgid, secs=3.0):
    a = pg_ticks(pgid)
    time.sleep(secs)
    return round(100.0 * (pg_ticks(pgid) - a) / (os.sysconf("SC_CLK_TCK") * secs), 1)


def find_pid(needle):
    for p in os.listdir("/proc"):
        if p.isdigit():
            try:
                c = open("/proc/%s/cmdline" % p, "rb").read().replace(b"\0", b" ").decode()
            except OSError:
                continue
            if needle in c and "fault-inject" not in c:
                return int(p)
    return None


def post(port, body, timeout=900):
    """Raw HTTP so a 5xx body and the timing are both visible."""
    data = json.dumps(body).encode()
    s = socket.create_connection(("127.0.0.1", port), timeout=timeout)
    s.sendall(b"POST /v1/chat/completions HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\n"
              b"Content-Length: %d\r\n\r\n" % len(data) + data)
    t0 = time.monotonic()
    resp = b""
    while True:
        try:
            x = s.recv(65536)
        except OSError:
            break
        if not x:
            break
        resp += x
    s.close()
    return round(time.monotonic() - t0, 1), resp.split(b"\r\n", 1)[0].decode(errors="replace"), resp[-300:].decode(errors="replace")


def watch_cpu(pgid_fn, stop, tag):
    while not stop.is_set():
        pg = pgid_fn()
        if pg:
            out({"what": "cpu", "tag": tag, "pgid": pg, "pct": cpu_pct(pg, 5.0)})
        else:
            stop.wait(5)


def proxy_mode():
    d = os.environ.get("S22_FI_DIR", "/srv/s22/staging/phase0-20261022/fi")
    os.makedirs(d, exist_ok=True)
    fake = os.path.join(d, "fake-hung-llama.py")
    open(fake, "w").write(FAKE)
    mode = os.path.join(d, "mode")
    open(mode, "w").write("hang")
    state = os.path.join(d, "state")
    subprocess.run(["rm", "-rf", state])
    argv = json.dumps(["/usr/bin/python3", fake, "18091", mode])

    def start_proxy():
        p = subprocess.Popen(["/usr/bin/python3", PROXY_BIN, "serve", "--port", "18090", "--backend-port", "18091",
                              "--backend-argv", argv, "--state", state, "--first-s", "30", "--max-s", "60",
                              "--idle-s", "120"], stdin=subprocess.DEVNULL, stdout=open(d + "/proxy.out", "ab"),
                             stderr=subprocess.STDOUT, start_new_session=True)
        for _ in range(50):
            try:
                socket.create_connection(("127.0.0.1", 18090), 0.2).close()
                break
            except OSError:
                time.sleep(0.1)
        return p

    def backend_pg():
        try:
            return json.load(open(state + "/backend.json"))["pid"]
        except (OSError, ValueError, KeyError):
            return None

    proxy = start_proxy()
    out({"what": "proxy_started", "pid": proxy.pid})
    body = {"model": "m", "stream": True, "messages": [{"role": "user", "content": "fault injection %d" % time.time()}]}
    for i in (1, 2, 3):
        stop = threading.Event()
        th = threading.Thread(target=watch_cpu, args=(backend_pg, stop, "attempt%d" % i), daemon=True)
        th.start()
        el, status, tail = post(18090, body)
        stop.set()
        out({"what": "attempt", "n": i, "elapsed_s": el, "status": status, "tail": tail[-200:]})
        pg = backend_pg()
        time.sleep(4)
        out({"what": "after_attempt", "n": i, "backend_pgid": pg, "cpu_pct_4s_after": cpu_pct(pg, 4.0) if pg else None})
    # restart persistence: a stubborn backend mid-request, then the proxy dies (SIGKILL, exact pid)
    open(mode, "w").write("stubborn")
    body2 = {"model": "m", "stream": True, "messages": [{"role": "user", "content": "restart test %d" % time.time()}]}
    th = threading.Thread(target=lambda: out({"what": "inflight_result", "r": post(18090, body2)}), daemon=True)
    th.start()
    time.sleep(8)
    pg = backend_pg()
    out({"what": "before_proxy_kill", "backend_pgid": pg, "cpu_pct": cpu_pct(pg, 3.0) if pg else None,
         "active": json.load(open(state + "/active.json"))})
    os.kill(proxy.pid, signal.SIGKILL)
    proxy.wait()
    time.sleep(3)
    out({"what": "proxy_killed", "pid": proxy.pid, "orphan_alive": pg_alive(pg) if pg else None,
         "orphan_cpu_pct": cpu_pct(pg, 3.0) if pg else None})
    proxy = start_proxy()
    time.sleep(3)
    out({"what": "proxy_restarted", "pid": proxy.pid, "orphan_alive_after": pg_alive(pg) if pg else None})
    el, status, tail = post(18090, body2)
    out({"what": "retry_of_interrupted", "elapsed_s": el, "status": status, "tail": tail[-160:]})
    log = [json.loads(l) for l in open(state + "/ondemand.jsonl")]
    out({"what": "proxy_log", "events": [{k: r.get(k) for k in ("event", "reason", "after_s", "pct", "pid", "failures",
                                                               "interrupted", "reaped") if k in r}
                                          for r in log if r["event"] not in ("request_shape",)]})
    os.killpg(proxy.pid, signal.SIGTERM)
    proxy.wait(15)
    out({"what": "done", "leftover_fake": find_pid(fake)})


def real_mode():
    pid = find_pid("llama-server-b30266")
    out({"what": "before", "llama_pid": pid, "status": json.load(urllib.request.urlopen("http://127.0.0.1:8090/_s22/status", timeout=5))})
    big = ("Summarise this log, line by line, with every number: " +
           " ".join("line %d: sensor value %d at offset %d ok" % (i, i * 7 % 101, i * 13) for i in range(600)))
    body = {"model": "local", "stream": True, "max_tokens": 64,
            "messages": [{"role": "user", "content": big}]}
    out({"what": "request", "chars": len(big)})

    def llama_pg():
        p = find_pid("llama-server-b30266")
        if not p:
            return None
        try:
            return int(open("/proc/%d/stat" % p).read().rsplit(")", 1)[1].split()[2])
        except (OSError, ValueError):
            return None
    stop = threading.Event()
    threading.Thread(target=watch_cpu, args=(llama_pg, stop, "real"), daemon=True).start()
    el, status, tail = post(8090, body)
    out({"what": "result", "elapsed_s": el, "status": status, "tail": tail[-220:]})
    time.sleep(12)
    stop.set()
    pg = llama_pg()
    out({"what": "after", "llama_pgid": pg, "cpu_pct_12s_after": cpu_pct(pg, 5.0) if pg else None})
    el, status, tail = post(8090, {"model": "local", "stream": False, "max_tokens": 8,
                                   "messages": [{"role": "user", "content": "Reply with the single word OK."}]}, 300)
    out({"what": "short_request", "elapsed_s": el, "status": status, "tail": tail[-200:]})
    time.sleep(10)
    pg = llama_pg()
    out({"what": "idle_after_short", "cpu_pct": cpu_pct(pg, 5.0) if pg else None})
    log = [json.loads(l) for l in open("/srv/s22/state/llama-ondemand/ondemand.jsonl").read().splitlines()[-40:]]
    out({"what": "proxy_log", "events": [{k: r.get(k) for k in ("event", "reason", "after_s", "pct", "pid", "failures",
                                                               "ttft_s", "total_s") if k in r}
                                          for r in log if r["t"] > T0 and r["event"] not in ("request_shape",)]})


def turn_mode():
    cfgp = "/srv/s22/state/turnguard/config.json"
    old = open(cfgp).read() if os.path.exists(cfgp) else None
    cfg = json.loads(old) if old else {}
    import importlib.machinery, importlib.util
    ld = importlib.machinery.SourceFileLoader("tg", "/srv/s22/hardware/bin/s22-turnguard")
    sp = importlib.util.spec_from_loader("tg", ld)
    tg = importlib.util.module_from_spec(sp)
    ld.exec_module(tg)
    classes = [{"name": "faultinject", "match": "^faultinject-", "limit_s": 45}] + tg.CONFIG["classes"]
    cfg["classes"] = classes
    open(cfgp, "w").write(json.dumps(cfg))
    sid = "faultinject-%d" % time.time()
    try:
        r = urllib.request.urlopen(urllib.request.Request(
            "http://127.0.0.1:18880/api/chat", data=json.dumps({"sessionId": sid, "message":
                "Use your shell tool to run exactly this command and report its output: sleep 150; echo finished"
            }).encode(), headers={"Content-Type": "application/json"}), timeout=60)
        out({"what": "posted", "session": sid, "answer": r.read()[:200].decode(errors="replace")})
        t0 = time.time()
        seen_sleep = None
        while time.time() - t0 < 200:
            d = json.load(urllib.request.urlopen("http://127.0.0.1:18880/api/chat/diagnostics", timeout=10))
            mine = [p for p in d.get("pending", []) if p.get("sessionId") == sid]
            sp_pid = find_pid("sleep 150")
            seen_sleep = seen_sleep or sp_pid
            out({"what": "poll", "age_s": round(time.time() - t0, 1), "pending": bool(mine), "sleep_pid": sp_pid})
            if not mine and time.time() - t0 > 5:
                break
            time.sleep(5)
        time.sleep(3)
        ev = [json.loads(l) for l in open("/srv/s22/state/turnguard/turnguard.jsonl") if sid in l]
        out({"what": "turnguard_events", "events": ev,
             "sleep_pid_seen": seen_sleep, "sleep_still_running": find_pid("sleep 150")})
    finally:
        if old is None:
            os.unlink(cfgp)
        else:
            open(cfgp, "w").write(old)
        out({"what": "config_restored"})


if __name__ == "__main__":
    OUT = sys.argv[2]
    T0 = time.time()
    {"proxy": proxy_mode, "real": real_mode, "turn": turn_mode}[sys.argv[1]]()
