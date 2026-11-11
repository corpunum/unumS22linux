#!/bin/sh
# Rebuild the S22's running vendor kernel reproducibly, then the same kernel plus extra patches,
# and package the patched Image into a RECOVERY candidate. Host only: never talks to the phone.
#
#   build-recovery-kernel.sh --src <kernel git tree> --rev <commit> --config <running .config>
#       --version-file <saved /proc/version> --expect-build-id <hex> --out <new dir under builds/>
#       --base-patch <p> [--base-patch <p> ...] --patch <p> [--patch <p> ...]
#       [--base-image <recovery.img> --base-sha256 <hex>] [--jobs N]
#
# 1. Detached worktree of --src at --rev; apply every --base-patch (what the running kernel has).
# 2. Build Image with the running config and the exact KBUILD identity from /proc/version (user, host,
#    timestamp, #version), then compare the GNU build-id with --expect-build-id. A mismatch stops the run
#    before anything is patched: the rebuild is not provably the running kernel.
# 3. Apply every --patch, rebuild, and record the new Image, build-id and vmlinux.symvers CRC diff (a
#    patch that changes exported-symbol CRCs would break the ramdisk's modules: that also stops the run).
# 4. With --base-image: package the patched Image with build-bt-hci-recovery.py (only the kernel payload
#    may change; AVB footer verified).
# Run it under the rig's heavy-job lock, e.g. `flock ~/.cache/rig-heavy.lock nice -n 10 sh ...`.
set -eu

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
SRC= REV= CONFIG= VERSION_FILE= EXPECT= OUT= BASE_IMAGE= BASE_SHA= EXPECT_IMAGE= JOBS=$(( $(nproc) - 4 ))
AVBTOOL=${AVBTOOL:-$HERE/../avb/avbtool.py}
MKBOOTIMG=${MKBOOTIMG:-$HERE/../mkbootimg/mkbootimg.py}
UNPACK_BOOTIMG=${UNPACK_BOOTIMG:-$HERE/../mkbootimg/unpack_bootimg.py}
BASE_PATCHES= PATCHES=
while [ $# -gt 0 ]; do
  case "$1" in
    --src) SRC=$2; shift 2;; --rev) REV=$2; shift 2;; --config) CONFIG=$2; shift 2;;
    --version-file) VERSION_FILE=$2; shift 2;; --expect-build-id) EXPECT=$2; shift 2;;
    --out) OUT=$2; shift 2;; --base-patch) BASE_PATCHES="$BASE_PATCHES $2"; shift 2;;
    --patch) PATCHES="$PATCHES $2"; shift 2;; --base-image) BASE_IMAGE=$2; shift 2;;
    --base-sha256) BASE_SHA=$2; shift 2;; --jobs) JOBS=$2; shift 2;;
    --expect-image) EXPECT_IMAGE=$2; shift 2;;
    *) echo "unknown argument $1" >&2; exit 2;;
  esac
done
for v in SRC REV CONFIG VERSION_FILE EXPECT OUT; do eval "[ -n \"\$$v\" ]" || { echo "--$(echo $v | tr A-Z_ a-z-) is required" >&2; exit 2; }; done
[ "$JOBS" -ge 1 ] || JOBS=1
[ -e "$OUT" ] && { echo "refusing existing output $OUT" >&2; exit 1; }
mkdir -p "$OUT"; OUT=$(cd "$OUT" && pwd)
LOG=$OUT/build.log; RESULT=$OUT/result.json
exec >>"$LOG" 2>&1
echo "== $(date -Is) build-recovery-kernel start (jobs $JOBS)"

# KBUILD identity from the saved /proc/version:
# "Linux version 5.10.260-g4e5c5ad7d950 (user@host) (clang ...) #1 SMP PREEMPT Thu Sep 24 09:09:12 EEST 2026"
V=$(head -1 "$VERSION_FILE")
KUSER=$(echo "$V" | sed -n 's/^Linux version [^ ]* (\([^@]*\)@\([^)]*\)).*/\1/p')
KHOST=$(echo "$V" | sed -n 's/^Linux version [^ ]* (\([^@]*\)@\([^)]*\)).*/\2/p')
KVER=$(echo "$V" | sed -n 's/.*) #\([0-9]*\) .*/\1/p')
KTIME=$(echo "$V" | sed -n 's/.* PREEMPT \(.*\)$/\1/p')
[ -n "$KUSER" ] && [ -n "$KHOST" ] && [ -n "$KVER" ] && [ -n "$KTIME" ] || { echo "cannot parse $VERSION_FILE"; exit 1; }
export KBUILD_BUILD_USER="$KUSER" KBUILD_BUILD_HOST="$KHOST" KBUILD_BUILD_VERSION="$KVER" KBUILD_BUILD_TIMESTAMP="$KTIME"
echo "identity: $KUSER@$KHOST #$KVER $KTIME"

WT=$OUT/src
git -C "$SRC" worktree add --detach "$WT" "$REV"
for p in $BASE_PATCHES; do git -C "$WT" apply -p1 "$p"; echo "base patch: $p"; done
# Toolchain flags of the running build (found 2026-11-12, see FLASH_LOG.md): `ARCH=arm64 LLVM=1` with
# LLVM_IAS UNSET. This tree uses the integrated assembler unless LLVM_IAS=0, so the running config has
# AS_IS_LLVM=y and RELR, but no HAS_LTO_CLANG, which needs LLVM_IAS=1 literally. GNU binutils would drop RELR.
# LOCALVERSION="" stops setlocalversion from appending "+" (CONFIG_LOCALVERSION carries the suffix).
KMAKE_FLAGS=${KMAKE_FLAGS:-ARCH=arm64 LLVM=1}
export LOCALVERSION=
MK="make -C $WT $KMAKE_FLAGS -j$JOBS"
echo "make flags: $KMAKE_FLAGS"

