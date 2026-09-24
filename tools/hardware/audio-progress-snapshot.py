#!/usr/bin/env python3
"""Capture a bounded, synchronized read-only ABOX/RDMA2 observation.

The fixed ALSA/ABOX observations come from the pinned S5E9925 stack. Optional
ASoC debugfs nodes use the kernel's standard DPCM/DAI/DAPM debugfs ABI. RDMA2
register reads remain opt-in and are individually metadata-checked; there are
no register writes or broad register dumps. Missing debugfs nodes are reported
as unavailable rather than guessed or treated as evidence of a stopped route.
"""
import argparse
import errno
import json
import math
import os
from pathlib import Path
import re
import time

MAP = Path('/sys/kernel/debug/regmap/18c50000.abox')
ABOX = Path('/sys/bus/platform/devices/18c50000.abox')
ASOC = Path('/sys/kernel/debug/asoc')
ASOC_CARD = ASOC / 'Rainbow-Prince'
PCM = Path('/proc/asound/card0/pcm2p/sub0')
CLK_DEBUG = Path('/sys/kernel/debug/clk')
TARGETS = (0x1200, 0x1230, 0x1238)
MAX_CLOCK_DIRS = 512
MAX_CLOCK_MATCHES = 12
CLOCK_TERMS = ('aud', 'abox', 'sclk')
DAPM_WIDGETS = ('RDMA2', 'UAIF1', 'SPEAKER', 'RECEIVER')


def plans(ranges_text, access_text):
    ranges = []
    previous = -1
    for line in ranges_text.splitlines():
        m = re.fullmatch(r'([0-9a-f]+)-([0-9a-f]+)', line)
        if not m:
            raise ValueError('invalid range metadata')
        a, b = [int(x, 16) for x in m.groups()]
        if a % 4 or b % 4 or b < a or a <= previous:
            raise ValueError('unordered or unaligned ranges')
        ranges.append((a, b))
        previous = b

    flags = {}
    widths = set()
    for line in access_text.splitlines():
        m = re.fullmatch(r'([0-9a-f]+): ([yn]) ([yn]) ([yn]) ([yn])', line)
        if not m:
            raise ValueError('invalid access metadata')
        address = int(m[1], 16)
        if address in flags:
            raise ValueError('duplicate register')
        flags[address] = m.groups()[1:]
        widths.add(len(m[1]))
    if not ranges or len(widths) != 1:
        raise ValueError('missing/inconsistent register layout')
    width = widths.pop()
    if width not in (4, 5, 6):
        raise ValueError('unexpected address width')
    size = width + 8 + 3  # pinned config: val_bits32, stride4
    result = []
    for target in TARGETS:
        # CTRL is read with pread only, but normal driver updates make it
        # writable in metadata. The two status words are source-declared RO.
        expected = ('y', 'y', 'y', 'n') if target == 0x1200 else ('y', 'n', 'y', 'n')
        if flags.get(target) != expected:
            raise ValueError('target access metadata mismatch')
        index = 0
        for a, b in ranges:
            if a <= target <= b:
                index += (target - a) // 4
                break
            index += (b - a) // 4 + 1
        else:
            raise ValueError('target absent from printable map')
        result.append({'register': target, 'offset': index * size,
                       'length': size, 'width': width})
    return result


def decode_rdma2_ctrl(value):
    """Decode only the source-defined RDMA2 CTRL enable bit."""
    if not isinstance(value, int) or value < 0 or value > 0xffffffff:
        raise ValueError('invalid 32-bit CTRL value')
    return {'raw': value, 'enable': bool(value & 0x1)}


def bounded(path, limit):
    with open(path, 'r') as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError('oversized observation')
    return data


def timed_read(path, limit=4096, binary=False):
    """Read one fixed path with a per-source CLOCK_MONOTONIC interval."""
    started = time.monotonic_ns()
    try:
        with open(path, 'rb') as stream:
            raw = stream.read(limit + 1)
        if len(raw) > limit:
            return {'status': 'too_large', 'error': 'limit_exceeded',
                    'start_monotonic_ns': started,
                    'end_monotonic_ns': time.monotonic_ns()}
        value = raw.hex() if binary else raw.decode('utf-8', errors='replace').rstrip('\n')
        record = {'status': 'ok', 'value': value}
    except OSError as error:
        code = errno.errorcode.get(error.errno, 'EIO')
        status = 'not_present' if error.errno == errno.ENOENT else 'read_error'
        record = {'status': status, 'error_code': code}
    except (UnicodeError, ValueError):
        record = {'status': 'decode_error'}
    ended = time.monotonic_ns()
    record.update({'start_monotonic_ns': started, 'end_monotonic_ns': ended,
                   'duration_ns': max(0, ended - started)})
    return record


