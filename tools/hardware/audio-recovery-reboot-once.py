#!/usr/bin/env python3
"""One explicitly selected recovery reboot, followed by bounded observation.

Requires the separate, successful audio RECOVERY flash receipt. No retries of
reboot, firmware writes, Android targets or hardware-control probes.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import pwd
import re
import shlex
import shutil
import stat
import struct
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
RIG_HOME = Path(pwd.getpwuid(os.getuid()).pw_dir)
TRIAL_STATE_ROOT = RIG_HOME / '.local/state/s22-hci-trial-20260924'
OUT = TRIAL_STATE_ROOT / 'observer'
EXPECTED_TARGET = 'recovery'
EXPECTED_FLASH_SHA256 = '42da267f3dd9f94f30f62a95fb2ac13f91d4cf98f1a2307f7cc14e45d9c49be5'
EXPECTED_BASE_SHA256 = '758fc9d30491e17b7c829a89d338ba69476efa15a1280deb8a1b9b8009687f4b'
EXPECTED_GNU_BUILD_ID = 'b2dda820b18d410d9bf12f1bd2584567d545991d'
EXPECTED_FLASH_BYTES = 100663296
REQUIRED_MODULES = ('wlan', 'cfg80211')
REQUIRED_HCI_COMPONENTS = ('btpower', 'exynos_tty', 'bluetooth', 'hci_uart')
EXPECTED_BOOT_MODEL = 'SM-S901B'
EXPECTED_BOOT_HARDWARE = 's5e9925'
EXPECTED_PERSISTENT_UUID = '1dd55c26-bd57-489a-9d9b-4c60e6f430eb'
MIN_UPTIME_SECONDS = 180
OBSERVATION_SECONDS = 600
SERIOUS_FAULT = re.compile(r'Kernel panic|Oops:|BUG:|Unable to handle kernel|general protection fault|Out of memory:|oom-kill|Call trace:', re.I)
BORE_RECORD = re.compile(r'^\[\s*\d+\].*$', re.M)

class ObserverError(RuntimeError):
    pass


WIFI_ADDRESS = r'''import ipaddress,json,subprocess
r=subprocess.run(['ip','-j','-4','addr','show','dev','wlan0'],capture_output=True,text=True,timeout=4,check=False)
if r.returncode: raise SystemExit(2)
items=json.loads(r.stdout)
addresses=[a['local'] for x in items for a in x.get('addr_info',[]) if a.get('family')=='inet' and isinstance(a.get('local'),str)]
private=[a for a in addresses if ipaddress.ip_address(a).is_private and not ipaddress.ip_address(a).is_loopback and not ipaddress.ip_address(a).is_link_local and not ipaddress.ip_address(a).is_reserved]
if not private: raise SystemExit(3)
print(private[0])
'''

SNAPSHOT = r'''
import json,os,pathlib,re,struct,subprocess,urllib.request
p=pathlib.Path
def read(name,limit=16384):
 try:
  with open(name,'rb') as f:return f.read(limit).decode('utf-8','replace').rstrip('\0\n')
 except OSError:return None
def readb(name,limit=1048576):
 try:
  with open(name,'rb') as f:return f.read(limit)
 except OSError:return None
def gnu_build_id(data):
 if data is None:return None
 pos=0
 while pos+12<=len(data):
  namesz,descsz,kind=struct.unpack_from('<III',data,pos);pos+=12
  name_end=pos+namesz;name_padded=pos+((namesz+3)&~3)
  desc_end=name_padded+descsz;next_pos=name_padded+((descsz+3)&~3)
  if name_end>len(data) or desc_end>len(data) or next_pos>len(data):return None
  if data[pos:name_end].rstrip(b'\0')==b'GNU' and kind==3:
   return data[name_padded:desc_end].hex()
  pos=next_pos
 return None
ready=read('/run/s22-persistent-ready.json')
try:persistent=json.loads(ready) if ready else None
except Exception:persistent=None
persistent_mount_ready=False
try:
 for line in read('/proc/self/mountinfo',131072).splitlines():
  left,separator,right=line.partition(' - ')
  fields=left.split();post=right.split()
  if separator and len(fields)>5 and len(post)>2 and fields[4]=='/srv/s22':
   persistent_mount_ready=(fields[2]=='259:20' and post[0]=='ext4' and
                           'rw' in fields[5].split(','))
   break
except Exception:pass
persistent_summary=({'ready':persistent.get('uuid')==__EXPECTED_PERSISTENT_UUID__ and persistent_mount_ready,
 'mount_ready':persistent_mount_ready}
 if isinstance(persistent,dict) else {'ready':False,'mount_ready':persistent_mount_ready})
try:
 op=urllib.request.build_opener(urllib.request.ProxyHandler({}))
 with op.open('http://127.0.0.1:8089/health',timeout=3) as f:health=json.load(f)
except Exception:health=None
try:
 op=urllib.request.build_opener(urllib.request.ProxyHandler({}))
 with op.open('http://127.0.0.1:8089/slots',timeout=3) as f:raw_slots=json.load(f)
except Exception:raw_slots=None
try:
 r=subprocess.run(['dmesg'],capture_output=True,text=True,timeout=5,check=False)
 log=r.stdout[-131072:] if r.returncode==0 else None
except Exception:log=None
records=re.findall(r'^\[\s*\d+\].*$',read('/proc/boot_reset',16384) or '',re.M)
module_lines=(read('/proc/modules',1048576) or '').splitlines()
loaded_modules=sorted({line.split()[0] for line in module_lines if line.split()})
components=sorted(x.name for x in p('/sys/module').iterdir()) if p('/sys/module').is_dir() else []
dt_model=(read('/proc/device-tree/model') or '').replace('\0','').strip()
dt_compatible=[x.decode('utf-8','replace') for x in (readb('/proc/device-tree/compatible') or b'').split(b'\0') if x]
cmdline=(read('/proc/cmdline') or '').split()
props=dict(x.split('=',1) for x in cmdline if '=' in x)
bootloader=props.get('androidboot.bootloader')
interfaces=[]
for item in p('/sys/class/net').glob('*'):
 interfaces.append({'name':item.name,'operstate':read(str(item/'operstate'),128),
  'carrier':read(str(item/'carrier'),128)})
network_ready=any(x['name']=='wlan0' and x['operstate']=='up' and x['carrier']=='1' for x in interfaces)
process_comms=[read(str(proc/'comm'),256) for proc in p('/proc').glob('[0-9]*')]
hyprland='Hyprland' in process_comms
pi_running='pi' in process_comms
tmux_server=any(isinstance(x,str) and x.startswith('tmux') for x in process_comms)
ttyd_running='ttyd' in process_comms
pi_ready=pi_running and tmux_server
slot_processing=[]
if isinstance(raw_slots,list):
 slot_processing=[x.get('is_processing') for x in raw_slots if isinstance(x,dict)]
slots_valid=(isinstance(raw_slots,list) and len(raw_slots)>0 and
 len(slot_processing)==len(raw_slots) and
 all(type(x) is bool for x in slot_processing))
idle=bool(slots_valid and all(x is False for x in slot_processing))
slots_state={'count':len(raw_slots) if isinstance(raw_slots,list) else 0,
 'processing':[x for x in slot_processing if type(x) is bool]}
safe_health={'status':health.get('status')} if isinstance(health,dict) else {'status':None}
def read_int(name):
 value=read(name,128)
 try:return int(value.strip()) if value is not None else None
 except (ValueError,AttributeError):return None
battery_status=read('/sys/class/power_supply/battery/status',128)
battery_capacity=read_int('/sys/class/power_supply/battery/capacity')
battery_temp_raw=read_int('/sys/class/power_supply/battery/temp')
battery_temp=(round(battery_temp_raw/10,1) if battery_temp_raw is not None else None)
thermal_zones=sorted(p('/sys/class/thermal').glob('thermal_zone*'))
thermal_raw=[]
for zone in thermal_zones:
 value=read_int(str(zone/'temp'))
 thermal_raw.append(value)
thermal_valid=bool(thermal_zones) and all(value is not None for value in thermal_raw)
thermal_max=(round(max(thermal_raw)/1000,1) if thermal_valid else None)
power_state={'battery_status':battery_status,'battery_capacity_percent':battery_capacity,
 'battery_temperature_celsius':battery_temp,'thermal_zone_count':len(thermal_zones),
 'thermal_all_readable':thermal_valid,'thermal_max_temperature_celsius':thermal_max}
state=dict(boot_id=read('/proc/sys/kernel/random/boot_id'),
 uptime_seconds=float((read('/proc/uptime') or '0').split()[0]),pid1=read('/proc/1/comm'),
 kernel_release=read('/proc/sys/kernel/osrelease'),
 gnu_build_id=gnu_build_id(readb('/sys/kernel/notes')),
 boot_reset_first_record=records[0] if records else None,
 native_ready=p('/run/native-ready').exists(),persistent_ready=persistent_summary,
 health=safe_health,slots=slots_state,assistant_idle=idle,serious_fault=None if log is None else bool(re.search(
  r'Kernel panic|Oops:|BUG:|Unable to handle kernel|general protection fault|Out of memory:|oom-kill|Call trace:',log,re.I)),
 loaded_modules=sorted(loaded_modules),kernel_components=components,
 hyprland_running=hyprland,pi_process_running=pi_running,
 tmux_server_running=tmux_server,ttyd_running=ttyd_running,pi_assistant_ready=pi_ready,
 network_state={'interfaces':interfaces,'ready':network_ready},
 power_state=power_state,
 device_tree_model=dt_model,device_tree_compatible=dt_compatible,
 boot_model=props.get('androidboot.em.model'),boot_hardware=props.get('androidboot.hardware'),
 bootloader_model_match=(bootloader.startswith('S901B') if bootloader else None))
print(json.dumps(state))
'''


def render_snapshot_script():
    return SNAPSHOT.replace('__EXPECTED_PERSISTENT_UUID__', repr(EXPECTED_PERSISTENT_UUID))


def snapshot(project_root=ROOT):
    result = run_trusted_remote('usb', None,
                                'python3 -c '+shlex.quote(render_snapshot_script()),
                                timeout=12, project_root=project_root)
    if result.returncode:
        raise RuntimeError('SSH snapshot unavailable')
    return json.loads(result.stdout)


def save(name, value):
    durable_json(OUT/name, value)


def require(condition, message):
    if not condition:
        raise ObserverError(message)


def validate_flash_receipt(flash):
    require(isinstance(flash, dict), 'flash receipt must be a JSON object')
    require(flash.get('mode') == 'flash', "flash receipt mode must be 'flash'")
    require(flash.get('partition_written') == EXPECTED_TARGET,
            'flash receipt target must be RECOVERY')
    require(flash.get('before_sha256') == EXPECTED_BASE_SHA256,
            'flash receipt baseline hash mismatch')
    require(flash.get('readback_sha256') == EXPECTED_FLASH_SHA256,
            'flash receipt candidate hash mismatch')
    require(flash.get('bytes') == EXPECTED_FLASH_BYTES,
            'flash receipt byte count mismatch')
    require(flash.get('reboot_performed') is False,
            'flash receipt must show that deployment did not reboot')


def ready_value(value):
    return (isinstance(value, dict) and value.get('ready') is True and
            value.get('mount_ready') is True)


def summarize_slots(payload):
    """Keep only the slot count and explicit busy booleans from /slots."""
    if not isinstance(payload, list):
        return {'count': 0, 'processing': []}, False
    processing = [slot.get('is_processing') for slot in payload if isinstance(slot, dict)]
    valid = (bool(payload) and len(processing) == len(payload) and
             all(type(value) is bool for value in processing))
    return ({'count': len(payload), 'processing': processing},
            bool(valid and all(value is False for value in processing)))


def recovery_record(value):
    return (isinstance(value, str) and ' / R / ' in value and
            'INFORM3(12345674)' in value and ' > RECOVERY >' in value and
            not re.search(r'PANIC|WDOG|\bKP\b', value))


def parse_gnu_build_id(notes):
    """Extract the GNU type-3 build ID from the ELF note stream."""
    if not isinstance(notes, bytes):
        return None
    offset = 0
    while offset + 12 <= len(notes):
        namesz, descsz, note_type = struct.unpack_from('<III', notes, offset)
        offset += 12
        name_end = offset + namesz
        name_padded_end = offset + ((namesz + 3) & ~3)
        desc_end = name_padded_end + descsz
        next_offset = name_padded_end + ((descsz + 3) & ~3)
        if name_end > len(notes) or desc_end > len(notes) or next_offset > len(notes):
            return None
        if notes[offset:name_end].rstrip(b'\0') == b'GNU' and note_type == 3:
            return notes[name_padded_end:desc_end].hex()
        offset = next_offset
    return None


def target_identity_valid(state):
    model = state.get('device_tree_model')
    compatible = state.get('device_tree_compatible')
    if not isinstance(model, str) or not model.strip() or not isinstance(compatible, list):
        return False
    compatible = [value.lower() for value in compatible if isinstance(value, str)]
    samsung_model = 'samsung' in model.lower() and 'r0s' in model.lower()
    soc_compatible = any('s5e9925' in value for value in compatible)
    bootloader_match = state.get('bootloader_model_match')
    return (samsung_model and soc_compatible and
            state.get('boot_model') == EXPECTED_BOOT_MODEL and
            state.get('boot_hardware') == EXPECTED_BOOT_HARDWARE and
            bootloader_match is True)


def power_state_valid(power):
    return (isinstance(power, dict) and
            power.get('battery_status') in ('Charging', 'Full') and
            type(power.get('battery_capacity_percent')) is int and
            power['battery_capacity_percent'] >= 60 and
            type(power.get('battery_temperature_celsius')) in (int, float) and
            power['battery_temperature_celsius'] < 42 and
            power.get('thermal_all_readable') is True and
            type(power.get('thermal_zone_count')) is int and power['thermal_zone_count'] > 0 and
            type(power.get('thermal_max_temperature_celsius')) in (int, float) and
            power['thermal_max_temperature_celsius'] < 65)


def network_state_valid(network):
    return (isinstance(network, dict) and network.get('ready') is True and
            isinstance(network.get('interfaces'), list) and
            any(isinstance(interface, dict) and interface.get('name') == 'wlan0' and
                interface.get('operstate') == 'up' and interface.get('carrier') == '1'
                for interface in network['interfaces']))


def validate_snapshot(state, *, post_reboot):
    require(isinstance(state, dict), 'device snapshot must be an object')
    require(isinstance(state.get('boot_id'), str) and bool(state.get('boot_id')),
            'device boot ID is unavailable')
    require(state.get('pid1') == 'native-guardian', 'native guardian is not PID 1')
    require(state.get('native_ready') is True and ready_value(state.get('persistent_ready')),
            'native readiness is not established')
    require(isinstance(state.get('health'), dict) and state['health'].get('status') == 'ok',
            'assistant health endpoint is not healthy')
    slots = state.get('slots')
    require(isinstance(slots, dict) and type(slots.get('count')) is int and
            slots['count'] > 0 and isinstance(slots.get('processing'), list) and
            len(slots['processing']) == slots['count'] and
            all(type(value) is bool and value is False for value in slots['processing']) and
            state.get('assistant_idle') is True,
            'assistant idleness is not established by a nonempty idle /slots response')
    require(state.get('serious_fault') is False,
            'serious-fault status is unknown or a serious fault was recorded')
    loaded_value = state.get('loaded_modules')
    require(isinstance(loaded_value, list) and
            all(isinstance(value, str) for value in loaded_value),
            'loaded module inventory is unavailable')
    loaded = set(loaded_value)
    missing = sorted(set(REQUIRED_MODULES) - loaded)
    require(not missing, 'required loaded modules missing: '+','.join(missing))
    component_value = state.get('kernel_components')
    require(isinstance(component_value, list) and
            all(isinstance(value, str) for value in component_value),
            'kernel component inventory is unavailable')
    components = set(component_value)
    missing = sorted(set(REQUIRED_HCI_COMPONENTS) - components)
    require(not missing, 'required HCI kernel components missing: '+','.join(missing))
    require(state.get('hyprland_running') is True and
            state.get('pi_process_running') is True and
            state.get('tmux_server_running') is True and
            state.get('ttyd_running') is True and
            state.get('pi_assistant_ready') is True,
            'Hyprland or Pi assistant readiness is missing')
    require(network_state_valid(state.get('network_state')),
            'redacted network readiness is missing')
    require(power_state_valid(state.get('power_state')),
            'battery or thermal safety gate is not satisfied')
    require(target_identity_valid(state), 'device-tree/boot properties are not the Samsung r0s target')
    uptime = state.get('uptime_seconds')
    require(type(uptime) in (int, float) and uptime >= MIN_UPTIME_SECONDS,
            'device uptime is below 180 seconds')
    if post_reboot:
        require(state.get('gnu_build_id') == EXPECTED_GNU_BUILD_ID,
                'running GNU build ID does not match the HCI candidate')
        require(isinstance(state.get('kernel_release'), str) and
                bool(state.get('kernel_release').strip()),
                'uname kernel release was not captured separately')
        require(recovery_record(state.get('boot_reset_first_record')),
                'latest boot record does not prove the RECOVERY target')


def durable_json(path, value):
    data = (json.dumps(value, sort_keys=True, indent=2) + '\n').encode()
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_CLOEXEC', 0)
    flags |= getattr(os, 'O_NOFOLLOW', 0)
    fd = os.open(path, flags, 0o600)
    try:
        offset = 0
        while offset < len(data):
            count = os.write(fd, data[offset:])
            if count <= 0:
                raise OSError('durable write made no progress')
            offset += count
        os.fsync(fd)
    finally:
        os.close(fd)
    dfd = os.open(path.parent, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0)
                  | getattr(os, 'O_NOFOLLOW', 0))
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)


def ensure_private_directory(path):
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = path.lstat()
    require(path.is_dir() and not path.is_symlink(), 'state path is not a real directory')
    require(info.st_uid == os.geteuid() and info.st_mode & 0o077 == 0,
            'state directory must be private and owned by this user')


def require_unused_trial_marker(path):
    """Fail closed if this trial already attempted the irreversible request."""
    try:
        os.lstat(path)
    except FileNotFoundError:
        return
    raise FileExistsError(f'trial one-shot marker already exists: {path.name}')


def read_json(path):
    try:
        info = path.lstat()
        require(path.is_file() and not path.is_symlink() and info.st_size <= 1048576,
                'receipt must be a bounded regular file')
        require(info.st_uid == os.geteuid() and info.st_mode & 0o077 == 0,
                'receipt must be private and owned by this user')
        value = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as error:
        raise ObserverError(f'cannot read receipt: {error}') from error
    require(isinstance(value, dict), 'receipt must contain a JSON object')
    return value


def observer_receipt_valid(value):
    observed = value.get('observed_seconds') if isinstance(value, dict) else None
    uptime = value.get('continuous_uptime_seconds') if isinstance(value, dict) else None
    return (isinstance(value, dict) and
            value.get('schema') == 's22-hci-recovery-observer/v1' and
            value.get('status') == 'completed' and
            value.get('target') == EXPECTED_TARGET and
            value.get('candidate_sha256') == EXPECTED_FLASH_SHA256 and
            value.get('gnu_build_id') == EXPECTED_GNU_BUILD_ID and
            isinstance(value.get('kernel_release'), str) and bool(value.get('kernel_release').strip()) and
            value.get('actual_mode') == 'RECOVERY' and
            isinstance(value.get('boot_id'), str) and bool(value.get('boot_id')) and
            isinstance(value.get('baseline_boot_id'), str) and bool(value.get('baseline_boot_id')) and
            value.get('boot_id') != value.get('baseline_boot_id') and
            type(value.get('reboot_requests')) is int and value.get('reboot_requests') == 1 and
            type(observed) in (int, float) and OBSERVATION_SECONDS <= observed <= OBSERVATION_SECONDS + 1 and
            type(uptime) in (int, float) and uptime >= MIN_UPTIME_SECONDS and
            value.get('readiness') is True and value.get('assistant_idle') is True and
            value.get('no_serious_fault') is True and
            value.get('required_modules_ready') is True and
            value.get('hci_components_ready') is True and
            value.get('hyprland_running') is True and
            value.get('pi_assistant_ready') is True and
            value.get('network_ready') is True and
            value.get('device_target_valid') is True and
            value.get('power_ready') is True and
            value.get('baseline_power_ready') is True and
            value.get('baseline_readiness') is True and
            value.get('baseline_assistant_idle') is True and
            value.get('baseline_no_serious_fault') is True and
            value.get('baseline_modules_ready') is True and
            value.get('baseline_hci_components_ready') is True and
            value.get('baseline_desktop_ready') is True and
            value.get('baseline_pi_process_running') is True and
            value.get('baseline_tmux_server_running') is True and
            value.get('baseline_ttyd_running') is True and
            value.get('postboot_pi_process_running') is True and
            value.get('postboot_tmux_server_running') is True and
            value.get('postboot_ttyd_running') is True and
            value.get('baseline_network_ready') is True and
            value.get('baseline_device_target_valid') is True and
            value.get('reboot_request_outcome') in ('ACKNOWLEDGED', 'UNKNOWN') and
            value.get('recovery_sha256') == EXPECTED_FLASH_SHA256 and
            value.get('recovery_sha256_after_boot') == EXPECTED_FLASH_SHA256)


def request_reboot_once(marker, result_path, runner=subprocess.run, project_root=ROOT):
    durable_json(marker, {'target': EXPECTED_TARGET, 'status': 'request_started',
                          'created_at': datetime.now(timezone.utc).isoformat(),
                          'retry_allowed': False})
    try:
        result = run_trusted_remote('usb', None, 's22-reboot recovery', runner=runner,
                                    timeout=15, project_root=project_root)
    except (subprocess.TimeoutExpired, OSError, ObserverError) as error:
        durable_json(result_path, {'status': 'unknown_disconnect_after_request',
                                   'outcome': 'UNKNOWN', 'retry_allowed': False,
                                   'error_type': type(error).__name__})
        return 'UNKNOWN'
    if result.returncode != 0:
        durable_json(result_path, {'status': 'unknown_disconnect_after_request',
                                   'outcome': 'UNKNOWN', 'returncode': result.returncode,
                                   'retry_allowed': False})
        return 'UNKNOWN'
    durable_json(result_path, {'status': 'request_returned', 'returncode': 0,
                               'retry_allowed': False})
    return 'ACKNOWLEDGED'


def approved_ssh_wrapper_sha256():
    policy_pattern = r"^APPROVED_SSH_WRAPPER_SHA256\s*=\s*'([0-9a-f]{64})'\s*$"
    try:
        source = (ROOT/'tools/hardware/deploy-audio-recovery.py').read_text(encoding='utf-8')
    except OSError as error:
        raise ObserverError('trusted deployer policy is unavailable') from error
    match = re.search(policy_pattern, source, re.M)
    require(match is not None, 'trusted deployer has no approved SSH wrapper digest')
    return match.group(1)


def validated_ssh_wrapper(project_root=ROOT):
    wrapper = project_root/'tools/s22-ssh'
    try:
        digest = hashlib.sha256(wrapper.read_bytes()).hexdigest()
    except OSError as error:
        raise ObserverError('trusted USB SSH wrapper is unavailable') from error
    require(digest == approved_ssh_wrapper_sha256(),
            'USB SSH wrapper does not match the approved deployer SHA-256')
    return wrapper


def _trusted_deployer():
    """Load the reviewed deployer's sealed-wrapper helpers, not caller code."""
    path = ROOT/'tools/hardware/deploy-audio-recovery.py'
    spec = importlib.util.spec_from_file_location('s22_reviewed_deployer', path)
    require(spec is not None and spec.loader is not None,
            'reviewed deployer wrapper helpers are unavailable')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_trusted_remote(transport, host, remote, *, runner=subprocess.run,
                       timeout=15, project_root=ROOT):
    """Use an opened, sealed SSH-wrapper snapshot and a trusted SSH PATH."""
    deployer = _trusted_deployer()
    if transport == 'usb':
        wrapper = project_root/'tools/s22-ssh'
        try:
            wrapper_fd = deployer.validate_approved_ssh_wrapper(wrapper)
        except (OSError, ValueError) as error:
            raise ObserverError('approved USB SSH wrapper could not be pinned') from error
        try:
            argv, environment = deployer.build_approved_ssh_invocation(
                wrapper_fd, wrapper, remote, project_root=project_root)
            return runner(argv, capture_output=True, text=True, timeout=timeout,
                          check=False, pass_fds=(wrapper_fd,), env=environment)
        finally:
            os.close(wrapper_fd)
    command = _ssh_transport(transport, host, remote, project_root)
    environment = os.environ.copy()
    environment.pop('BASH_ENV', None)
    environment.pop('ENV', None)
    try:
        environment['PATH'] = deployer.verified_ssh_path()
    except (OSError, ValueError) as error:
        raise ObserverError('trusted SSH executable path is unavailable') from error
    return runner(command, capture_output=True, text=True, timeout=timeout,
                  check=False, env=environment)


