#!/bin/sh
set -eu
src=$(dirname "$0")/bt-qca6490-board-probe.c
out=$(mktemp)
trap 'rm -f "$out"' EXIT HUP INT TERM
cc -std=c11 -Wall -Wextra -Werror -O2 "$src" -o "$out"
"$out" --self-test
