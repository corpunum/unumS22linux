# Device Record — Samsung Galaxy S22 (r0s)

## Identity
- Model: SM-S901B/DS (Europe, Dual SIM)
- Codename: r0s (SoC platform: s5e9925 / Exynos 2200)
- Serial (adb): &lt;redacted&gt;
- Released: February 25, 2022

## Hardware (from LineageOS wiki, verify against actual bring-up later)
- SoC: Exynos 2200
- CPU: 1x Cortex-X2 @2.80GHz + 3x Cortex-A710 @2.52GHz + 4x Cortex-A510 @1.82GHz
- GPU: Samsung Xclipse 920 (AMD RDNA2-based)
- RAM: 8GB, Storage: 128/256GB (UFS)
- Display: 6.1in 2340x1080 Dynamic AMOLED
- Modem/BT/WiFi: Exynos modem, BT 5.2, WiFi ax
- Battery: non-removable 3700mAh

## Verified software state — 2026-09-18
```
ro.product.model            = SM-S901B
ro.product.device           = r0s
ro.bootloader                = S901BXXSIFYI3
ro.boot.flash.locked         = 0
ro.boot.verifiedbootstate    = orange
ro.boot.vbmeta.device_state  = unlocked
ro.boot.warranty_bit         = 1
sys.oem_unlock_allowed       = 1
ro.build.version.release     = 15
ro.build.display.id          = AP3A.240905.015.A2.S901BXXSIFYI3
ro.build.fingerprint          = samsung/r0sxeea/r0s:15/AP3A.240905.015.A2/S901BXXSIFYI3:user/release-keys
kernel                        = 5.10.223-android12-9-30958166-abS901BXXSIFYI3
```

Bootloader unlock status: PROVEN unlocked (all three properties consistent).

## Special boot modes
- Recovery: power on holding Volume Up + Power while USB connected
- Download/Bootloader mode: power off, hold Volume Up + Volume Down, connect USB

## DO NOT TOUCH
PIT, EFS/sec_efs, IMEI/radio calibration, BL/SBL, TrustZone/security partitions,
bootloader partitions. Never relock bootloader while non-stock images may exist.