def timed_text(path, limit=4096):
    return timed_read(path, limit=limit)


def parse_alsa_status(text):
    """Extract only the standard ALSA status fields used for progress."""
    if not isinstance(text, str):
        return {}
    result = {}
    state = re.search(r'^state\s*:\s*([^\n]+)$', text, re.M)
    if state:
        result['state'] = state.group(1).strip()
    for name in ('hw_ptr', 'appl_ptr', 'delay', 'avail', 'avail_max'):
        matches = re.findall(r'^' + name + r'\s*:\s*(-?\d+)$', text, re.M)
        if len(matches) == 1:
            result[name] = int(matches[0])
    return result


def parse_hw_params(text):
    if not isinstance(text, str):
        return {}
    values = {}
    for key in ('format', 'subformat', 'access'):
        match = re.search(r'^' + key + r'\s*:\s*([^\n]+)$', text, re.M)
        if match:
            values[key] = match.group(1).strip()
    for key in ('channels', 'rate', 'period_size', 'buffer_size'):
        match = re.search(r'^' + key + r'\s*:\s*(\d+)', text, re.M)
        if match:
            values[key] = int(match.group(1))
    return values


def period_progress(samples):
    """Report period progress across contiguous observed samples, not an IRQ count."""
    if not isinstance(samples, list) or len(samples) > 256:
        return {'verified': False, 'periods_advanced': 0,
                'reason': 'invalid sample collection', 'irq_counter_available': False}
    points = []
    sequences = []
    period_sizes = set()
    gap_after_running = False
    for sample in samples:
        if not isinstance(sample, dict):
            if points:
                gap_after_running = True
            continue
        alsa = sample.get('alsa_counters')
        if not isinstance(alsa, dict) or alsa.get('state') != 'RUNNING':
            if points:
                gap_after_running = True
            continue
        if gap_after_running:
            return {'verified': False, 'periods_advanced': 0,
                    'reason': 'RUNNING observations separated by a missing or non-RUNNING sample',
                    'irq_counter_available': False}
        hw_ptr = alsa.get('hw_ptr')
        timestamp = sample.get('monotonic')
        hw_params = sample.get('hw_params_parsed')
        size = hw_params.get('period_size') if isinstance(hw_params, dict) else None
        sequence = sample.get('sequence')
        if (isinstance(hw_ptr, bool) or not isinstance(hw_ptr, int) or hw_ptr < 0
                or isinstance(timestamp, bool) or not isinstance(timestamp, (int, float))
                or isinstance(size, bool) or not isinstance(size, int) or size <= 0
                or ('sequence' in sample and
                    (isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0))):
            return {'verified': False, 'periods_advanced': 0,
                    'reason': 'invalid RUNNING sample', 'irq_counter_available': False}
        timestamp = float(timestamp)
        if not math.isfinite(timestamp):
            return {'verified': False, 'periods_advanced': 0,
                    'reason': 'invalid RUNNING sample', 'irq_counter_available': False}
        points.append((timestamp, hw_ptr))
        sequences.append(sequence if 'sequence' in sample else None)
        period_sizes.add(size)
    if len(period_sizes) != 1:
        return {'verified': False, 'periods_advanced': 0,
                'reason': 'period size unavailable or changed', 'irq_counter_available': False}
    if any(sequence is not None for sequence in sequences):
        if any(sequence is None for sequence in sequences):
            return {'verified': False, 'periods_advanced': 0,
                    'reason': 'capture sequence missing inside RUNNING observation window',
                    'irq_counter_available': False}
        for previous, current in zip(sequences, sequences[1:]):
            if current != previous + 1:
                return {'verified': False, 'periods_advanced': 0,
                        'reason': 'capture sequence gap inside RUNNING observation window',
                        'irq_counter_available': False}
    for previous, current in zip(points, points[1:]):
        if current[0] <= previous[0] or current[1] < previous[1]:
            return {'verified': False, 'periods_advanced': 0,
                    'reason': 'non-monotonic observation', 'irq_counter_available': False}
    advance = points[-1][1] - points[0][1] if len(points) >= 2 else 0
    period_size = next(iter(period_sizes))
    completed = advance // period_size
    return {
        'verified': len(points) >= 2 and completed > 0,
        'running_samples': len(points),
        'period_size': period_size,
        'hw_ptr_advance': advance,
        'periods_advanced': completed,
        'irq_counter_available': False,
        'irq_counter_reason': 'No per-RDMA2 IRQ counter is exposed by the audited userspace ABI; hw_ptr period crossings are a callback proxy only.',
        'continuity_scope': 'Observed samples only; state changes between captures are not detectable.',
        'reason': 'hw_ptr crossed ALSA period boundary in observed RUNNING samples' if completed else 'no complete ALSA period observed',
    }


