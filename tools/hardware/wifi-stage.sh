#!/usr/bin/env bash
# Prepare (never activate) the native S22 QCA6490 Wi-Fi payload.
set -euo pipefail
repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
out=${WIFI_STAGE_DIR:-/tmp/s22-wifi-stage}
lineage="$repo/lineage/build-20260915/unpacked/ramdisk_extracted"
manifest="$repo/lineage/android_device_samsung_r0s/proprietary-files.txt"
recovered="$repo/rootfs/wifi-vendor-assets"
die() { echo "wifi-stage: $*" >&2; exit 1; }
need_file() { [[ -f "$1" ]] || die "missing $1"; }
case "${1:-inventory}" in
  inventory)
    need_file "$manifest"
    echo "stage=$out"
    echo "firmware_root=/vendor/firmware (kernel firmware_class.path; do not change)"
    echo "module_source=$lineage/lib/modules"
    echo "required_firmware:"
    awk '/^vendor\/firmware\/qca6490\// || /^vendor\/firmware\/(wlan-connection|wlan\/qcom_cfg)/ {print "  " $1}' "$manifest"
    echo "cnss closure: cnss2 -> wlan_firmware_service, cnss_plat_ipc_qmi_svc, cnss_utils, qmi_helpers, mhi, pcie-exynos-rc"
    wlan_src=${WIFI_WLAN_MODULE:-$recovered/lineage-23.2-20260915/vendor_dlkm/lib/modules/wlan.ko}
    [[ -f "$wlan_src" ]] && echo "wlan.ko=present (ABI/hash validation still required)" || echo "wlan.ko=missing"
    echo "WARNING: the first activation panicked; staging is NOT an approved activation recipe. Read docs/WIFI_NATIVE.md."
    ;;
  stage-modules)
    for m in cnss2 cnss_nl cnss_prealloc cnss_plat_ipc_qmi_svc cnss_utils wlan_firmware_service; do
      need_file "$lineage/lib/modules/$m.ko"
    done
    wlan_src="${WIFI_WLAN_MODULE:-}"
    [[ -n "${wlan_src:-}" && -f "$wlan_src" ]] || wlan_src="$recovered/lineage-23.2-20260915/vendor_dlkm/lib/modules/wlan.ko"
    [[ -f "$wlan_src" ]] || { echo "wifi-stage: exact wlan.ko is absent" >&2; exit 2; }
    kernel_release="${WIFI_KERNEL_RELEASE:-5.10.260-g4e5c5ad7d950}"
    vermagic=$(modinfo -F vermagic "$wlan_src" 2>/dev/null)
    [[ "${vermagic%% *}" == "$kernel_release" ]] || { echo "wifi-stage: wlan.ko vermagic does not match target kernel; refusing stage" >&2; exit 2; }
    mkdir -p "$out/modules"
    for m in cnss2 cnss_nl cnss_prealloc cnss_plat_ipc_qmi_svc cnss_utils wlan_firmware_service; do
      install -m 0644 "$lineage/lib/modules/$m.ko" "$out/modules/$m.ko"
    done
    install -m 0644 "$wlan_src" "$out/modules/wlan.ko"
    (cd "$out/modules" && sha256sum ./*.ko | sort) > "$out/modules.sha256"
    echo "staged modules in $out/modules; no phone changes made"
    ;;
  stage-firmware)
    need_file "$manifest"
    mkdir -p "$out/firmware/qca6490" "$out/firmware"
    mapfile -t req < <(awk '/^vendor\/firmware\/qca6490\// || /^vendor\/firmware\/(wlan-connection|wlan\/qcom_cfg)/ {print $1}' "$manifest")
    missing=0
    for rel in "${req[@]}"; do
      src="$lineage/$rel"; [[ -f "$src" ]] || src="$recovered/$rel"
      [[ -f "$src" ]] || { echo "missing recovered artifact: $rel" >&2; missing=1; }
    done
    if (( missing )); then echo "wifi-stage: recovered QCA firmware closure is incomplete; no phone changes made" >&2; exit 2; fi
    for rel in "${req[@]}"; do
      src="$lineage/$rel"; [[ -f "$src" ]] || src="$recovered/$rel"
      dst="$out/firmware/${rel#vendor/firmware/}"; mkdir -p "$(dirname "$dst")"; install -m 0644 "$src" "$dst"
    done
    (cd "$out/firmware" && find . -type f -print0 | sort -z | xargs -0 sha256sum) > "$out/firmware.sha256"
    echo "staged firmware in $out/firmware; no phone changes made"
    ;;
  *) die "usage: $0 inventory|stage-modules|stage-firmware" ;;
esac