def _ssh_transport(transport, host, remote, project_root=ROOT):
    wrapper = validated_ssh_wrapper(project_root)
    if transport == 'usb':
        return [str(wrapper), remote]
    require(host and re.fullmatch(r'[A-Za-z0-9._:-]+', host),
            f'invalid {transport} SSH host')
    known_hosts = project_root/'evidence/native-linux-20260919/native-v2-known-hosts'
    alias = pinned_usb_host_key_alias(known_hosts, project_root)
    return [trusted_system_executable('ssh'), '-o', 'StrictHostKeyChecking=yes', '-o',
            'UserKnownHostsFile='+str(known_hosts), '-o', 'HostKeyAlias='+alias,
            '-o', 'ConnectTimeout=5',
            '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=3',
            'root@'+host, remote]


def trusted_system_executable(name):
    path = Path('/usr/bin')/name
    try:
        info = path.lstat()
    except OSError as error:
        raise ObserverError(f'trusted /usr/bin/{name} is unavailable') from error
    require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and
            (info.st_mode & 0o022) == 0 and (info.st_mode & 0o111) != 0,
            f'/usr/bin/{name} is not a protected root-owned executable')
    return str(path)


def pinned_usb_host_key_alias(known_hosts=None, project_root=ROOT):
    """Read the alias pinned by the USB SSH wrapper without returning it to receipts."""
    wrapper = validated_ssh_wrapper(project_root)
    known_hosts = known_hosts or project_root/'evidence/native-linux-20260919/native-v2-known-hosts'
    try:
        wrapper_text = wrapper.read_text(encoding='utf-8')
        pinned_info = known_hosts.lstat()
    except OSError as error:
        raise ObserverError('verified SSH host-key configuration is unavailable') from error
    require(stat.S_ISREG(pinned_info.st_mode) and not known_hosts.is_symlink(),
            'verified SSH host-key configuration is not a regular file')
    matches = re.findall(r'\broot@([A-Za-z0-9._:-]+)\s+"\$@"', wrapper_text)
    require(len(matches) == 1, 'USB SSH wrapper does not have one pinned endpoint')
    alias = matches[0]
    result = subprocess.run([trusted_system_executable('ssh-keygen'), '-F', alias,
                             '-f', str(known_hosts)], capture_output=True, text=True,
                            timeout=5, check=False)
    require(result.returncode == 0 and bool(result.stdout.strip()),
            'USB wrapper endpoint has no matching pinned host key')
    return alias


