# Other phones: native Linux and Omarchy feasibility

**Yes, the approach can be adapted—but this is not a universal phone image.**
Only the **SM-S901B/DS S22** has been exercised by this project. The table is
a researched candidate list, **not a supported-device list or an exhaustive
list of every Linux-capable phone**. Never flash this project's S22 images
onto another model, including an S22+ or Ultra.

Research reviewed **2026-09-21**. Device-specific links below are primary port
documentation. Some postmarketOS wiki pages could only be read through older
indexed snapshots because direct access was blocked; their component status
may be stale. The live [postmarketOS 26.06 release][pmos-release] corroborates
several maintained device families, but does not certify every component.
See [source freshness, selection criteria and porting boundaries](PHONE_PORTABILITY.md).

**Screen / touch / Wi-Fi:** `Y` = reported working, `P` = partial,
`N` = reported broken, `?` = not established in the reviewed evidence.
These are upstream reports, **not our hardware tests**, except the S22 row.
“Candidate” in the last column is our engineering assessment. **No other row
has a verified Arch ARM + Hyprland + Omarchy installation from this project.**

| Phone / exact target | SoC | Native Linux evidence / route | Screen / touch / Wi-Fi | Graphics and Omarchy assessment |
| --- | --- | --- | --- | --- |
| **Galaxy S22 SM-S901B/DS** — `r0s` | Exynos 2200 | **Measured here:** vendor-kernel RECOVERY handoff to Alpine + persistent Arch userspace | Y / ? / Y; physical finger input unverified | Software-rendered Hyprland + Omarchy UI; Samsung OpenCL/Vulkan compute and Qwen0.8B GPU inference verified through isolated Bionic runtime |
| Galaxy S22+ SM-S906B — `g0s` | Exynos 2200 | **Hypothesis only:** related kernel target; separate images/bring-up required [details][s22-relatives] | ? / ? / ? | Closest porting relative, not an easier/proven GPU solution |
| Galaxy S22 Ultra SM-S908B — `b0s` | Exynos 2200 | **Hypothesis only:** related kernel target; panel/touch/pen differ [details][s22-relatives] | ? / ? / ? | Related GPU family; no transfer of S22 acceptance |
| [OnePlus 6][op6] — `enchilada` | Snapdragon 845 | Existing native pmOS port; 26.06 community | Y / Y / P | Adreno 630 3D reported working; **first-choice additional trial** |
| [OnePlus 6T][op6t] — `fajita` | Snapdragon 845 | Existing native pmOS port; 26.06 community | Y / Y / Y | 3D reported working; strong trial candidate; check carrier/unlock and audio variant |
| [POCO F1 / Pocophone F1][poco-f1] — `beryllium` | Snapdragon 845 | Existing native pmOS port; 26.06 community | Y / Y / P | 3D reported working; strong trial candidate; match EBBG/Tianma panel |
| [SHIFT6mq][shift6mq] — `axolotl` | Snapdragon 845 | Existing native pmOS/SDM845 port; 26.06 community | ? / ? / ? | Promising platform; component acceptance must be rechecked, not inferred from OP6 |
| [Fairphone 4][fp4] — `fp4` | Snapdragon 750G | Existing native pmOS port; 26.06 community | Y / Y / Y | 3D reported working; **strong trial candidate**; audio/calls/camera still need review |
| [Fairphone 5][fp5] — `fp5` | QCM6490 | Existing native pmOS port; testing in reviewed device snapshot | Y / Y / Y | 3D reported working; newer experimental target, not full phone/Omarchy acceptance |
| [Google Pixel 3a][pixel3a] — `sargo` | Snapdragon 670 | Existing native pmOS port; 26.06 community | Y / Y / P | 3D reported partial; repair/validate graphics before assuming Hyprland |
| [Google Pixel 3a XL][pixel3axl] — `bonito` | Snapdragon 670 | Existing native pmOS port; 26.06 community | Y / Y / P | 3D reported working; conditional trial, not equivalent to smaller model |
| [OnePlus 5][op5] — `cheeseburger` | Snapdragon 835 | Native/close-mainline pmOS testing port | Y / Y / Y | Adreno 540 3D reported working; desktop candidate, **not a Turnip Vulkan target** |
| [OnePlus 5T][op5t] — `dumpling` | Snapdragon 835 | Native/close-mainline pmOS testing port | Y / Y / Y | Same Adreno 540 limitation; telephony/audio/camera are not accepted |
| [Xiaomi Mi 9T / Redmi K20][mi9t] — `davinci` | Snapdragon 730 | Native generic-SM7150 route; 26.06 community | Y / Y / Y | Adreno 618 3D reported working; candidate with audio/camera/power caveats |
| [POCO X3 NFC][poco-x3] — `surya` | Snapdragon 732G | Native generic-SM7150 route; 26.06 community | Y / Y / Y | 3D reported working; touchscreen firmware and charging need attention; not X3 Pro |
| [Xiaomi Mi Mix 2S][mix2s] — `polaris` | Snapdragon 845 | Native pmOS testing port | P / P / P | 3D reported working, but display/touch/Wi-Fi problems make this a secondary target |
| [Xiaomi Mi 8][mi8-family] — `dipper` | Snapdragon 845 | Experimental native family port | Screen/touch/Wi-Fi need per-variant recheck | Driver-development target; not a usable-Omarchy recommendation |
| [Xiaomi Mi 8 Pro][mi8-family] — `equuleus` | Snapdragon 845 | Experimental native family port | Screen/touch/Wi-Fi need per-variant recheck | Separate panel/fingerprint variant; do not inherit Mi 8 results |
| [Xiaomi Mi 8 Explorer Edition][mi8-family] — `ursa` | Snapdragon 845 | Experimental native family port | Screen/touch/Wi-Fi need per-variant recheck | Separate variant; neither desktop nor model acceleration accepted |
| [Galaxy S9 SM-G9600/DS][s9-qcom] — `starqltechn` | Snapdragon 845 | Native/close-mainline port; older snapshot says testing | Y / Y / N | Interesting Samsung alternative, but snapshot Wi-Fi/BT broken; not Exynos or US S9 |
| [PINE64 PinePhone][pinephone] — `pinephone` | Allwinner A64 | Linux-first hardware; native distributions, SD/eMMC route | Y / Y / Y | Mali-400/Lima is limited; prefer a lighter mobile UI over this Omarchy target |
| [PINE64 PinePhone Pro][pinephone-pro] — `pinephonepro` | RK3399S | Linux-first hardware; native distributions, device-specific bootloader | P / Y / Y | Mali-T860/Panfrost; possible desktop trial, not a strong local-LLM/GPU-compute choice |
| [Purism Librem 5][librem5] — `librem5` | NXP i.MX8MQ | Linux-first PureOS and native pmOS port | Y / Y / Y | Vivante 3D reported working; custom desktop possible, modest CPU/RAM platform |
| [Galaxy S9 Exynos][s9-exynos] — `starlte` | Exynos 9810 | Downstream port entry only; **not qualified by this survey** | ? / ? / ? | Do not confuse with the Snapdragon S9 port; new driver assessment required |
| [Galaxy S9+ Exynos][s9plus-exynos] — `star2lte` | Exynos 9810 | Downstream port entry only; **not qualified by this survey** | ? / ? / ? | Research-only lead, not a claim of a working native desktop |

