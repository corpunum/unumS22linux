# Native Linux on this S22

Verified on 2026-09-19: Alpine 3.24.2 ARM64 boots from RECOVERY, with native
guardian PID1, persistent packages/files, USB SSH/internet and a CPU-rendered
Weston Wayland desktop. No Android services are running. This uses the shipping
Samsung/Lineage kernel and modules, not a mainline kernel. AOSP first-stage init
still performs the initial module/bootstrap work, then hands off before Android
services start; Android recovery binaries remain in the image for rescue.

## Connect and use

From this computer, with the same USB cable connected:

```sh
/home/corpunum/s22-linux/tools/s22-ssh
```

The host's public SSH key is authorized. Password login is disabled. The
persisted SSH host-key public-file SHA256 is
`92e22dd4d7aa889578c3b01cada1a6c715690ff5941ce83763bb3c74afd432de`.

On the phone:

```sh
apk add PACKAGE
python3 -m venv /root/my-agent
. /root/my-agent/bin/activate
python -m pip install PACKAGE
tmux
htop
s22-reboot recovery
```

Python, pip, virtualenv, Git, curl, tmux, htop and nano are installed. A local
CPU chat launcher, `s22-chat`, uses the separately RAM-staged Qwen3.5 2B Q4
runtime. Restore its verified weights after reboot from this host with
`tools/model-bench/stage-phone.sh`. No agent account or API credential is
configured; the chat client does not execute model-generated commands.
GPU/NPU inference is not working. Measured throughput and the newer Omarchy
display trial are documented in [the driver/model report](DRIVER_MODELS_2026-09-20.md).

Use `s22-reboot recovery`, not an ordinary untargeted reboot. This flushes
filesystems and uses the tested Samsung RESTART2 recovery target without a
userspace BCB write. Normal cold-power-on boot has **not** been converted to
Linux: BOOT and bootloader partitions remain untouched.

## Storage and networking

The embedded base root is extracted into RAM. OverlayFS stores changes under
`/cache/s22-linux/upper`, with its work directory alongside. Existing cache
contents were backed up before creating this directory; CACHE was not formatted.
`/tmp` is a separate 256 MiB RAM filesystem and is cleared on reboot.

Storage is deliberately small: the cache filesystem is about 583 MiB total.
Check `df -h /` before installing packages. Large model weights do not fit in
the remaining space. Do not format or reuse userdata to expand it without a
separate preservation/storage plan.

USB networking uses phone `10.55.0.2`, host `10.55.0.1`. The host NetworkManager
profile `s22-linux-usb` shares internet over the MAC-specific ECM interface
`enx027322000001`. Wi-Fi, cellular, suspend, audio, cameras, GPU compute and NPU
inference are not accepted/tested features of this Linux installation.

## Boot image and evidence

Current installed image: `builds/native_handoff_v3.img`, SHA256
`1a827b43d29141efb47f530902dd4e4ee2b6d780515893c9ecd676ad27efd7d1`.
Its whole RECOVERY-partition read-back matched. BORE record 517 confirms actual
RECOVERY selection; live native uptime passed 100 seconds without interruption.
The persisted root marker, SSH keys, package versions and automatic desktop
were verified after the reboot. See `EXPERIMENTS.md` and
`evidence/native-linux-20260919/native-v3-live.txt`.

Later: the Omarchy/driver session software-rebooted successfully into BORE
record **518** at 2026-09-19 23:02:18 UTC without reflashing. The current
Arch/Omarchy RAM desktop and resident CPU chat are described in
[OMARCHY_TRIAL.md](OMARCHY_TRIAL.md) and
[DRIVER_MODELS_2026-09-20.md](DRIVER_MODELS_2026-09-20.md).

Read only the start of the BORE log: `head -c 4096 /proc/boot_reset | tr -d '\000'`. It has a
large trailing NUL-filled area; unfiltered `cat` is noisy. It records selected
boot mode, not proof that an arbitrary replacement init successfully ran.

The host `s22-native-auto-ack.service` remains enabled and only ACKs the verified
native guardian on the exact USB link. V3 also retains healthy Linux without
the ACK; disconnecting the host is not intended to trigger Android fallback.
The earlier hot transition into Android recovery broke USB. That early-failure
rescue path remains unproven; do not deliberately exercise it on the working
installation.

## Rollback

Known-good Lineage recovery:
`lineage/build-20260915/recovery.img`, SHA256
`b5bf01c4a47091eb95078fc69b133b44c2b453b31c23433594c5b605e3747b55`.

Only after positively identifying Download Mode, the existing rollback command
is:

```sh
/home/corpunum/s22-linux/tools/samloader/samloader flash --verbose --no-reboot -p RECOVERY /home/corpunum/s22-linux/lineage/build-20260915/recovery.img
```

Only RECOVERY is an authorized image-write target. Never flash PIT, EFS, MISC,
BOOT, bootloader or TrustZone. Restoring recovery does not delete the Linux
folder in CACHE, although later recovery cache-wipe operations would.