def discover_wifi_host(runner=subprocess.run, project_root=ROOT):
    """Read wlan0 IPv4 over verified USB, then keep it only in process memory."""
    result = run_trusted_remote('usb', None, 'python3 -c '+shlex.quote(WIFI_ADDRESS),
                                runner=runner, timeout=10, project_root=project_root)
    require(result.returncode == 0, 'cannot discover wlan0 SSH address over USB')
    try:
        address = ipaddress.ip_address(result.stdout.strip())
    except ValueError as error:
        raise ObserverError('wlan0 returned an invalid address') from error
    require(address.is_private and not address.is_loopback and not address.is_link_local and
            not address.is_unspecified and not address.is_multicast and not address.is_reserved,
            'wlan0 address is not a private unicast address')
    return str(address)


def snapshot_over(transport='usb', host=None, runner=subprocess.run, project_root=ROOT):
    result = run_trusted_remote(transport, host,
                                'python3 -c '+shlex.quote(render_snapshot_script()),
                                runner=runner, timeout=15, project_root=project_root)
    if result.returncode:
        raise ObserverError(f'{transport} reconnect unavailable')
    try:
        state = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise ObserverError(f'{transport} returned invalid snapshot JSON') from error
    require(isinstance(state, dict), 'snapshot must be a JSON object')
    return state


