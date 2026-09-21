#!/usr/bin/env python3
"""Start the persistent Arch desktop and resident model from userdata.

This is deliberately a foreground supervisor, not an init replacement.  The
Alpine/native guardian remains the rescue layer.  It never formats, repairs,
or guesses a partition: userdata must already be ext4 with the identity below.
"""
from __future__ import annotations

import argparse
import errno
import fcntl
import hashlib
import json
import os
import re
import signal
import socket
import stat
import subprocess
import time
import urllib.request
from pathlib import Path

UUID = "1dd55c26-bd57-489a-9d9b-4c60e6f430eb"
EXPECTED_MAJOR, EXPECTED_MINOR = 259, 20
EXPECTED_SECTORS = 221257728
DEVICE = "/dev/block/by-name/userdata"
MOUNT = Path("/srv/s22")
ARCH = MOUNT / "arch"
MODEL = MOUNT / "model-bench"
DEPLOYMENT = MOUNT / ".persistent-ready.json"
RUNTIME_READY = Path("/run/s22-persistent-ready.json")
CHROOT = Path("/mnt/omarchy-trial")
LOCK = Path("/run/s22-persistent-desktop.lock")
SOUND_CARD_SYSFS = Path("/sys/class/sound/card0")
SOUND_CONTROL_SYSFS = Path("/sys/class/sound/controlC0")
SOUND_SYSFS_DEVICES = Path("/sys/devices")
SOUND_DIR = Path("/dev/snd")
SOUND_CONTROL = SOUND_DIR / "controlC0"
STRIDE_ENABLED = Path("/etc/s22-linear-stride-enabled")
WIFI_ENABLED = Path('/etc/s22-wifi-enabled')
WIFI_DISABLED = Path('/etc/s22-wifi-disabled')
TAILSCALE_ENABLED = Path('/etc/s22-tailscale-enabled')
MODEL_PROFILE_SELECTOR = Path('/etc/s22-model-profile')
QWEN2B_PROFILE = 'qwen2b'
QWEN4B_PROFILE = 'qwen4b'
QWEN4B_ALIAS = 's22-qwen4b'
QWEN2B_MODEL_ID = '/mnt/model-bench/models/Qwen3.5-2B-Q4_0.gguf'
QWEN4B_MODEL_ID = '/mnt/model-bench/models/Qwen3.5-4B-Uncensored-HauhauCS-Aggressive-Q4_K_M.gguf'
QWEN4B_SHA256 = '79e28ecacf84e75b6056cf4059636d435aa9eb67795780f7b7dbc7d32a962741'


class Failure(RuntimeError):
    pass


class AlreadyRunning(Failure):
    """Do not start a competing rescue compositor."""


def say(msg: str) -> None:
    print(f"start-persistent-desktop: {msg}", flush=True)