For a second development phone, our shortlist is **OnePlus 6/6T, POCO F1,
or Fairphone 4**, after verifying the exact unit's bootloader and current port
regressions. This is a lower-porting-risk assessment, not a benchmark or a
promise of daily-driver reliability. Existing native ports should use their
own maintained kernel/boot instructions; copying the S22 recovery workaround
is unnecessary and potentially destructive.

**Hyprland is not the same as full Omarchy.** Omarchy now has official ARM
initiatives for [Apple machines][omarchy-m] and [Snapdragon computers][omarchy-dragon].
Those announcements concern computers, not blanket smartphone support. The
phone work still needs compatible ARM packages, DRM/EGL, touch/keyboard setup,
power management and phone-safe installation. Phosh, Plasma Mobile or Sxmo
can be a more practical first interface; they are not Omarchy.

**Graphics support is not AI acceleration.** [Freedreno/Turnip][freedreno]
makes supported Adreno 6xx devices promising GPU-compute experiments, but a
working 3D desktop is not proof of correct or fast `llama.cpp` inference.
Adreno 5xx is not supported by Turnip. [Panfrost's API support][panfrost] also
varies by Mali generation. No GPU-model or NPU-inference result is claimed
for any additional phone in this table. Treat GPU memory as shared system RAM,
not extra dedicated VRAM, and benchmark each actual device/runtime.

**Before buying or installing:** verify the exact regional/carrier model,
real bootloader-unlock eligibility (SIM-unlocked is not enough), required
firmware, current touchscreen/panel variant, and a recoverable installation
path. Unlocking may wipe data. Keep firmware, calibration and recovery
backups; do not erase Android-related partitions just to make Linux “clean.”
Our scope is native Linux userspace without running Android services—not a
promise of an entirely open bootloader, kernel or firmware stack.

For the broader, changing inventory, consult the [postmarketOS device catalog][pmos-devices].
The [Ubuntu Touch catalog][ubports-devices] separately identifies Native,
Halium and Legacy ports; a Halium port is not evidence that this project's
Android-service-free Arch/Hyprland path will work.

[pmos-release]: https://postmarketos.org/blog/2026/06/21/v26.06-release/
[pmos-devices]: https://wiki.postmarketos.org/wiki/Devices
[s22-relatives]: PHONE_PORTABILITY.md#closest-s22-relatives
[op6]: https://wiki.postmarketos.org/wiki/OnePlus_6_%28oneplus-enchilada%29
[op6t]: https://wiki.postmarketos.org/wiki/OnePlus_6T_%28oneplus-fajita%29
[poco-f1]: https://wiki.postmarketos.org/wiki/Xiaomi_POCO_F1_%28xiaomi-beryllium%29
[shift6mq]: https://wiki.postmarketos.org/wiki/SHIFT_SHIFT6mq_%28shift-axolotl%29
[fp4]: https://wiki.postmarketos.org/wiki/Fairphone_4_%28fairphone-fp4%29
[fp5]: https://wiki.postmarketos.org/wiki/Fairphone_5_%28fairphone-fp5%29
[pixel3a]: https://wiki.postmarketos.org/wiki/Google_Pixel_3a_%28google-sargo%29
[pixel3axl]: https://wiki.postmarketos.org/wiki/Google_Pixel_3a_XL_%28google-bonito%29
[op5]: https://wiki.postmarketos.org/wiki/OnePlus_5_%28oneplus-cheeseburger%29
[op5t]: https://wiki.postmarketos.org/wiki/OnePlus_5T_%28oneplus-dumpling%29
[mi9t]: https://wiki.postmarketos.org/wiki/Xiaomi_Mi_9T_/_Redmi_K20_%28xiaomi-davinci%29
[poco-x3]: https://wiki.postmarketos.org/wiki/Xiaomi_POCO_X3_NFC_%28xiaomi-surya%29
[mix2s]: https://wiki.postmarketos.org/wiki/Xiaomi_Mi_Mix_2S_%28xiaomi-polaris%29
[mi8-family]: https://wiki.postmarketos.org/wiki/Xiaomi_Mi_8_%28SDM845%29_%28xiaomi-dipper%2C_xiaomi-equuleus%2C_xiaomi-ursa%29
[s9-qcom]: https://wiki.postmarketos.org/wiki/Samsung_Galaxy_S9_%28samsung-starqltechn%29
[s9-exynos]: https://wiki.postmarketos.org/wiki/Samsung_Galaxy_S9_%28samsung-starlte%29
[s9plus-exynos]: https://wiki.postmarketos.org/wiki/Samsung_Galaxy_S9%2B_%28samsung-star2lte%29
[pinephone]: https://wiki.postmarketos.org/wiki/PINE64_PinePhone_%28pine64-pinephone%29
[pinephone-pro]: https://pine64.org/documentation/PinePhone_Pro/_full/
[librem5]: https://wiki.postmarketos.org/wiki/Purism_Librem5_%28purism-librem5%29
[omarchy-m]: https://omarchy.org/news/2026/09/introducing-omarchy-m/
[omarchy-dragon]: https://omarchy.org/news/2026/09/introducing-omarchy-dragon/
[freedreno]: https://docs.mesa3d.org/drivers/freedreno.html
[panfrost]: https://docs.mesa3d.org/drivers/panfrost.html
[ubports-devices]: https://devices.ubuntu-touch.io/