def reconnect_snapshot(wifi_host=None, tailscale_host=None, runner=subprocess.run,
                       project_root=ROOT):
    failures = []
    routes = [('usb', None)]
    if wifi_host:
        routes.append(('wifi', wifi_host))
    if tailscale_host:
        routes.append(('tailscale', tailscale_host))
    for route, host in routes:
        try:
            return snapshot_over(route, host, runner, project_root), route
        except (ObserverError, OSError, subprocess.TimeoutExpired) as error:
            failures.append(route)
    raise ObserverError('no configured USB/WiFi/Tailscale route reconnected: '+','.join(failures))


def redacted_host_enumeration(runner=subprocess.run):
    """Return counts and interface states only; omit addresses and host identities."""
    found = {'usb_device_count': None, 'interfaces': None, 'wifi_devices': None,
             'tailscale': None}
    if shutil.which('lsusb'):
        result = runner(['lsusb'], capture_output=True, text=True, timeout=5, check=False)
        if result.returncode == 0:
            found['usb_device_count'] = len(result.stdout.splitlines())
    if shutil.which('ip'):
        result = runner(['ip', '-brief', 'link'], capture_output=True, text=True,
                        timeout=5, check=False)
        if result.returncode == 0:
            found['interfaces'] = [{'name': cols[0].rstrip(':'), 'state': cols[1]}
                                   for line in result.stdout.splitlines()
                                   if len(cols := line.split()) >= 2]
    if shutil.which('nmcli'):
        result = runner(['nmcli', '-t', '-f', 'DEVICE,TYPE,STATE', 'device', 'status'],
                        capture_output=True, text=True, timeout=5, check=False)
        if result.returncode == 0:
            found['wifi_devices'] = [{'device': fields[0], 'state': fields[2]}
                                     for line in result.stdout.splitlines()
                                     if len(fields := line.split(':')) >= 3 and fields[1] == 'wifi']
    if shutil.which('tailscale'):
        result = runner(['tailscale', 'status', '--json'], capture_output=True, text=True,
                        timeout=5, check=False)
        if result.returncode == 0:
            try:
                state = json.loads(result.stdout)
                peers = state.get('Peer', {})
                found['tailscale'] = {'backend_state': state.get('BackendState'),
                                      'peer_count': len(peers),
                                      'online_peers': sum(bool(p.get('Online')) for p in peers.values()
                                                          if isinstance(p, dict))}
            except (json.JSONDecodeError, AttributeError):
                found['tailscale'] = {'status_json_valid': False}
    return found