def command(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    p = subprocess.run(args, text=True, stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, check=False, timeout=30)
    if check and p.returncode:
        raise Failure(f"{' '.join(args)} failed ({p.returncode}): {p.stderr.strip()}")
    return p


def mount_lines() -> list[str]:
    try:
        return Path("/proc/self/mountinfo").read_text().splitlines()
    except OSError:
        return []


def is_mounted(path: Path) -> bool:
    target = os.path.realpath(path)
    for line in mount_lines():
        fields = line.split(" - ", 1)[0].split()
        if len(fields) > 4 and os.path.realpath(fields[4]) == target:
            return True
    return False


def mount_details(path: Path) -> tuple[str, str, str] | None:
    target = os.path.realpath(path)
    for line in mount_lines():
        left, sep, right = line.partition(" - ")
        fields = left.split()
        if len(fields) > 5 and os.path.realpath(fields[4]) == target and sep:
            post = right.split()
            if len(post) >= 2:
                return fields[2], post[0], fields[5]
    return None


def stat_device(path: str) -> tuple[int, int]:
    st = os.stat(path)
    if not stat.S_ISBLK(st.st_mode):
        raise Failure(f"{path} is not a block device")
    return os.major(st.st_rdev), os.minor(st.st_rdev)


def partition_size_sectors(path: str) -> int | None:
    real = os.path.realpath(path)
    name = Path(real).name
    try:
        return int(Path(f"/sys/class/block/{name}/size").read_text().strip())
    except (OSError, ValueError):
        return None


def filesystem_uuid(path: str) -> str | None:
    # BusyBox blkid does not implement util-linux's -s/-o options.  Parse its
    # stable key/value output instead, while accepting util-linux output too.
    p = command("blkid", path, check=False)
    text = p.stdout + p.stderr
    m = re.search(r'\bUUID="?([0-9A-Fa-f-]{36})"?', text)
    return m.group(1).lower() if m else None


def selected_model_profile(selector: Path = MODEL_PROFILE_SELECTOR) -> str:
    """Read the explicit model selector, defaulting only when absent."""
    try:
        value = selector.read_text().strip()
    except FileNotFoundError:
        return QWEN2B_PROFILE
    if value == QWEN2B_PROFILE:
        return QWEN2B_PROFILE
    if value in (QWEN4B_PROFILE, QWEN4B_ALIAS):
        return QWEN4B_PROFILE
    raise Failure(f"unsupported model profile {value!r}; expected qwen2b or qwen4b")


def model_id(profile: str) -> str:
    if profile == QWEN2B_PROFILE:
        return QWEN2B_MODEL_ID
    if profile == QWEN4B_PROFILE:
        return QWEN4B_MODEL_ID
    raise Failure(f"unsupported model profile {profile!r}")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def preflight(*, mounted_ok: bool = False) -> dict[str, object]:
    profile = selected_model_profile()
    selected_model_id = model_id(profile)
    if Path('/proc/1/comm').read_text().strip() != 'native-guardian':
        raise Failure('PID 1 is not native-guardian')
    props = dict(line.split('=', 1) for line in
                 Path('/sys/class/block/sda36/uevent').read_text().splitlines() if '=' in line)
    if props.get('PARTNAME') != 'userdata':
        raise Failure('sysfs PARTNAME is not userdata')
    if Path('/sys/class/block/sda36/start').read_text().strip() != '28594176':
        raise Failure('userdata start sector mismatch')
    if MOUNT.is_symlink():
        raise Failure('refusing symlink userdata mountpoint')
    if not os.path.exists(DEVICE):
        raise Failure(f"missing userdata PARTNAME device: {DEVICE}")
    major, minor = stat_device(DEVICE)
    if (major, minor) != (EXPECTED_MAJOR, EXPECTED_MINOR):
        raise Failure(f"userdata device identity {(major, minor)} != {(EXPECTED_MAJOR, EXPECTED_MINOR)}")
    sectors = partition_size_sectors(DEVICE)
    if sectors != EXPECTED_SECTORS:
        raise Failure(f"userdata size {sectors} sectors != {EXPECTED_SECTORS}")
    if not os.path.islink(DEVICE) or os.path.realpath(DEVICE) != "/dev/sda36":
        raise Failure(f"userdata PARTNAME link does not resolve to /dev/sda36: {os.path.realpath(DEVICE)}")
    fs_uuid = filesystem_uuid(DEVICE)
    if fs_uuid != UUID:
        raise Failure(f"userdata UUID {fs_uuid!r} != {UUID}")
    if is_mounted(MOUNT):
        if not mounted_ok:
            raise Failure(f"{MOUNT} is already mounted; refusing to assume ownership")
        details = mount_details(MOUNT)
        if not details or details[0] != "259:20" or details[1] != "ext4" or "rw" not in details[2].split(","):
            raise Failure(f"unexpected existing mount at {MOUNT}: {details}")
    elif not mounted_ok:
        if MOUNT.exists() and any(MOUNT.iterdir()):
            raise Failure(f"nonempty unmounted mountpoint: {MOUNT}")
    if mounted_ok:
        if not ARCH.is_dir() or not MODEL.is_dir():
            raise Failure("mounted userdata lacks arch/ or model-bench/")
        if not DEPLOYMENT.is_file():
            raise Failure(f"missing deployment marker: {DEPLOYMENT}")
        try:
            marker = json.loads(DEPLOYMENT.read_text())
        except (OSError, ValueError) as e:
            raise Failure(f"invalid deployment marker: {e}")
        required = ("schema", "uuid", "arch_archive_sha256", "model_sha256",
                    "server_sha256", "chat_sha256")
        if marker.get("schema") != "s22-persistent-v1" or marker.get("uuid") != UUID:
            raise Failure("deployment marker schema or UUID mismatch")
        if any(not isinstance(marker.get(k), str) or
               not re.fullmatch(r"[0-9a-f]{64}", marker[k]) for k in required[2:]):
            raise Failure("deployment marker lacks complete SHA-256 fields")
        checks = [(MODEL / "server/bin/llama-server", "server_sha256"),
                  (ARCH / "usr/local/bin/s22-chat", "chat_sha256")]
        checks.insert(0, (MODEL / "models/Qwen3.5-2B-Q4_0.gguf", "model_sha256"))
        if profile == QWEN4B_PROFILE:
            four_b = MODEL / Path(QWEN4B_MODEL_ID).relative_to('/mnt/model-bench')
            if not four_b.is_file():
                raise Failure(f"deployment artifact missing: {four_b}")
            actual = sha256_file(four_b)
            if actual != QWEN4B_SHA256:
                raise Failure(f"SHA-256 mismatch for {four_b}: {actual} != {QWEN4B_SHA256}")
        for path, key in checks:
            if not path.is_file():
                raise Failure(f"deployment artifact missing: {path}")
            actual = sha256_file(path)
            if actual != marker[key]:
                raise Failure(f"SHA-256 mismatch for {path}: {actual} != marker")
    return {"device": DEVICE, "major": major, "minor": minor,
            "sectors": sectors, "uuid": fs_uuid, "mounted": is_mounted(MOUNT),
            "model_profile": profile, "model_id": selected_model_id}


def mount_fs(path: Path, source: str, *, fstype: str | None = None,
             options: str | None = None) -> None:
    path.mkdir(parents=True, exist_ok=True)
    args = ["mount"]
    if fstype:
        args += ["-t", fstype]
    if options:
        args += ["-o", options]
    args += [source, str(path)]
    command(*args)


def bind(src: Path, dst: Path, *, readonly: bool = False) -> None:
    if not src.exists():
        raise Failure(f"missing bind source {src}")
    dst.mkdir(parents=True, exist_ok=True)
    if is_mounted(dst):
        raise Failure(f"refusing pre-existing mount at {dst}")
    mount_fs(dst, str(src), options="bind")
    if readonly:
        try:
            command("mount", "-o", "remount,bind,ro", str(dst))
        except BaseException:
            command('umount', str(dst), check=False)
            raise


def tmpfs(path: Path, mode: str, size: str) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if is_mounted(path):
        raise Failure(f'refusing pre-existing tmpfs at {path}')
    mount_fs(path, "tmpfs", fstype="tmpfs", options=f"mode={mode},size={size},nosuid")


def bind_file(src: Path, dst: Path, *, readonly: bool = False) -> None:
    if not src.exists() or dst.is_symlink():
        raise Failure(f"missing source or symlink bind destination {src} -> {dst}")
    if is_mounted(dst):
        raise Failure(f"refusing pre-existing mount at {dst}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    if not dst.exists():
        dst.touch(mode=0o600)
    command('mount', '-o', 'bind', str(src), str(dst))
    if readonly:
        try:
            command('mount', '-o', 'remount,bind,ro', str(dst))
        except BaseException:
            command('umount', str(dst), check=False)
            raise


def validate_optional_audio_control(*, card_sysfs: Path = SOUND_CARD_SYSFS,
                                    control_sysfs: Path = SOUND_CONTROL_SYSFS,
                                    sysfs_devices: Path = SOUND_SYSFS_DEVICES,
                                    sound_dir: Path = SOUND_DIR,
                                    control_node: Path = SOUND_CONTROL,
                                    require_node: bool = True) -> Path | None:
    """Return the one verified ALSA control node, or raise ``Failure``.

    The class entries are expected to be symlinks, so they are resolved and
    then constrained beneath /sys/devices.  This is deliberately narrower
    than exposing /dev/snd: no PCM or mixer-state node is created or bound.
    """
    devices = sysfs_devices.resolve(strict=True)
    card = card_sysfs.resolve(strict=True)
    expected_card = devices / "platform/sound/sound/card0"
    if card != expected_card:
        raise Failure(f"unexpected sound card ancestry: {card}")
    try:
        card_id = (card_sysfs / "id").read_text().strip()
    except OSError as exc:
        raise Failure(f"cannot read sound card identity: {exc}") from exc
    if card_id != "RainbowPrince":
        raise Failure(f"unexpected sound card identity: {card_id!r}")
    control = control_sysfs.resolve(strict=True)
    if control != card / "controlC0":
        raise Failure(f"unexpected sound control ancestry: {control}")
    try:
        dev = (control_sysfs / "dev").read_text().strip()
        event = dict(line.split("=", 1) for line in
                     (control_sysfs / "uevent").read_text().splitlines() if "=" in line)
    except OSError as exc:
        raise Failure(f"cannot read sound control identity: {exc}") from exc
    if dev != "116:114" or event.get("MAJOR") != "116" or event.get("MINOR") != "114":
        raise Failure(f"unexpected controlC0 sysfs identity: dev={dev!r}")
    if event.get("DEVNAME") != "snd/controlC0":
        raise Failure(f"unexpected controlC0 DEVNAME: {event.get('DEVNAME')!r}")
    if not require_node:
        return None

    try:
        directory = sound_dir.lstat()
    except OSError as exc:
        raise Failure(f"missing ALSA directory {sound_dir}: {exc}") from exc
    mode = stat.S_IMODE(directory.st_mode)
    if not stat.S_ISDIR(directory.st_mode) or directory.st_uid != 0 or mode not in (0o755, 0o777):
        raise Failure(f"unsafe ALSA directory {sound_dir} (uid={directory.st_uid}, mode={mode:o})")
    if mode == 0o777:
        os.chmod(sound_dir, 0o755)

    try:
        node = control_node.lstat()
    except OSError as exc:
        raise Failure(f"missing ALSA control node {control_node}: {exc}") from exc
    if (not stat.S_ISCHR(node.st_mode) or node.st_uid != 0 or
            stat.S_IMODE(node.st_mode) not in (0o600, 0o640, 0o660) or
            os.major(node.st_rdev) != 116 or os.minor(node.st_rdev) != 114):
        raise Failure(f"unsafe ALSA control node {control_node}")
    return control_node


def prepare_optional_audio_control() -> Path | None:
    """Trigger and validate controlC0 without making audio required startup."""
    try:
        # Validate the card's immutable platform identity before asking udev to
        # materialize the control node.  The post-trigger check repeats the
        # card check and then verifies controlC0's device identity.
        validate_optional_audio_control(require_node=False)
        command('udevadm', 'trigger', '--action=add', '--subsystem-match=sound',
                '--sysname-match=controlC0')
        command('udevadm', 'settle', '--timeout=10')
        return validate_optional_audio_control()
    except (Failure, OSError, ValueError, subprocess.SubprocessError) as exc:
        say(f"optional ALSA control unavailable; continuing without it: {exc}")
        return None


def terminate(proc: subprocess.Popen[str] | None, timeout: float = 3.0) -> None:
    if not proc:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        pass
    # A session leader may exit before its children. Kill the remaining owned
    # group too; never use a process-name-wide kill.
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        proc.wait(timeout=1)
    except subprocess.TimeoutExpired:
        pass


def chroot_cmd(env: dict[str, str], *args: str) -> list[str]:
    return ["chroot", str(CHROOT), "/usr/bin/env", "-i", *
            [f"{k}={v}" for k, v in env.items()], *args]


def healthy(url: str) -> bool:
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(url, timeout=1.0) as r:
            return r.status == 200 and json.loads(r.read()).get("status") == "ok"
    except Exception:
        return False


def model_command(profile: str) -> list[str]:
    command = [
        "/usr/bin/nice", "-n", "20", "/mnt/model-bench/server/libc/ld-linux-aarch64.so.1",
        "--library-path", "/mnt/model-bench/server/libc:/mnt/model-bench/server/lib",
        "/mnt/model-bench/server/bin/llama-server", "-m", model_id(profile),
        "-t", "4", "-C", "f0", "--cpu-strict", "1", "-c", "4096", "-np", "1",
        "-ngl", "0", "--host", "127.0.0.1", "--port", "8089", "--no-webui",
    ]
    if profile == QWEN4B_PROFILE:
        command += ["-b", "64", "-ub", "32", "--cache-ram", "0"]
    return command


def start_model(log: Path, retries: int = 3, profile: str | None = None) -> subprocess.Popen[str]:
    profile = selected_model_profile() if profile is None else profile
    cmd = chroot_cmd(
        {"HOME": "/root", "PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"},
        *model_command(profile))
    for attempt in range(1, retries + 1):
        try:
            with socket.create_connection(("127.0.0.1", 8089), timeout=.2):
                raise Failure("loopback port 8089 is already occupied")
        except OSError:
            pass
        say(f"starting model (attempt {attempt}/{retries})")
        with log.open("ab", buffering=0) as out:
            proc = subprocess.Popen(cmd, stdout=out, stderr=subprocess.STDOUT,
                                    start_new_session=True)
        try:
            for _ in range(120):
                if proc.poll() is not None:
                    break
                if healthy("http://127.0.0.1:8089/health"):
                    return proc
                time.sleep(0.25)
        except BaseException:
            terminate(proc)
            raise
        terminate(proc)
        time.sleep(1)
    raise Failure("model server did not become healthy after bounded retries")


def start_desktop(log: Path) -> tuple[subprocess.Popen[str], subprocess.Popen[str]]:
    # Marker is installed only after physical-panel confirmation. An explicit
    # environment value overrides it, so '=0' retains a no-preload rescue path.
    stride_setting = os.environ.get('S22_LINEAR_STRIDE_TRIAL')
    stride_trial = stride_setting == '1' or (stride_setting is None and STRIDE_ENABLED.is_file())
    stride_library = '/opt/s22-aquamarine/libs22-linear-stride.so'
    if stride_trial and not (CHROOT / stride_library.lstrip('/')).is_file():
        raise Failure('requested display stride trial library is missing')
    seat_env = {"HOME": "/root", "PATH": "/usr/local/bin:/usr/bin:/bin",
                "SEATD_VTBOUND": "0"}
    with log.open("ab", buffering=0) as out:
        seat = subprocess.Popen(chroot_cmd(seat_env, "/usr/bin/seatd", "-u", "root", "-g", "root"),
                                stdout=out, stderr=subprocess.STDOUT, start_new_session=True)
    sock = CHROOT / "run/seatd.sock"
    try:
        for _ in range(40):
            if sock.exists():
                break
            if seat.poll() is not None:
                raise Failure("seatd exited before creating socket")
            time.sleep(.1)
        else:
            raise Failure("seatd socket timeout")
    except BaseException:
        terminate(seat)
        raise
    env = {"HOME": "/root", "PATH": "/usr/local/bin:/opt/omarchy-source/bin:/usr/bin:/bin",
           "OMARCHY_PATH": "/opt/omarchy-source", "XDG_RUNTIME_DIR": "/run/user/0",
           "LANG": "C.UTF-8", "TZ": "Europe/Athens", "XDG_SESSION_TYPE": "wayland",
           "LIBSEAT_BACKEND": "seatd", "SEATD_VTBOUND": "0",
           "LD_LIBRARY_PATH": "/opt/s22-aquamarine:/usr/lib", "AQ_S22_DISPLAY_ONLY": "1",
           "AQ_DRM_DEVICES": "/dev/dri/card1", "LIBGL_ALWAYS_SOFTWARE": "1",
           "GALLIUM_DRIVER": "llvmpipe", "HYPRLAND_NO_CRASHREPORTER": "1"}
    if stride_trial:
        env.update({'LD_PRELOAD': stride_library, 'S22_LINEAR_STRIDE': '1'})
    try:
        with log.open("ab", buffering=0) as out:
            desktop = subprocess.Popen(chroot_cmd(env, "/usr/bin/dbus-run-session", "--",
                                                   "/usr/bin/Hyprland", "--i-am-really-stupid",
                                                   "--config", "/root/hyprland-omarchy-ui.lua"),
                                       stdout=out, stderr=subprocess.STDOUT,
                                       start_new_session=True)
    except BaseException:
        terminate(seat)
        raise
    return seat, desktop


def rescue_if_requested() -> None:
    say("execing configured Alpine rescue Weston")
    env = os.environ.copy()
    env["S22_RESCUE"] = "1"
    os.execve("/usr/local/bin/start-weston-native",
              ["/usr/local/bin/start-weston-native"], env)


def start_optional_wifi() -> subprocess.Popen | None:
    """Spawn once after desktop readiness; radio failure is not GUI failure."""
    if not WIFI_ENABLED.is_file() or WIFI_DISABLED.exists():
        return None
    try:
        script = MOUNT / 'hardware/wifi-tools/wifi-autostart.py'
        private = MOUNT / 'hardware/wifi-private'
        if not script.is_file() or private.is_symlink() or not private.is_dir():
            raise Failure('optional Wi-Fi deployment missing')
        if private.stat().st_mode & 0o077:
            raise Failure('optional Wi-Fi private directory permissions')
        fd = os.open(private / 'autostart.log', os.O_WRONLY | os.O_CREAT |
                     os.O_APPEND | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'ab', buffering=0) as out:
            os.fchmod(out.fileno(), 0o600)
            child = subprocess.Popen(['/usr/bin/python3', str(script), '--start'],
                                     stdin=subprocess.DEVNULL, stdout=out,
                                     stderr=subprocess.STDOUT, start_new_session=True,
                                     close_fds=True, cwd='/')
        say(f'optional Wi-Fi startup launched pid={child.pid}')
        return child
    except Exception as error:
        say(f'optional Wi-Fi startup unavailable: {type(error).__name__}')
        return None


def prepare_data_mount(reuse: bool) -> list[Path]:
    """Never own an existing data mount; reuse only after full validation."""
    if reuse and is_mounted(MOUNT):
        preflight(mounted_ok=True)
        return []
    preflight()
    mount_fs(MOUNT, DEVICE, fstype="ext4", options="rw,noatime,nosuid,nodev,errors=remount-ro")
    return [MOUNT]


def start_optional_tailscale() -> None:
    if not TAILSCALE_ENABLED.is_file():
        return
    try:
        result = command('/usr/bin/python3', str(MOUNT / 'network/start-tailscale.py'), '--start')
        say(result.stdout.strip())
    except Exception as error:
        say(f'optional Tailscale startup unavailable: {type(error).__name__}')


def optional_agent_web(action: str) -> None:
    """The web terminal is optional; Tailscale/rescue remain independent."""
    base = MOUNT / 'agent-web'
    if not (base / 'enabled').is_file():
        return
    try:
        script = base / 'start-agent-web.py'
        st = script.stat()
        if script.is_symlink() or st.st_uid != 0 or st.st_mode & 0o022:
            raise Failure('optional web helper ownership changed')
        result = command('/usr/bin/python3', str(script), action)
        say('optional agent web: ' + result.stdout.strip())
    except Exception as error:
        say(f'optional agent web {action} unavailable: {type(error).__name__}')


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="read-only identity and layout check")
    ap.add_argument("--foreground", action="store_true", help="run supervisor in foreground (default)")
    ap.add_argument("--reuse-data-mount", action="store_true",
                    help="reuse a validated userdata mount during a desktop-only restart")
    args = ap.parse_args()
    def stop_signal(_signum: int, _frame: object) -> None:
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, stop_signal)
    signal.signal(signal.SIGINT, stop_signal)
    signal.signal(signal.SIGHUP, stop_signal)
    try:
        if args.check:
            info = preflight(mounted_ok=is_mounted(MOUNT))
            print(json.dumps(info, sort_keys=True))
            return 0
        if Path("/proc/1/comm").read_text().strip() != "native-guardian":
            raise Failure("PID 1 is not native-guardian")
        LOCK.parent.mkdir(parents=True, exist_ok=True)
        lockfd = os.open(LOCK, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(lockfd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as e:
            if e.errno in (errno.EACCES, errno.EAGAIN):
                raise AlreadyRunning("another persistent desktop supervisor owns the lock")
            raise
        if command('pidof', 'weston', 'Hyprland', check=False).returncode == 0:
            raise AlreadyRunning('an existing compositor is active; refusing to take it over')
        if Path("/etc/s22-persistent-disabled").exists() or Path('/run/s22-persistent-disabled').exists():
            raise Failure("persistent desktop explicitly disabled")
        owned_mounts = prepare_data_mount(args.reuse_data_mount)
        try:
            readiness = preflight(mounted_ok=True)
            profile = str(readiness['model_profile'])
            # Remote access must not depend on the model or compositor starting.
            start_optional_tailscale()
            RUNTIME_READY.unlink(missing_ok=True)
            if command('pidof', 'udevd', check=False).returncode:
                command('/sbin/udevd', '--daemon')
            # Sound is optional for desktop startup.  Trigger only the
            # verified control node; PCM nodes and mixer state remain hidden.
            audio_control = prepare_optional_audio_control()
            command('udevadm', 'trigger', '--action=add', '--subsystem-match=input')
            command('udevadm', 'trigger', '--subsystem-match=drm')
            command('udevadm', 'settle', '--timeout=10')
            # Bind the complete candidate first; all runtime submounts then
            # become visible through the same root used by chroot(2).
            bind(ARCH, CHROOT); owned_mounts.append(CHROOT)
            for p, mode, size in ((CHROOT / 'dev', '0755', '8m'),
                                  (CHROOT / "run", "0755", "64m"),
                                  (CHROOT / "tmp", "1777", "128m")):
                tmpfs(p, mode, size)
                owned_mounts.append(p)
            (CHROOT / "run/user").mkdir(parents=True, exist_ok=True)
            (CHROOT / "run/user/0").mkdir(exist_ok=True)
            os.chmod(CHROOT / "run/user/0", 0o700)
            for src, dst, ro in ((Path("/dev/dri"), CHROOT / "dev/dri", False),
                                 (Path("/dev/input"), CHROOT / "dev/input", False),
                                 (Path("/dev/pts"), CHROOT / "dev/pts", False),
                                 (Path("/proc"), CHROOT / "proc", True),
                                 (Path("/sys"), CHROOT / "sys", True),
                                 (Path("/run/udev"), CHROOT / "run/udev", True)):
                bind(src, dst, readonly=ro); owned_mounts.append(dst)
            for name in ("null", "zero", "random", "urandom"):
                bind_file(Path("/dev") / name, CHROOT / "dev" / name)
                owned_mounts.append(CHROOT / "dev" / name)
            if audio_control is not None:
                audio_target = CHROOT / "dev/snd/controlC0"
                try:
                    bind_file(audio_control, audio_target)
                except (Failure, OSError, ValueError, subprocess.SubprocessError) as exc:
                    # bind_file may have created its private placeholder before
                    # mount(2) failed; never leave that non-device in /dev.
                    if not is_mounted(audio_target) and audio_target.exists() and not audio_target.is_symlink():
                        audio_target.unlink()
                    say(f"optional ALSA control bind unavailable; continuing without it: {exc}")
                else:
                    owned_mounts.append(audio_target)
            for name, target in {'ptmx':'pts/ptmx','fd':'/proc/self/fd',
                                 'stdin':'/proc/self/fd/0','stdout':'/proc/self/fd/1',
                                 'stderr':'/proc/self/fd/2'}.items():
                (CHROOT/'dev'/name).symlink_to(target)
            (CHROOT/'dev/shm').mkdir(mode=0o1777)
            os.chmod(CHROOT/'dev/shm', 0o1777)
            tmpfs(CHROOT/'dev/shm', '1777', '128m')
            owned_mounts.append(CHROOT/'dev/shm')
            # Candidate images may carry a desktop-host stub resolver
            # (127.0.0.53).  Use the live Alpine resolver, read-only.
            bind_file(Path("/etc/resolv.conf"), CHROOT / "etc/resolv.conf", readonly=True)
            owned_mounts.append(CHROOT / "etc/resolv.conf")
            bind(MODEL, Path("/mnt/model-bench")); owned_mounts.append(Path("/mnt/model-bench"))
            bind(MODEL, CHROOT / "mnt/model-bench", readonly=True)
            owned_mounts.append(CHROOT / "mnt/model-bench")
            state = MOUNT / "state"; state.mkdir(exist_ok=True)
            model = seat = desktop = None
            try:
                model = start_model(state / "model-server.log", profile=profile)
                seat, desktop = start_desktop(state / "desktop.log")
                for _ in range(150):
                    if desktop.poll() is not None:
                        raise Failure("Hyprland exited before readiness")
                    if list((CHROOT / 'run/user/0').glob('wayland-[0-9]')):
                        break
                    time.sleep(.1)
                else:
                    raise Failure("Wayland socket did not appear before readiness")
                for _ in range(100):
                    ready_clients = all(command('pidof', name, check=False).returncode == 0
                                        for name in ('foot', 'squeekboard', 'quickshell'))
                    if ready_clients:
                        break
                    if desktop.poll() is not None:
                        raise Failure('desktop exited while waiting for UI clients')
                    time.sleep(.1)
                else:
                    raise Failure('terminal, keyboard or Omarchy shell did not start')
                runtime = {"uuid": UUID, "device": DEVICE,
                    "arch": str(ARCH), "model": str(MODEL), "model_port": 8089,
                    "model_profile": profile, "model_id": model_id(profile),
                    "desktop_pid": desktop.pid, "model_pid": model.pid,
                    "supervisor_pid": os.getpid(), "started_at": int(time.time())}
                RUNTIME_READY.write_text(json.dumps(runtime, sort_keys=True) + "\n")
                say("persistent desktop and model ready")
                optional_agent_web('--start')
                wifi_startup = start_optional_wifi()
                model_restarts = 0
                while desktop.poll() is None:
                    if wifi_startup is not None and wifi_startup.poll() is not None:
                        say(f'optional Wi-Fi startup exited status={wifi_startup.returncode}; no retry')
                        wifi_startup = None
                    if model.poll() is not None:
                        if model_restarts >= 2:
                            raise Failure("model server exceeded bounded runtime restarts")
                        model_restarts += 1
                        model = start_model(state / "model-server.log", profile=profile)
                        runtime['model_pid'] = model.pid
                        runtime['model_restarts'] = model_restarts
                        RUNTIME_READY.write_text(json.dumps(runtime, sort_keys=True) + '\n')
                    time.sleep(1)
                raise Failure(f"desktop exited with status {desktop.returncode}")
            finally:
                optional_agent_web('--stop')
                RUNTIME_READY.unlink(missing_ok=True)
                terminate(desktop); terminate(seat); terminate(model)
        finally:
            for p in reversed(owned_mounts):
                result = command("umount", str(p), check=False)
                if result.returncode:
                    say(f'cleanup could not unmount {p}: {result.stderr.strip()}')
    except AlreadyRunning as e:
        say(f'ERROR: {e}')
        return 1
    except Exception as e:
        say(f"ERROR: {e}")
        if not args.check:
            rescue_if_requested()
        return 1
    except KeyboardInterrupt:
        say("stopping on signal")
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