def _capture_clock_observations(clock_root=CLK_DEBUG):
    """Read only a small allowlisted set of aud/ABOX-named clock nodes."""
    scan_start = time.monotonic_ns()
    clocks = []
    truncated = False
    try:
        with os.scandir(clock_root) as entries:
            for index, entry in enumerate(entries):
                if index >= MAX_CLOCK_DIRS:
                    truncated = True
                    break
                if not entry.is_dir(follow_symlinks=False):
                    continue
                name = entry.name
                if not re.fullmatch(r'[A-Za-z0-9_.-]{1,128}', name):
                    continue
                if not any(term in name.lower() for term in CLOCK_TERMS):
                    continue
                if len(clocks) >= MAX_CLOCK_MATCHES:
                    truncated = True
                    break
                base = Path(clock_root) / name
                record = {'name': name}
                for field in ('clk_rate', 'clk_enable_count', 'clk_prepare_count'):
                    record[field] = timed_text(base / field, 64)
                clocks.append(record)
        status = 'ok'
    except OSError as error:
        status = 'not_present' if error.errno == errno.ENOENT else 'read_error'
        code = errno.errorcode.get(error.errno, 'EIO')
    result = {'status': status, 'clocks': clocks, 'scan_truncated': truncated,
              'start_monotonic_ns': scan_start, 'end_monotonic_ns': time.monotonic_ns()}
    if status != 'ok':
        result['error_code'] = code
    return result


def _rich_observations():
    result = {
        'alsa_hw_params': timed_text(PCM / 'hw_params', 4096),
        'alsa_sw_params': timed_text(PCM / 'sw_params', 4096),
        'abox_runtime_active_time': timed_text(ABOX / 'power/runtime_active_time', 64),
        'abox_runtime_suspended_time': timed_text(ABOX / 'power/runtime_suspended_time', 64),
        'abox_runtime_usage': timed_text(ABOX / 'power/runtime_usage', 64),
        'calliope_version_hex': timed_read(ABOX / 'calliope_version', 32, binary=True),
        'dpcm_rdma2': timed_text(ASOC_CARD / 'dpcm/RDMA2/state', 4096),
        'asoc_dais': timed_text(ASOC / 'dais', 8192),
        'dapm': {},
        'clocks': _capture_clock_observations(),
    }
    for widget in DAPM_WIDGETS:
        result['dapm'][widget] = timed_text(ASOC_CARD / 'dapm' / widget, 4096)
    return result


def _observation_intervals(value):
    if isinstance(value, dict):
        start = value.get('start_monotonic_ns')
        end = value.get('end_monotonic_ns')
        if (isinstance(start, int) and not isinstance(start, bool)
                and isinstance(end, int) and not isinstance(end, bool) and end >= start):
            yield (start, end)
        for child in value.values():
            yield from _observation_intervals(child)
    elif isinstance(value, list):
        for child in value:
            yield from _observation_intervals(child)


def _observation_count(value):
    if isinstance(value, dict):
        own = 1 if 'status' in value else 0
        return own + sum(_observation_count(child) for child in value.values())
    if isinstance(value, list):
        return sum(_observation_count(child) for child in value)
    return 0


