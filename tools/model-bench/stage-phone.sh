#!/usr/bin/env bash
# Restore the verified 2B CPU runtime into RAM without partition-image writes.
set -euo pipefail
project=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
cd "$project"
model=rootfs/model-bench/models/Qwen3.5-2B-Q4_0.gguf
expected=cd70221bebaee0503e0f6717e174250cd7825aa88438b3aabec9ad55731d9bb1
[[ $(sha256sum "$model" | cut -d ' ' -f 1) == "$expected" ]] || {
    echo 'Host model hash mismatch; refusing transfer.' >&2; exit 1;
}
ssh_phone=tools/s22-ssh
scp_options=(-o StrictHostKeyChecking=yes
    -o UserKnownHostsFile="$project/evidence/native-linux-20260919/native-v2-known-hosts"
    -o ConnectTimeout=5)
"$ssh_phone" 'set -eu
    test "$(cat /proc/1/comm)" = native-guardian
    if pgrep -f "^/mnt/model-bench/(server/)?libc/ld-linux-aarch64.so.1" >/dev/null; then
        echo "A staged model is running; stop it before restoring runtime files." >&2; exit 1
    fi
    if ! mountpoint -q /mnt/model-bench; then
        test "$(awk '\''/^MemAvailable:/ {print $2}'\'' /proc/meminfo)" -ge 3500000
        mkdir -p /mnt/model-bench
        test -z "$(ls -A /mnt/model-bench)"
        mount -t tmpfs -o size=3G,mode=700 s22-model-bench /mnt/model-bench
    fi
    awk '\''$2 == "/mnt/model-bench" && $3 == "tmpfs" {ok=1} END {exit !ok}'\'' /proc/mounts
    mkdir -p /mnt/model-bench/optimized /mnt/model-bench/models'
scp "${scp_options[@]}" -r rootfs/model-bench/libc root@10.55.0.2:/mnt/model-bench/
scp "${scp_options[@]}" -r rootfs/model-bench-optimized/bin rootfs/model-bench-optimized/lib \
    root@10.55.0.2:/mnt/model-bench/optimized/
remote_hash=$("$ssh_phone" 'sha256sum /mnt/model-bench/models/Qwen3.5-2B-Q4_0.gguf 2>/dev/null' | cut -d ' ' -f 1 || true)
if [[ "$remote_hash" != "$expected" ]]; then
    scp "${scp_options[@]}" "$model" root@10.55.0.2:/mnt/model-bench/models/Qwen3.5-2B-Q4_0.gguf.part
    "$ssh_phone" 'set -eu
        test "$(sha256sum /mnt/model-bench/models/Qwen3.5-2B-Q4_0.gguf.part | cut -d " " -f 1)" = cd70221bebaee0503e0f6717e174250cd7825aa88438b3aabec9ad55731d9bb1
        mv /mnt/model-bench/models/Qwen3.5-2B-Q4_0.gguf.part /mnt/model-bench/models/Qwen3.5-2B-Q4_0.gguf'
fi
scp "${scp_options[@]}" tools/linux-rootfs/s22-chat.py root@10.55.0.2:/tmp/s22-chat.next
"$ssh_phone" 'chmod 755 /tmp/s22-chat.next && mv /tmp/s22-chat.next /usr/local/bin/s22-chat'
printf '%s\n' 'Verified 2B CPU chat is ready. Phone command: s22-chat. Weights remain RAM-only.'
