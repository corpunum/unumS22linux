# Phone portability: evidence and limits

Research review: **2026-09-21**. The candidate matrix lives directly in the
[README](../README.md#other-phones-native-linux-and-omarchy-feasibility).
It is a feasibility survey, not an installer release or a promise that all
listed phones work. No additional handset was tested during this review.

## What transfers from this project

Reusable ideas include boot-mode evidence, a recoverable native-userspace
handoff, USB rescue, persistent ARM64 userspace, bounded display/compute tests,
and separate acceptance of boot, touch, networking and inference. Our concrete
S22 achievement is documented in [the project log](../EXPERIMENTS.md),
[Wi-Fi acceptance](WIFI_AUTOSTART.md), and
[shader diagnostics](GPU_SHADER_DIAGNOSTICS_2026-09-21.md).

The binary kernel, DTB/DTBO, module/firmware ABI, boot-image header, partition
names, display-stride patch and Samsung reboot conventions are **not portable
artifacts**. A shared CPU architecture or SoC does not establish compatibility.
Other devices may use A/B boot slots, boot-as-recovery, different verified-boot
rules, or a different recovery path altogether.

For a phone with an existing native postmarketOS/Mobian port, start with that
port's kernel, firmware and installer. Then evaluate Arch Linux ARM/Hyprland
userspace separately. Do not introduce our vendor-kernel handoff merely for
consistency. A postmarketOS package/image is not automatically an Arch package
or Omarchy installer.

## Source freshness and confidence

- **Measured here:** only S22 `r0s`, with explicit component limitations.
- **Upstream native port:** a phone-specific Linux port exists in the linked
  project. This is stronger evidence than Android rooting or LineageOS support,
  but is not this project's independent hardware acceptance.
- **Experimental/downstream entry:** a development lead, possibly with broken
  essentials. Inclusion is not a recommendation to buy or flash it.
- **Hypothesis:** related hardware/source targets only; no demonstrated port
  from this project.

The [postmarketOS v26.06 release](https://postmarketos.org/blog/2026/06/21/v26.06-release/)
was readable live. It lists several relevant community devices and warns that
the release is aimed at enthusiasts, not Android/iOS-level polish. Its
categories describe a release snapshot, not guaranteed current component
functionality. For example, its short name “Galaxy S9” must not be generalized
to every Snapdragon/Exynos/carrier S9.

Direct postmarketOS wiki requests encountered an anti-bot page. The device
feature matrices were therefore read from indexed primary-source snapshots,
many crawled around May/June 2025. The review date is **not** a claim that every
feature matrix was freshly fetched in September 2026. `Y/P/N/?` reproduces the
reviewed evidence's meaning; newer fixes and regressions may supersede it.
Unestablished fields, especially per-variant Mi 8 and SHIFT6mq details, are
left unknown rather than inferred from a shared chipset.

Primary sources supporting the scope and interpretation:

- [SDM845 generic native port](https://wiki.postmarketos.org/wiki/Qualcomm_SDM845_%28qualcomm-sdm845%29)
  identifies OnePlus 6/6T, SHIFT6mq and POCO F1, with device-specific boot and
  installation choices. Individual device pages are linked in the README.
- [PINE64 PinePhone documentation](https://pine64.org/documentation/PinePhone/)
  and [PinePhone Pro documentation](https://pine64.org/documentation/PinePhone_Pro/_full/)
  establish Linux-first hardware, but also document platform limitations.
- [Purism Librem 5](https://puri.sm/products/librem-5/)
  establishes the native PureOS route; it is not an Omarchy certification.
- [Mesa Freedreno/Turnip](https://docs.mesa3d.org/drivers/freedreno.html),
  [Panfrost](https://docs.mesa3d.org/drivers/panfrost.html) and
  [Lima](https://docs.mesa3d.org/drivers/lima.html) distinguish GPU families and
  graphics APIs. Turnip does not support Adreno 5xx; Mali-T860's Panfrost entry
  does not advertise Vulkan. The presence of a GPU is not an inference result.
- Omarchy's official [M announcement, September 11](https://omarchy.org/news/2026/09/introducing-omarchy-m/)
  and [Dragon announcement, September 18](https://omarchy.org/news/2026/09/introducing-omarchy-dragon/)
  establish ARM work for Apple/Snapdragon computers. It would now be wrong to
  call Omarchy categorically x86-only; it would also be wrong to treat those
  announcements as smartphone installation support.
- The [Ubuntu Touch device catalog](https://devices.ubuntu-touch.io/) explicitly
  distinguishes Native, Halium and Legacy. A listing there is useful for that
  distribution, but is not sufficient evidence for this native Arch route.

## Closest S22 relatives

The local kernel source checkout is
`lineage/android_kernel_samsung_s5e9925`, commit
`4e5c5ad7d950e4de0688b5663965f2075654b2ad`. Its configuration headers identify:

| Target | Local source path | What that establishes |
| --- | --- | --- |
| S22 `r0s` | `arch/arm64/configs/r0s.config` | The target used by this project |
| S22+ `g0s` | `arch/arm64/configs/g0s.config` | A separate Galaxy S22+ configuration merged with `s5e9925_defconfig` |
| S22 Ultra `b0s` | `arch/arm64/configs/b0s.config` | A separate Galaxy S22 Ultra configuration merged with `s5e9925_defconfig` |

This is a **local source audit**, not a claim that the excluded kernel checkout
ships in this public repository or that these configurations boot our userspace
unchanged. It motivates only a porting hypothesis for the Exynos `SM-S906B`
and `SM-S908B` variants. Their panels, touchscreen/pen devices, DTBs and boot
artifacts need individual validation. The unresolved Xclipse compute problem
also prevents presenting either as a proven GPU-accelerated alternative.

The Snapdragon S22 variants are different targets. The Snapdragon S9
`SM-G9600/DS` (`starqltechn`) likewise must not be confused with Exynos
`starlte`/`star2lte` or US carrier models.

## Practical selection and acceptance

Our **inference from the port evidence** is to try OnePlus 6/6T, POCO F1 or
Fairphone 4 before buying another Exynos S22 solely for Linux. Their existing
native ports reduce the amount of initial kernel/device work. This is not a
price comparison, current-stock claim, battery-life ranking or measured model
speed comparison. Unlock eligibility and current device bugs still decide
whether a particular used phone is suitable.

Before treating another phone as supported, record:

1. Exact model/codename, firmware, actual unlock eligibility and a verified
   rollback. SIM-unlocked does not mean bootloader-unlocked. Official unlock
   policy, carrier restrictions and server availability can change.
2. Device-specific native boot, USB rescue and persistent storage; then cold
   boot and recovery without assuming the S22 partition layout.
3. Physical finger input, on-screen keyboard, screen layout/brightness,
   charging/thermals, Wi-Fi and a recoverable display session.
4. A working DRM/EGL renderer and ARM packages before enabling Hyprland or
   Omarchy services. Keep a mobile UI/rescue fallback. Do not run a generic
   desktop disk installer against phone partitions.
5. Real GPU shader correctness before LLM offload. Measure CPU and GPU prompt
   processing and token generation, memory use and sustained thermals. NPU
   inference needs its own driver/runtime/model validation; none is claimed.
6. Separate audio, Bluetooth, camera, calls, SMS, mobile data, emergency-call
   behavior, suspend/wake and unplugged battery-life acceptance if the device
   is meant to replace an everyday phone.

“Clean Linux” here means a native Linux userspace without Android services,
not erasing every vendor partition or removing firmware needed by peripherals.
Our S22 itself retains vendor boot/kernel components and rescue binaries.
Bootloader unlocking or storage conversion can erase data; preserve the
device's own firmware/calibration and use its maintained installation guide.

## Coverage is deliberately bounded

The README covers 25 device entries, including hypotheses and explicitly
unqualified variants. It does not claim to enumerate all phones ever ported
to Linux. The [postmarketOS catalog](https://wiki.postmarketos.org/wiki/Devices)
and [Ubuntu Touch catalog](https://devices.ubuntu-touch.io/) are the broader
inventories; their entries have very different levels and kinds of support.

Recent Galaxy S/Pixel Tensor models, arbitrary MediaTek phones, iPhones and
other unlisted devices are **not qualified by this survey**, not declared
impossible. Unlocking alone is insufficient. Older 32-bit Linux phones may be
useful with lightweight distributions but are outside this ARM64 Omarchy/model
shortlist. Evidence for a real port and usable hardware should precede adding
another model, not just a similar marketing name or a high NPU TOPS number.
