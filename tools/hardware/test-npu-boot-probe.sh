#!/bin/sh
set -eu
cd "$(dirname "$0")/../.."
out="$(mktemp)"
trap 'rm -f "$out"' EXIT
cc -std=c11 -Wall -Wextra -Werror tools/hardware/npu-boot-probe.c -o "$out"
dry="$($out --dry-run)"
printf '%s\n' "$dry" | grep -F 'abi=vs4l_ctrl size=24' >/dev/null
printf '%s\n' "$dry" | grep -F 'ctrl=0x0 value=0x2 mem=0' >/dev/null
printf '%s\n' "$dry" | grep -F 'ioctl=0xc018560d' >/dev/null
if "$out" --bad-mode >/dev/null 2>&1; then exit 1; fi
if "$out" --execute >/dev/null 2>&1; then exit 1; fi
echo 'npu-boot-probe host ABI tests: PASS (3 assertions + refusal checks)'
