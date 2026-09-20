#!/usr/bin/env bash
# Restore/start the verified RAM-only CPU API server; no partition/image writes.
set -euo pipefail
project=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
cd "$project"
archive=tools/model-bench/server-runtime.tar.gz
expected=21426e1eb355c66ee01ee36161072dff4702f5e4fe363f38188876ff0c29fa6b
[[ $(sha256sum "$archive" | cut -d ' ' -f 1) == "$expected" ]] || {
    echo 'Server archive mismatch; refusing transfer.' >&2; exit 1;
}
scp_options=(-o StrictHostKeyChecking=yes
    -o UserKnownHostsFile="$project/evidence/native-linux-20260919/native-v2-known-hosts"
    -o ConnectTimeout=5)
tools/s22-ssh 'set -eu
    test "$(cat /proc/1/comm)" = native-guardian
    awk '\''$2 == "/mnt/model-bench" && $3 == "tmpfs" {ok=1} END {exit !ok}'\'' /proc/mounts
    test -f /mnt/model-bench/models/Qwen3.5-2B-Q4_0.gguf
    if pgrep -f "^/mnt/model-bench/server/libc/ld-linux-aarch64.so.1" >/dev/null; then
        echo "Server already running; refusing to overwrite its runtime." >&2; exit 1
    fi
    python3 -c '\''import socket; s=socket.socket(); s.bind(("127.0.0.1",8089)); s.close()'\''
    mkdir -p /mnt/model-bench/server'
scp "${scp_options[@]}" "$archive" root@10.55.0.2:/mnt/model-bench/server-runtime.tar.gz
tools/s22-ssh 'set -eu
    test "$(sha256sum /mnt/model-bench/server-runtime.tar.gz | cut -d " " -f 1)" = 21426e1eb355c66ee01ee36161072dff4702f5e4fe363f38188876ff0c29fa6b
    tar -xzf /mnt/model-bench/server-runtime.tar.gz -C /mnt/model-bench/server
    rm /mnt/model-bench/server-runtime.tar.gz
    nohup nice -n 20 /mnt/model-bench/server/libc/ld-linux-aarch64.so.1 \
      --library-path /mnt/model-bench/server/libc:/mnt/model-bench/server/lib \
      /mnt/model-bench/server/bin/llama-server \
      -m /mnt/model-bench/models/Qwen3.5-2B-Q4_0.gguf \
      -t 4 -C f0 --cpu-strict 1 -c 4096 -np 1 -ngl 0 \
      --host 127.0.0.1 --port 8089 --no-webui \
      </dev/null >/mnt/model-bench/server.log 2>&1 &
    echo $! >/mnt/model-bench/server.pid'
tools/s22-ssh 'python3 -' <<'PY'
import json
import pathlib
import time
import urllib.request

pid = int(pathlib.Path('/mnt/model-bench/server.pid').read_text())
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
for _ in range(40):
    if not pathlib.Path(f'/proc/{pid}').exists():
        raise SystemExit('Server exited; inspect /mnt/model-bench/server.log')
    try:
        with opener.open('http://127.0.0.1:8089/health', timeout=1) as response:
            if json.load(response).get('status') == 'ok':
                print(f'CPU model server ready, pid={pid}, loopback port 8089.')
                break
    except (OSError, ValueError):
        pass
    time.sleep(.25)
else:
    raise SystemExit('Server readiness deadline expired; inspect /mnt/model-bench/server.log')
PY
