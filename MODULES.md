# r0s Recovery/Vendor Kernel Module Load Order

Source: `android_device_samsung_s5e9925-common/configs/kernel/modules/r0s.load`
(LineageOS lineage-23.2, commit 7be96871), copied to `lineage/r0s_module_load_order.txt`.

This is the list `BOARD_VENDOR_RAMDISK_KERNEL_MODULES_LOAD`, which LineageOS's
BoardConfigCommon.mk explicitly reuses for the RECOVERY ramdisk too:
```
BOARD_RECOVERY_RAMDISK_KERNEL_MODULES_LOAD := $(BOARD_VENDOR_RAMDISK_KERNEL_MODULES_LOAD)
RECOVERY_KERNEL_MODULES := $(BOARD_RECOVERY_RAMDISK_KERNEL_MODULES_LOAD)
```

324 modules total, loaded in the exact file order. This is almost certainly load-order
sensitive (regulators and IOMMU groups before consumers; PHY before controller; USB
controller before gadget function drivers).

## Subsystem-relevant excerpts (full list in lineage/r0s_module_load_order.txt)

**Power/regulator (must load early):**
- s2mps25-regulator.ko, s2mps26-regulator.ko, s2mpb02-regulator.ko, s2mpm07_regulator.ko

**IOMMU (must load before most bus-mastering peripherals):**
- samsung_iommu.ko, samsung-iommu-group.ko, exynos-pcie-iommu.ko, exynos-cpif-iommu.ko

**USB (this is the PROVEN blocker hypothesis for the previous failed native boot):**
- phy-exynos-usbdrd-super.ko  (USB PHY — must precede controller)
- dwc3-exynos-usb.ko          (DWC3 controller wrapper)
- usb_typec_manager.ko, usb_repeater_module.ko, repeater_tusb2e11_module.ko
- usb_notify_layer.ko, usb_notifier.ko
- usb_f_dm.ko, usb_f_dm1.ko, usb_f_conn_gadget.ko, usb_f_ss_mon_gadget.ko (gadget functions)
- usbserial.ko, usblp.ko
- exynos-usb-audio-offloading.ko

**Display:**
- phy-exynos-mipi-dsim.ko
- mcd-panel-samsung-drv.ko, mcd-panel-samsung-helper.ko, mcd-panel.ko
- mcd-panel-s6e3fac_rainbow_r0.ko (panel-specific — r0 in the filename is this exact panel)

**Touch:**
- sec_ts.ko, hid-keytouch.ko

## Conclusion for Phase 6
The previous native-Linux recovery attempt used a static Alpine/BusyBox `/init` that
tried to configure a USB ACM gadget directly via ConfigFS, with NO evidence of loading
`phy-exynos-usbdrd-super.ko` or `dwc3-exynos-usb.ko` first (these are modular, not
built into the stock kernel per this same config approach). Without the PHY and DWC3
controller modules loaded, ConfigFS gadget creation would either fail outright or
create a gadget with no working UDC bound to it — explaining "no USB device enumerated
during the failed native recovery boot."

Native Recovery V2 (Phase 10) MUST insmod at minimum, in order:
1. regulator modules (s2mps25/26, s2mpb02, s2mpm07)
2. IOMMU modules (samsung_iommu, samsung-iommu-group)
3. phy-exynos-usbdrd-super.ko
4. dwc3-exynos-usb.ko
5. usb_notify_layer.ko, usb_notifier.ko (if the gadget stack depends on them — verify
   with `modinfo`/`depmod` once kernel build artifacts are available)
6. THEN configure ConfigFS ACM gadget

This must be verified against actual `.ko` dependency graphs (`modinfo`) once we have
the FYI3 stock kernel modules or build our own — do not assume this order is complete,
treat it as EXPECTED not PROVEN until tested.