def candidate_hash(transport='usb', host=None, runner=subprocess.run, project_root=ROOT):
    result = run_trusted_remote(transport, host,
                                'sha256sum /dev/block/by-name/recovery', runner=runner,
                                timeout=60, project_root=project_root)
    require(result.returncode == 0, 'cannot read live RECOVERY hash')
    match = re.match(r'^([0-9a-f]{64})\s+', result.stdout)
    require(match is not None and match.group(1) == EXPECTED_FLASH_SHA256,
            'live RECOVERY hash does not match the candidate')
    return match.group(1)


def run_reboot_observer(state_root, name, flash, wifi_host=None, tailscale_host=None,
                        observation_seconds=OBSERVATION_SECONDS, sample_interval=5,
                        runner=subprocess.run, clock=time.monotonic, sleep=time.sleep,
                        enumerate_host=redacted_host_enumeration, project_root=ROOT):
    ensure_private_directory(TRIAL_STATE_ROOT)
    marker = TRIAL_STATE_ROOT/'hci-candidate-reboot-attempted.json'
    require_unused_trial_marker(marker)
    validate_flash_receipt(flash)
    before = snapshot_over('usb', runner=runner, project_root=project_root)
    validate_snapshot(before, post_reboot=False)
    baseline_hash = candidate_hash(runner=runner, project_root=project_root)
    ensure_private_directory(state_root)
    out = state_root/name
    out.mkdir(mode=0o700)
    durable_json(out/'before.json', before)
    durable_json(out/'baseline-recovery-hash.json', {'sha256': baseline_hash})
    durable_json(out/'host-enumeration-baseline.json', enumerate_host(runner=runner))
    if not wifi_host:
        try:
            wifi_host = discover_wifi_host(runner=runner, project_root=project_root)
        except (ObserverError, OSError, subprocess.TimeoutExpired):
            wifi_host = None
    started = clock()
    deadline = started + observation_seconds

    def bounded_runner(command, **kwargs):
        remaining = deadline-clock()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(command, kwargs.get('timeout', 0))
        requested_timeout = kwargs.get('timeout')
        kwargs['timeout'] = (remaining if requested_timeout is None else
                             min(requested_timeout, remaining))
        return runner(command, **kwargs)

    request_outcome = request_reboot_once(marker, out/'request-result.json',
                                           runner=bounded_runner, project_root=project_root)

    prior_boot = before.get('boot_id')
    candidate_boot = None
    accepted = None
    verified_hash = None
    samples = 0
    last_valid_elapsed = -1
    while clock() < deadline:
        samples += 1
        elapsed = round(clock()-started, 2)
        try:
            host_observation = enumerate_host(runner=bounded_runner)
        except (OSError, subprocess.TimeoutExpired):
            host_observation = {'usb_device_count': None, 'interfaces': None,
                                'wifi_devices': None, 'tailscale': None,
                                'enumeration_available': False}
        durable_json(out/('host-window-%03d.json' % samples),
                     {'elapsed_seconds': elapsed, 'redacted': host_observation})
        if clock() >= deadline:
            break
        try:
            state, transport = reconnect_snapshot(wifi_host, tailscale_host, runner=bounded_runner,
                                                  project_root=project_root)
            route_host = (wifi_host if transport == 'wifi' else
                          tailscale_host if transport == 'tailscale' else None)
            state['host_elapsed_seconds'] = elapsed
            state['transport'] = transport
            state['host_network_redacted'] = host_observation
            durable_json(out/('sample-%03d.json' % samples), state)
            boot_id = state.get('boot_id')
            if candidate_boot is None and boot_id and boot_id != prior_boot:
                candidate_boot = boot_id
            if candidate_boot is not None:
                require(boot_id == candidate_boot, 'boot ID changed during observation')
                validate_snapshot(state, post_reboot=True)
                if verified_hash is None:
                    verified_hash = candidate_hash(transport, route_host, bounded_runner, project_root)
                accepted = state
                last_valid_elapsed = elapsed
        except (ObserverError, OSError, subprocess.TimeoutExpired, ValueError) as error:
            accepted = None
            print(json.dumps({'elapsed_seconds': round(clock()-started, 2),
                              'observation_error': type(error).__name__}), flush=True)
        sleep(min(sample_interval, max(0, deadline-clock())))

    observed = round(clock()-started, 2)
    success = (candidate_boot is not None and accepted is not None and
               observed >= observation_seconds and
               last_valid_elapsed >= observation_seconds-sample_interval and
               accepted.get('uptime_seconds', 0) >= MIN_UPTIME_SECONDS and
               verified_hash == EXPECTED_FLASH_SHA256)
    receipt = {'schema': 's22-hci-recovery-observer/v1',
               'status': 'completed' if success else 'incomplete',
               'target': EXPECTED_TARGET, 'candidate_sha256': EXPECTED_FLASH_SHA256,
               'gnu_build_id': EXPECTED_GNU_BUILD_ID,
               'kernel_release': accepted.get('kernel_release') if accepted else None,
               'actual_mode': 'RECOVERY' if candidate_boot else None,
               'boot_id': candidate_boot, 'reboot_requests': 1,
               'baseline_boot_id': prior_boot,
               'reboot_request_outcome': request_outcome,
               'wifi_route_available': bool(wifi_host),
               'observed_seconds': observed,
               'continuous_uptime_seconds': accepted.get('uptime_seconds') if accepted else None,
               'readiness': bool(accepted and accepted.get('native_ready') and
                                 ready_value(accepted.get('persistent_ready'))),
               'assistant_idle': bool(accepted and accepted.get('assistant_idle') is True),
               'baseline_readiness': bool(before.get('native_ready') and
                                          ready_value(before.get('persistent_ready'))),
               'baseline_assistant_idle': before.get('assistant_idle') is True,
               'baseline_no_serious_fault': before.get('serious_fault') is False,
               'no_serious_fault': bool(accepted and accepted.get('serious_fault') is False),
               'required_modules_ready': bool(accepted and
                    set(REQUIRED_MODULES).issubset(set(accepted.get('loaded_modules', [])))),
               'hci_components_ready': bool(accepted and
                    set(REQUIRED_HCI_COMPONENTS).issubset(set(accepted.get('kernel_components', [])))),
               'hyprland_running': bool(accepted and accepted.get('hyprland_running') is True),
               'pi_assistant_ready': bool(accepted and accepted.get('pi_assistant_ready') is True),
               'network_ready': bool(accepted and
                    network_state_valid(accepted.get('network_state'))),
               'device_target_valid': bool(accepted and target_identity_valid(accepted)),
               'power_ready': bool(accepted and power_state_valid(accepted.get('power_state'))),
               'baseline_power_ready': power_state_valid(before.get('power_state')),
               'baseline_modules_ready': set(REQUIRED_MODULES).issubset(set(before.get('loaded_modules', []))),
               'baseline_hci_components_ready': set(REQUIRED_HCI_COMPONENTS).issubset(set(before.get('kernel_components', []))),
               'baseline_desktop_ready': before.get('hyprland_running') is True and before.get('pi_assistant_ready') is True,
               'baseline_network_ready': network_state_valid(before.get('network_state')),
               'baseline_device_target_valid': target_identity_valid(before),
               'baseline_power_state': before.get('power_state'),
               'postboot_power_state': accepted.get('power_state') if accepted else None,
               'baseline_network_state': before.get('network_state'),
               'postboot_network_state': accepted.get('network_state') if accepted else None,
               'baseline_loaded_modules': before.get('loaded_modules'),
               'postboot_loaded_modules': accepted.get('loaded_modules') if accepted else None,
               'baseline_kernel_components': before.get('kernel_components'),
               'postboot_kernel_components': accepted.get('kernel_components') if accepted else None,
               'baseline_hyprland_running': before.get('hyprland_running') is True,
               'baseline_pi_assistant_ready': before.get('pi_assistant_ready') is True,
               'postboot_hyprland_running': accepted.get('hyprland_running') is True if accepted else False,
               'postboot_pi_assistant_ready': accepted.get('pi_assistant_ready') is True if accepted else False,
               'baseline_pi_process_running': before.get('pi_process_running') is True,
               'baseline_tmux_server_running': before.get('tmux_server_running') is True,
               'baseline_ttyd_running': before.get('ttyd_running') is True,
               'postboot_pi_process_running': accepted.get('pi_process_running') is True if accepted else False,
               'postboot_tmux_server_running': accepted.get('tmux_server_running') is True if accepted else False,
               'postboot_ttyd_running': accepted.get('ttyd_running') is True if accepted else False,
               'baseline_recovery_sha256': baseline_hash,
               'recovery_sha256': verified_hash,
               'recovery_sha256_after_boot': verified_hash,
               'samples': samples}
    durable_json(out/'result.json', receipt)
    require(success, '600-second observation did not prove candidate RECOVERY readiness')
    return receipt