build_id() { readelf -n "$1" | sed -n 's/.*Build ID: \([0-9a-f]*\).*/\1/p' | head -1; }

build() {   # $1 = O dir
  mkdir -p "$1"; cp "$CONFIG" "$1/.config"
  $MK O="$1" olddefconfig
  if ! cmp -s "$CONFIG" "$1/.config"; then
    diff "$CONFIG" "$1/.config" > "$1/config.diff" || true
    echo "note: olddefconfig changed $(grep -c '^[<>]' "$1/config.diff") config lines (see $1/config.diff)"
  fi
  $MK O="$1" Image
}

T0=$(date +%s)
build "$OUT/o-base"
BASE_ID=$(build_id "$OUT/o-base/vmlinux")
echo "baseline build-id $BASE_ID (expect $EXPECT), $(( $(date +%s) - T0 )) s"
# Second, path-independent proof: lld's build-id also hashes DWARF (build directory paths), so a rebuild
# in another directory gets a new build-id even when the code is identical. With --expect-image (the
# Image unpacked from the live RECOVERY), compare the Images byte for byte with the 20-byte build-id masked.
MASKED=skipped
if [ -n "$EXPECT_IMAGE" ]; then
  MASKED=$(python3 -I - "$OUT/o-base/arch/arm64/boot/Image" "$EXPECT_IMAGE" "$BASE_ID" "$EXPECT" <<'PY'
import sys
a, b = open(sys.argv[1], 'rb').read(), open(sys.argv[2], 'rb').read()
ida, idb = bytes.fromhex(sys.argv[3]), bytes.fromhex(sys.argv[4])
if len(a) != len(b):
    print('size-differs'); sys.exit()
ia, ib = a.find(ida), b.find(idb)
if ia < 0 or ia != ib:
    print('build-id-offset-differs'); sys.exit()
diff = sum(1 for x, y in zip(a[:ia] + a[ia + 20:], b[:ib] + b[ib + 20:]) if x != y)
print('identical' if diff == 0 else 'differs:%d' % diff)
PY
)
fi
echo "baseline Image vs running Image (build-id masked): $MASKED"
if [ "$BASE_ID" = "$EXPECT" ]; then PROOF=build-id
elif [ "$MASKED" = identical ]; then PROOF=masked-image
else
  printf '{"ok": false, "stage": "baseline", "build_id": "%s", "expected": "%s", "masked_image": "%s"}\n' "$BASE_ID" "$EXPECT" "$MASKED" > "$RESULT"
  echo "STOP: the rebuild is not provably the running kernel"; exit 3
fi
echo "baseline proof: $PROOF"

for p in $PATCHES; do git -C "$WT" apply -p1 "$p"; echo "patch: $p"; done
cp -a "$OUT/o-base" "$OUT/o-patched"        # incremental: only the patched files rebuild
$MK O="$OUT/o-patched" Image
NEW_ID=$(build_id "$OUT/o-patched/vmlinux")
CRC_DIFF=$(diff "$OUT/o-base/vmlinux.symvers" "$OUT/o-patched/vmlinux.symvers" 2>/dev/null | grep -c '^[<>]' || true)
IMG=$OUT/o-patched/arch/arm64/boot/Image
echo "patched build-id $NEW_ID, exported-symbol CRC lines changed: $CRC_DIFF"
if [ "${CRC_DIFF:-0}" != 0 ]; then
  printf '{"ok": false, "stage": "symvers", "crc_lines_changed": %s}\n' "$CRC_DIFF" > "$RESULT"
  echo "STOP: exported-symbol CRCs changed; the ramdisk modules would not load"; exit 4
fi

PACKED=null
if [ -n "$BASE_IMAGE" ]; then
  CAND=$(cd "$HERE/../.." && pwd)/builds/$(basename "$OUT")-candidate   # the packer writes under its repo's builds/
  python3 -I "$HERE/build-bt-hci-recovery.py" --kernel "$IMG" --out-dir "$CAND" \
    --base-image "$BASE_IMAGE" --base-sha256 "$BASE_SHA" \
    --avbtool "$AVBTOOL" --mkbootimg "$MKBOOTIMG" --unpack-bootimg "$UNPACK_BOOTIMG"
  cp -p "$CAND/recovery.img" "$OUT/recovery-candidate.img"
  PACKED="\"$(sha256sum "$OUT/recovery-candidate.img" | cut -c1-64)\""
fi
printf '{"ok": true, "proof": "%s", "baseline_build_id": "%s", "patched_build_id": "%s", "image_sha256": "%s", "candidate_sha256": %s, "seconds": %s}\n' \
  "$PROOF" "$BASE_ID" "$NEW_ID" "$(sha256sum "$IMG" | cut -c1-64)" "$PACKED" "$(( $(date +%s) - T0 ))" > "$RESULT"
echo "== $(date -Is) done: $(cat "$RESULT")"