def snapshot(read_status=False, sequence=None, trial_id=None):
    """Take one bounded observation set; no route, mixer, or register writes."""
    snapshot_start = time.monotonic_ns()
    if bounded('/proc/1/comm', 64).strip() != 'native-guardian':
        raise ValueError('wrong native session')
    plan_start = time.monotonic_ns()
    plan = plans(bounded(MAP / 'range', 65536), bounded(MAP / 'access', 262144))
    plan_end = time.monotonic_ns()
    observations = {
        'alsa_status': timed_text(PCM / 'status', 4096),
        'abox_runtime_status': timed_text(ABOX / 'power/runtime_status', 64),
        'abox_cache_only': timed_text(MAP / 'cache_only', 64),
        'abox_service': timed_text(ABOX / 'service', 64),
        'abox_reset_count': timed_text(ABOX / 'reset_count', 64),
    }
    def value(key):
        record = observations[key]
        return record.get('value') if record.get('status') == 'ok' else None
    alsa_status = value('alsa_status') or ''
    runtime_status = value('abox_runtime_status')
    cache_only = value('abox_cache_only')
    service = value('abox_service')
    result = {
        'sample_schema': 2,
        'trial_id': trial_id if isinstance(trial_id, str) and re.fullmatch(r'[a-z0-9-]{1,64}', trial_id) else None,
        'sequence': sequence if isinstance(sequence, int) and not isinstance(sequence, bool) and sequence >= 0 else None,
        'snapshot_start_monotonic_ns': snapshot_start,
        'plan_interval_monotonic_ns': {'start': plan_start, 'end': plan_end},
        'plan': plan,
        'observations': observations,
        'alsa_status': alsa_status,
        'alsa_counters': parse_alsa_status(alsa_status),
        # Existing flat fields are retained for receipt consumers.
        'runtime_status': runtime_status,
        'cache_only': cache_only,
        'service': service,
        'reset_count': value('abox_reset_count'),
        'registers': {},
        'period_irq_observability': {
            'available': False,
            'reason': 'The source-mapped ABOX DMA IRQ feeds ALSA period_elapsed, but no per-RDMA2 interrupt counter is exported through this userspace interface.'},
        'firmware_response_observability': {
            'available': False,
            'reason': 'calliope_version and service state are sampled; no read-only per-stream IPC response counter is exported.'},
    }
    if read_status:
        result['observations'].update(_rich_observations())
        result['hw_params_parsed'] = parse_hw_params(
            result['observations']['alsa_hw_params'].get('value', ''))
    if not read_status or runtime_status != 'active' or cache_only != 'N' or service != '1':
        result['status_read_skipped'] = True
    else:
        fd = os.open(MAP / 'registers', os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        try:
            register_start = time.monotonic_ns()
            for item in plan:
                raw = os.pread(fd, item['length'], item['offset']).decode('ascii')
                match = re.fullmatch(r'([0-9a-f]+): ([0-9a-f]{8})\n', raw)
                if len(raw) != item['length'] or not match or int(match[1], 16) != item['register']:
                    raise ValueError('register record mismatch; stop rather than scanning')
                result['registers'][f'{item["register"]:04x}'] = int(match[2], 16)
            result['observations']['rdma2_registers'] = {
                'status': 'ok', 'start_monotonic_ns': register_start,
                'end_monotonic_ns': time.monotonic_ns(), 'registers': result['registers'].copy()}
        finally:
            os.close(fd)
        if '1200' in result['registers']:
            result['rdma2_ctrl'] = decode_rdma2_ctrl(result['registers']['1200'])
    snapshot_end = time.monotonic_ns()
    alsa_window = observations['alsa_status']
    alsa_start = alsa_window.get('start_monotonic_ns', snapshot_start)
    alsa_end = alsa_window.get('end_monotonic_ns', snapshot_end)
    result['monotonic'] = (alsa_start + alsa_end) / 2_000_000_000
    result['sample_monotonic_ns'] = (alsa_start + alsa_end) // 2
    result['snapshot_end_monotonic_ns'] = snapshot_end
    intervals = list(_observation_intervals(observations))
    total_observations = _observation_count(observations)
    result['synchronization'] = {
        'clock_domain': 'CLOCK_MONOTONIC',
        'sample_time_source': 'ALSA sub0 status read midpoint',
        'observation_count': total_observations,
        'timed_observation_count': len(intervals),
        'all_observations_timed': total_observations > 0 and len(intervals) >= total_observations,
        'observation_window_start_monotonic_ns': min((start for start, _ in intervals), default=None),
        'observation_window_end_monotonic_ns': max((end for _, end in intervals), default=None),
        'observation_window_span_ns': (max((end for _, end in intervals), default=0)
                                       - min((start for start, _ in intervals), default=0)) if intervals else None,
        'max_single_read_duration_ns': max((end - start for start, end in intervals), default=None),
    }
    result['snapshot_span_ns'] = max(0, snapshot_end - snapshot_start)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--read-status', action='store_true',
                        help='also capture rich observations and source-validated RDMA2 status words')
    parser.add_argument('--trial-id')
    parser.add_argument('--sequence', type=int)
    args = parser.parse_args()
    print(json.dumps(snapshot(args.read_status, args.sequence, args.trial_id), sort_keys=True))