def run_hci_once(state_root, name, observer, runner=subprocess.run,
                 route_selector=reconnect_snapshot, hasher=candidate_hash,
                 wifi_host=None, tailscale_host=None, project_root=ROOT):
    ensure_private_directory(TRIAL_STATE_ROOT)
    marker = TRIAL_STATE_ROOT/'hci-candidate-socket-attempted.json'
    require_unused_trial_marker(marker)
    require(observer_receipt_valid(observer),
            'hci-once requires a valid completed observer receipt')
    current, transport = route_selector(wifi_host, tailscale_host, runner=runner,
                                        project_root=project_root)
    route_host = (wifi_host if transport == 'wifi' else
                  tailscale_host if transport == 'tailscale' else None)
    validate_snapshot(current, post_reboot=True)
    require(current.get('boot_id') == observer.get('boot_id'),
            'observer receipt is stale: device boot ID changed')
    require(current.get('gnu_build_id') == EXPECTED_GNU_BUILD_ID,
            'running GNU build ID changed since observation')
    recovery_sha256 = hasher(transport, route_host, runner, project_root)
    require(recovery_sha256 == EXPECTED_FLASH_SHA256,
            'live RECOVERY hash changed since observation')
    ensure_private_directory(state_root)
    out = state_root/(name+'-hci')
    out.mkdir(mode=0o700)
    durable_json(out/'preflight.json', {
        'boot_id': current.get('boot_id'), 'gnu_build_id': current.get('gnu_build_id'),
        'kernel_release': current.get('kernel_release'),
        'transport': transport,
        'recovery_sha256': recovery_sha256,
        'device_target_valid': target_identity_valid(current),
        'required_modules_ready': True, 'hci_components_ready': True,
        'hyprland_running': True, 'pi_assistant_ready': True,
        'network_state': current.get('network_state'),
        'power_state': current.get('power_state'),
    })
    durable_json(marker, {'status': 'request_started', 'retry_allowed': False,
                          'observer_boot_id': observer.get('boot_id'),
                          'current_boot_id': current.get('boot_id'),
                          'recovery_sha256': recovery_sha256})
    probe = "import json,socket; s=socket.socket(socket.AF_BLUETOOTH,socket.SOCK_RAW,socket.BTPROTO_HCI); s.close(); print(json.dumps({'socket_created_and_closed':True,'attached':False,'scan_sent':False,'pairing_started':False}))"
    try:
        result = run_trusted_remote(transport, route_host,
                                    'python3 -c '+shlex.quote(probe), runner=runner,
                                    timeout=15, project_root=project_root)
    except (subprocess.TimeoutExpired, OSError, ObserverError) as error:
        durable_json(out/'result.json', {'status': 'unknown_disconnect_after_request',
                                         'outcome': 'UNKNOWN', 'retry_allowed': False,
                                         'error_type': type(error).__name__})
        raise ObserverError('HCI request disconnected; outcome UNKNOWN; no retry')
    expected_probe = {'socket_created_and_closed': True, 'attached': False,
                      'scan_sent': False, 'pairing_started': False}
    probe_receipt = None
    if result.returncode == 0:
        try:
            def unique_json_object(pairs):
                value = {}
                for key, item in pairs:
                    if key in value:
                        raise ValueError('duplicate HCI receipt key')
                    value[key] = item
                return value
            probe_receipt = json.loads(result.stdout, object_pairs_hook=unique_json_object)
        except (json.JSONDecodeError, TypeError):
            probe_receipt = None
        except ValueError:
            probe_receipt = None
    verified_probe = (isinstance(probe_receipt, dict) and
                      set(probe_receipt) == set(expected_probe) and
                      all(type(probe_receipt[key]) is bool and
                          probe_receipt[key] is expected_probe[key]
                          for key in expected_probe))
    unknown_transport = result.returncode == 255 or not verified_probe and result.returncode == 0
    receipt = {'schema': 's22-hci-socket-smoke/v1',
               'status': ('completed' if verified_probe else
                          'unknown_transport_after_request' if unknown_transport else 'failed'),
               'outcome': ('SUCCESS' if verified_probe else
                           'UNKNOWN' if unknown_transport else 'FAILED'),
               'returncode': result.returncode, 'remote_receipt_valid': verified_probe,
               'retry_allowed': False, 'attached': False,
               'scan_sent': False, 'pairing_started': False}
    durable_json(out/'result.json', receipt)
    require(verified_probe, 'HCI socket result was not verified; no retry')
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('reboot-observe', 'hci-once'), required=True)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--name', default='hci-candidate-20260924')
    parser.add_argument('--project-root', type=Path, default=ROOT,
                        help='trusted project root containing the pinned SSH wrapper and deployer')
    parser.add_argument('--state-root', type=Path, default=OUT)
    parser.add_argument('--flash-receipt', type=Path, default=(RIG_HOME/
                        '.local/state/s22-hci-trial-20260924/receipts/hci-recovery-forward-flash.json'))
    parser.add_argument('--observer-receipt', type=Path)
    parser.add_argument('--wifi-host', default=os.environ.get('S22_WIFI_SSH_HOST'))
    parser.add_argument('--tailscale-host', default=os.environ.get('S22_TAILSCALE_SSH_HOST'))
    args = parser.parse_args(argv)
    if re.fullmatch(r'[a-z0-9-]+', args.name) is None:
        parser.error('use a unique lowercase evidence name')
    if not args.execute:
        print(json.dumps({'plan_only': True, 'mode': args.mode,
                          'target': EXPECTED_TARGET,
                          'project_root_configured': args.project_root.is_dir(),
                          'observation_seconds': OBSERVATION_SECONDS if args.mode == 'reboot-observe' else None,
                          'retry_policy': 'request disconnect is UNKNOWN; never retry'}, indent=2))
        return 0
    os.umask(0o077)
    try:
        if args.mode == 'reboot-observe':
            result = run_reboot_observer(args.state_root, args.name,
                                         read_json(args.flash_receipt), args.wifi_host,
                                         args.tailscale_host,
                                         project_root=args.project_root)
        else:
            require(args.observer_receipt is not None,
                    'hci-once requires --observer-receipt')
            result = run_hci_once(args.state_root, args.name,
                                  read_json(args.observer_receipt),
                                  wifi_host=args.wifi_host,
                                  tailscale_host=args.tailscale_host,
                                  project_root=args.project_root)
    except (ObserverError, OSError, subprocess.TimeoutExpired, ValueError) as error:
        print(f'observer: {error}', file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
