#!/bin/sh
set -eu

src=$(dirname "$0")/bt-version-transport-probe.c
out=$(mktemp "${TMPDIR:-/tmp}/bt-version-transport.XXXXXX")
trap 'rm -f "$out"' EXIT HUP INT TERM

cc -std=c11 -Wall -Wextra -Werror -O2 "$src" -o "$out"
"$out" --self-test

# Device access is opt-in and must be rejected without both explicit gates.
if "$out" --uart /dev/null --btpower /dev/null >/dev/null 2>&1; then
	printf '%s\n' 'unexpected device execution' >&2
	exit 1
fi
# Even with both gates, a non-preflight path/type is rejected before open.
if "$out" --execute --allow-shared-wlan-rail --uart /dev/null \
	--btpower /dev/null --btpower-rdev 1:3 >/dev/null 2>&1; then
	printf '%s\n' 'unexpected non-device identity acceptance' >&2
	exit 1
fi
printf '%s\n' 'bt-version-transport host test: PASS'
