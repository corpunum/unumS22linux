#!/bin/sh
# stage-nnapi-sl.sh VENDOR_MNT SYSTEM_LIB64 OUTDIR
# Host side: collect Samsung's NNAPI support library (neuralnetworks_npu_sl_driver_prebuilt.so)
# and its dependency closure for the isolated Bionic runtime /srv/s22/enn-rt.
#   VENDOR_MNT   = read-only loop mount of the stock vendor image (rootfs/vendor-pristine-*.img)
#   SYSTEM_LIB64 = stock recovery ramdisk system/lib64 (stock/FYI3/extracted/unpacked_recovery/...)
# OUTDIR gets vendor/lib64 + system/lib64; on the phone copy only names enn-rt does not
# have yet (the existing enn-rt libs stay). Extra pieces not in either source:
#   android.hidl.safe_union@1.0.so (rootfs/cellular-loader-*/system/lib64),
#   libnativewindow.so (build nativewindow-stub.c: AHardwareBuffer_* fail cleanly),
#   /data/nn/lib/libEGL.so + libGLESv2.so stubs for TFLite benchmark_model (stub-*.c).
# Vendor binaries stay private (never git).
set -eu
V=$1; S=$2; O=$3
mkdir -p "$O/vendor/lib64" "$O/system/lib64"
todo="neuralnetworks_npu_sl_driver_prebuilt.so"; seen=" "
while [ -n "$todo" ]; do
  set -- $todo; n=$1; shift; todo="$*"
  case "$seen" in *" $n "*) continue;; esac; seen="$seen$n "
  if [ -e "$V/lib64/$n" ]; then f="$V/lib64/$n"; cp "$f" "$O/vendor/lib64/"
  elif [ -e "$S/$n" ]; then f="$S/$n"; cp "$f" "$O/system/lib64/"
  else echo "missing: $n" >&2; continue; fi
  todo="$todo $(readelf -d "$f" | sed -n 's/.*NEEDED.*\[\(.*\)\]/\1/p' | tr '\n' ' ')"
done
echo "staged $(ls "$O/vendor/lib64" | wc -l) vendor + $(ls "$O/system/lib64" | wc -l) system libs in $O"
