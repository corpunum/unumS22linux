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
UAIF1_CLOCKS = (
    ('DOUT_DIV_CLK_AUD_UAIF1', 'bclk'),
    ('GATE_ABOX_QCH_BCLK1', 'bclk_gate'),
    ('MOUT_CLK_AUD_UAIF1', 'mux'),
)
DAPM_WIDGET_PATHS = {
    'Rainbow-Prince': ('SPEAKER', 'RECEIVER'),
    'Rainbow-Prince/18c50000.abox': (
        'ABOX UDMA SIFS0', 'ABOX SIFS0', 'ABOX SIFS0 OUT',
        'ABOX SPUS OUT0-SIFS0', 'ABOX SPUS OUT1-SIFS0',
        'ABOX SPUS OUT2-SIFS0', 'ABOX SPUS OUT3-SIFS0',
        'ABOX SPUS OUT6-SIFS0', 'ABOX SPUS OUT9-SIFS0',
        'ABOX SPUS OUT11-SIFS0',
    ),
    'Rainbow-Prince/cs35l41.5-0040': (
        'Right AMP Playback', 'Right AMP Enable', 'Right ASPRX1',
        'Right ASPRX2', 'Right Main AMP', 'Right AMP SPK',
    ),
    'Rainbow-Prince/cs35l41.5-0041': (
        'Left AMP Playback', 'Left AMP Enable', 'Left ASPRX1',
        'Left ASPRX2', 'Left Main AMP', 'Left AMP SPK',
    ),
}
MAX_DAPM_READ_BYTES = 1024


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


def decode_rdma2_status(value):
    """Decode SOC4 RDMA_STATUS fields used by the pinned RDMA driver."""
    if not isinstance(value, int) or isinstance(value, bool) or value < 0 or value > 0xffffffff:
        raise ValueError('invalid 32-bit RDMA_STATUS value')
    return {
        'raw': value,
        'progress': bool(value & 0x80000000),
        'rbuf_offset': (value >> 20) & 0xff,
        'rbuf_count': value & 0xfffff,
    }


def decode_rdma2_status_add(value):
    """Decode the SOC4 RDMA_STATUS_ADD current-buffer-address field."""
    if not isinstance(value, int) or isinstance(value, bool) or value < 0 or value > 0xffffffff:
        raise ValueError('invalid 32-bit RDMA_STATUS_ADD value')
    return {'raw': value, 'current_address': value & 0x7fffffff}


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


def parse_dpcm_state(text):
    """Parse the read-only ASoC dynamic-PCM FE and active-BE state report."""
    if not isinstance(text, str):
        return {'available': False, 'streams': []}
    headers = list(re.finditer(r'^\[([^\]\n]+) - (Playback|Capture)\]$', text, re.M))
    streams = []
    for index, header in enumerate(headers):
        end = headers[index + 1].start() if index + 1 < len(headers) else len(text)
        block = text[header.end():end]
        state = re.search(r'^State: ([a-z_]+)$', block, re.M)
        backends = []
        current = None
        for line in block.splitlines():
            backend = re.fullmatch(r'- (.+)', line)
            if backend:
                current = {'name': backend.group(1), 'state': None}
                backends.append(current)
                continue
            backend_state = re.fullmatch(r'\s+State: ([a-z_]+)', line)
            if backend_state and current is not None:
                current['state'] = backend_state.group(1)
                current = None
        streams.append({
            'link': header.group(1),
            'stream': header.group(2).lower(),
            'state': state.group(1) if state else None,
            'backends': backends,
            'no_active_backends': bool(re.search(r'^ No active DSP links$', block, re.M)),
        })
    return {'available': bool(headers), 'streams': streams}


def _clock_counter(record):
    if not isinstance(record, dict) or record.get('status') != 'ok':
        return None
    value = record.get('value')
    if not isinstance(value, str) or not re.fullmatch(r'\d+', value):
        return None
    return int(value)


def _sample_point(sample):
    sequence = sample.get('sequence')
    timestamp = sample.get('sample_monotonic_ns')
    if not isinstance(timestamp, int) or isinstance(timestamp, bool):
        monotonic = sample.get('monotonic')
        if isinstance(monotonic, bool) or not isinstance(monotonic, (int, float)):
            timestamp = None
        else:
            try:
                monotonic = float(monotonic)
                timestamp = (int(monotonic * 1_000_000_000)
                             if math.isfinite(monotonic) and monotonic >= 0 else None)
            except (OverflowError, ValueError):
                timestamp = None
    if (isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0
            or isinstance(timestamp, bool) or not isinstance(timestamp, int) or timestamp < 0):
        return None
    return sequence, timestamp


def _continuous_points(points):
    """Require adjacent captured sequence numbers and increasing source time."""
    if len(points) < 2:
        return False
    for previous, current in zip(points, points[1:]):
        if current['sequence'] != previous['sequence'] + 1:
            return False
        if current['timestamp_ns'] <= previous['timestamp_ns']:
            return False
    return True


def _dapm_widget_states(observations):
    """Expose only source-named DAPM On/Off states; never infer sound."""
    result = {}
    dapm = observations.get('dapm') if isinstance(observations, dict) else None
    if not isinstance(dapm, dict):
        return result
    for owner, widgets in dapm.items():
        if not isinstance(owner, str) or not isinstance(widgets, dict):
            continue
        for widget, record in widgets.items():
            if not isinstance(widget, str) or not isinstance(record, dict):
                continue
            text = record.get('value') if record.get('status') == 'ok' else None
            if not isinstance(text, str):
                continue
            match = re.search(r'^' + re.escape(widget) + r': (On|Off)\b', text, re.M)
            if match:
                result[owner + '/' + widget] = match.group(1).lower()
    return result


def source_path_assessment(samples):
    """Localize FE, DPCM, UAIF1 framework-clock, RDMA and pointer evidence.

    Every stage claim is tied to same-sample status. A stationary RDMA result
    requires at least two contiguous RUNNING samples. This is not an audio
    acceptance test: the kernel exposes no per-stream IPC response counter,
    and clock-framework state is not a physical pin measurement.
    """
    if not isinstance(samples, list) or len(samples) > 256:
        return {'available': False, 'reason': 'invalid sample collection'}

    running = []
    dpcm_points = []
    clock_points = []
    rdma_points = []
    pointer_points = []
    rdma_hw_ptr_points = []
    any_alsa_state = False
    dpcm_observation_status_counts = {}
    clock_observation_status_counts = {}
    missing_clock_node_count = 0
    rdma_status_skip_reason_counts = {}
    rdma_missing_pair_count = 0
    for sample in samples:
        if not isinstance(sample, dict):
            continue
        counters = sample.get('alsa_counters')
        state = counters.get('state') if isinstance(counters, dict) else None
        any_alsa_state = any_alsa_state or isinstance(state, str)
        if state != 'RUNNING':
            continue
        point = _sample_point(sample)
        if point is None:
            running.append({'sample': sample, 'point': None})
            continue
        sequence, timestamp_ns = point
        running_point = {'sample': sample, 'sequence': sequence,
                         'timestamp_ns': timestamp_ns}
        running.append(running_point)
        pm_gate_ok = (sample.get('runtime_status') == 'active'
                      and sample.get('cache_only') == 'N'
                      and sample.get('service') == '1')
        observations = sample.get('observations')
        dpcm_record = observations.get('dpcm_rdma2') if isinstance(observations, dict) else None
        dpcm_status = (dpcm_record.get('status') if isinstance(dpcm_record, dict)
                       else 'not_sampled')
        dpcm_observation_status_counts[dpcm_status] = (
            dpcm_observation_status_counts.get(dpcm_status, 0) + 1)
        parsed = (parse_dpcm_state(dpcm_record.get('value'))
                  if isinstance(dpcm_record, dict) and dpcm_record.get('status') == 'ok'
                  else {'available': False, 'streams': []})
        rdma_streams = [stream for stream in parsed['streams']
                        if stream['link'] == 'RDMA2' and stream['stream'] == 'playback']
        dpcm_point = dict(running_point)
        dpcm_point.update({'available': bool(parsed['available'] and len(rdma_streams) == 1),
                           'stream': rdma_streams[0] if len(rdma_streams) == 1 else None})
        dpcm_points.append(dpcm_point)

        clock_record = observations.get('clocks') if isinstance(observations, dict) else None
        clock_status = (clock_record.get('status') if isinstance(clock_record, dict)
                        else 'not_sampled')
        if clock_status == 'ok' and not pm_gate_ok:
            clock_status = 'ok_without_pm_gate'
        clock_observation_status_counts[clock_status] = (
            clock_observation_status_counts.get(clock_status, 0) + 1)
        if (pm_gate_ok and isinstance(clock_record, dict)
                and clock_record.get('status') == 'ok'):
            by_name = {entry.get('name'): entry for entry in clock_record.get('clocks', [])
                       if isinstance(entry, dict)}
            for name, role in UAIF1_CLOCKS:
                entry = by_name.get(name)
                if isinstance(entry, dict) and any(
                        isinstance(entry.get(field), dict)
                        and entry[field].get('status') == 'not_present'
                        for field in ('clk_rate', 'clk_enable_count', 'clk_prepare_count')):
                    missing_clock_node_count += 1
            clock_point = dict(running_point)
            for name, role in UAIF1_CLOCKS:
                entry = by_name.get(name)
                clock_point[role] = _clock_counter(entry.get('clk_enable_count')) if entry else None
                clock_point[role + '_rate'] = _clock_counter(entry.get('clk_rate')) if entry else None
                clock_point[role + '_prepare'] = _clock_counter(entry.get('clk_prepare_count')) if entry else None
            clock_points.append(clock_point)

        registers = sample.get('registers')
        rdma_point = None
        status_skip_reason = sample.get('status_read_skip_reason')
        if isinstance(registers, dict):
            status = registers.get('1230')
            status_add = registers.get('1238')
            if (pm_gate_ok and isinstance(status, int) and not isinstance(status, bool)
                    and isinstance(status_add, int) and not isinstance(status_add, bool)):
                try:
                    rdma_point = {
                        'sequence': sequence,
                        'timestamp_ns': timestamp_ns,
                        'status': decode_rdma2_status(status),
                        'status_add': decode_rdma2_status_add(status_add),
                    }
                    rdma_points.append(rdma_point)
                except ValueError:
                    pass
            else:
                if not pm_gate_ok:
                    status_skip_reason = status_skip_reason or 'abox_active_cache_service_gate_not_confirmed'
                if isinstance(status_skip_reason, str):
                    rdma_status_skip_reason_counts[status_skip_reason] = (
                        rdma_status_skip_reason_counts.get(status_skip_reason, 0) + 1)
                else:
                    rdma_missing_pair_count += 1
        pointer = counters.get('hw_ptr') if isinstance(counters, dict) else None
        if isinstance(pointer, int) and not isinstance(pointer, bool) and pointer >= 0:
            pointer_point = {'sequence': sequence, 'timestamp_ns': timestamp_ns,
                             'hw_ptr': pointer}
            pointer_points.append(pointer_point)
            if rdma_point is not None:
                rdma_hw_ptr_points.append({
                    'sequence': sequence,
                    'timestamp_ns': timestamp_ns,
                    'rdma_position': (
                        rdma_point['status']['rbuf_offset'],
                        rdma_point['status']['rbuf_count'],
                        rdma_point['status_add']['current_address']),
                    'hw_ptr': pointer,
                })

    dpcm_frontend_states = []
    backend_state_sets = {}
    complete_dpcm = [point for point in dpcm_points if point['available']]
    uaif1_started_points = []
    for point in complete_dpcm:
        stream = point['stream']
        dpcm_frontend_states.append(stream['state'])
        for backend_item in stream['backends']:
            backend_state_sets.setdefault(backend_item['name'], set()).add(backend_item['state'])
            if backend_item['name'] == 'UAIF1' and backend_item['state'] == 'start':
                uaif1_started_points.append(point)
    backend_names = sorted(backend_state_sets)
    all_running_dpcm_complete = bool(running) and len(complete_dpcm) == len(running)
    no_backend_report_count = sum(
        not point['stream']['backends'] and point['stream']['no_active_backends']
        for point in complete_dpcm)
    if not running:
        frontend = 'alsa_not_running_in_observed_window' if any_alsa_state else 'unknown'
        backend = 'not_applicable_no_running_frontend'
    else:
        frontend = 'alsa_running_observed'
        if not all_running_dpcm_complete:
            backend = 'unknown_dpcm_observation_missing_for_running_sample'
        elif uaif1_started_points:
            backend = 'uaif1_dpcm_backend_started'
        elif backend_names:
            backend = 'dpcm_backend_observed_without_uaif1_start'
        elif no_backend_report_count == len(running):
            backend = 'running_frontend_without_active_dpcm_backend'
        else:
            backend = 'dpcm_state_does_not_prove_active_backend'

    paired_clocks = []
    for dpcm_point in uaif1_started_points:
        clock_point = next((candidate for candidate in clock_points
                            if candidate['sequence'] == dpcm_point['sequence']), None)
        if (clock_point is not None and clock_point.get('bclk') is not None
                and clock_point.get('bclk_gate') is not None):
            paired_clocks.append(clock_point)
    clock_counts = {
        role: [point[role] for point in paired_clocks]
        for role in ('bclk', 'bclk_gate', 'mux')
    }
    if any(point['bclk'] > 0 and point['bclk_gate'] > 0 for point in paired_clocks):
        clock_stage = 'uaif1_bclk_and_gate_enable_counts_nonzero_in_clock_framework'
    elif paired_clocks and all(point['bclk'] == 0 and point['bclk_gate'] == 0
                               for point in paired_clocks):
        clock_stage = 'uaif1_started_with_bclk_and_gate_enable_counts_zero'
    else:
        clock_stage = 'uaif1_clock_enable_counts_unknown_or_unpaired'

    rdma_contiguous = _continuous_points(rdma_points)
    rdma_position_changed = False
    rdma_progress_bit = any(point['status']['progress'] for point in rdma_points)
    if rdma_contiguous:
        rdma_position_changed = any(
            (current['status']['rbuf_offset'], current['status']['rbuf_count'],
             current['status_add']['current_address']) !=
            (previous['status']['rbuf_offset'], previous['status']['rbuf_count'],
             previous['status_add']['current_address'])
            for previous, current in zip(rdma_points, rdma_points[1:]))
    if len(rdma_points) < 2 or not rdma_contiguous:
        rdma_stage = 'rdma2_progress_not_localized_insufficient_or_gapped_running_samples'
    elif rdma_position_changed:
        rdma_stage = 'rdma2_status_position_changed_during_running_samples'
    elif rdma_progress_bit:
        rdma_stage = 'rdma2_progress_bit_set_but_position_unchanged_in_observed_samples'
    else:
        rdma_stage = 'rdma2_status_position_unchanged_during_contiguous_running_samples'

    pointer_contiguous = _continuous_points(pointer_points)
    pointer_advanced = bool(pointer_contiguous and any(
        current['hw_ptr'] > previous['hw_ptr']
        for previous, current in zip(pointer_points, pointer_points[1:])))
    rdma_hw_ptr_contiguous = _continuous_points(rdma_hw_ptr_points)
    paired_pointer_monotonic = all(
        current['hw_ptr'] >= previous['hw_ptr']
        for previous, current in zip(rdma_hw_ptr_points, rdma_hw_ptr_points[1:]))
    paired_rdma_position_changed = bool(rdma_hw_ptr_contiguous and any(
        current['rdma_position'] != previous['rdma_position']
        for previous, current in zip(rdma_hw_ptr_points, rdma_hw_ptr_points[1:])))
    paired_hw_ptr_advanced = bool(rdma_hw_ptr_contiguous and paired_pointer_monotonic and any(
        current['hw_ptr'] > previous['hw_ptr']
        for previous, current in zip(rdma_hw_ptr_points, rdma_hw_ptr_points[1:])))
    if (rdma_hw_ptr_contiguous and paired_pointer_monotonic
            and paired_rdma_position_changed and not paired_hw_ptr_advanced):
        ipc_stage = 'rdma_position_changes_but_alsa_hw_ptr_does_not_advance'
    elif (rdma_hw_ptr_contiguous and paired_pointer_monotonic
          and paired_rdma_position_changed and paired_hw_ptr_advanced):
        ipc_stage = 'alsa_hw_ptr_advances_with_rdma_position'
    else:
        ipc_stage = 'pointer_ipc_not_localized_insufficient_or_gapped_samples'

    if frontend != 'alsa_running_observed':
        localization = 'frontend_did_not_reach_observed_running_state'
    elif backend == 'running_frontend_without_active_dpcm_backend':
        localization = 'running_frontend_without_dpcm_backend'
    elif backend == 'unknown_dpcm_observation_missing_for_running_sample':
        localization = 'dpcm_backend_stage_unknown_due_to_missing_sample'
    elif ipc_stage == 'rdma_position_changes_but_alsa_hw_ptr_does_not_advance':
        localization = 'rdma_status_moves_but_firmware_pointer_ipc_or_alsa_notification_path_lags'
    elif clock_stage == 'uaif1_started_with_bclk_and_gate_enable_counts_zero':
        localization = 'uaif1_backend_started_but_linux_bclk_enable_counts_are_zero'
    elif (rdma_stage == 'rdma2_status_position_unchanged_during_contiguous_running_samples'
          and clock_stage == 'uaif1_bclk_and_gate_enable_counts_nonzero_in_clock_framework'):
        localization = 'frontend_backend_and_clock_framework_reached_start; dsp_consumption_vs_physical_clock_unresolved'
    else:
        localization = 'insufficient_source_distinguishing_evidence'

    return {
        'available': True,
        'sample_count': len(samples),
        'running_sample_count': len(running),
        'dpcm_sample_count': len(complete_dpcm),
        'dpcm_observation_status_counts': dpcm_observation_status_counts,
        'all_running_samples_have_dpcm_state': all_running_dpcm_complete,
        'frontend': frontend,
        'dpcm_backend': backend,
        'dpcm_frontend_states': dpcm_frontend_states,
        'dpcm_backend_states': {name: sorted(states) for name, states in sorted(backend_state_sets.items())},
        'uaif1_started_sample_count': len(uaif1_started_points),
        'uaif1_clock_stage': clock_stage,
        'clock_observation_status_counts': clock_observation_status_counts,
        'uaif1_clock_missing_node_count': missing_clock_node_count,
        'uaif1_clock_enable_counts_paired_with_backend_start': clock_counts,
        'rdma2_sample_count': len(rdma_points),
        'rdma2_pm_skip_reason_counts': rdma_status_skip_reason_counts,
        'rdma2_missing_register_pair_count': rdma_missing_pair_count,
        'rdma2_samples_contiguous': rdma_contiguous,
        'rdma2_progress_bit_seen': rdma_progress_bit,
        'rdma2_position_changed': rdma_position_changed,
        'rdma2_stage': rdma_stage,
        'alsa_hw_ptr_sample_count': len(pointer_points),
        'alsa_hw_ptr_samples_contiguous': pointer_contiguous,
        'alsa_hw_ptr_advanced': pointer_advanced,
        'rdma_hw_ptr_pair_sample_count': len(rdma_hw_ptr_points),
        'rdma_hw_ptr_pair_samples_contiguous': rdma_hw_ptr_contiguous,
        'rdma_position_changed_in_paired_window': paired_rdma_position_changed,
        'paired_alsa_hw_ptr_advanced': paired_hw_ptr_advanced,
        'pointer_ipc_stage': ipc_stage,
        'dapm_widget_states': {
            str(sample.get('sequence')): _dapm_widget_states(sample.get('observations'))
            for sample in samples if isinstance(sample, dict)
        },
        'localization': localization,
        'limits': [
            'No per-stream IPC response counter is exported; pointer IPC localization is indirect.',
            'Clock rate and enable counts are clock-framework bookkeeping; they are not a physical pin waveform.',
            'The ASoC DAI list is an inventory and is not interpreted as per-stream DAI activity.',
        ],
    }


def _capture_clock_observations(clock_root=CLK_DEBUG, *, allow_reads=False, reader=timed_text):
    """Read only the source-mapped UAIF1 bclk, bclk gate and mux nodes."""
    scan_start = time.monotonic_ns()
    if not allow_reads:
        return {'status': 'skipped_pm_gate', 'clocks': [],
                'reason': 'ABOX runtime-active/cache-only/service gate not satisfied',
                'scope': 'fixed SOC5E9925 UAIF1 clocks mapped from the bound device tree',
                'scan_truncated': False,
                'start_monotonic_ns': scan_start, 'end_monotonic_ns': time.monotonic_ns()}
    clocks = []
    for name, role in UAIF1_CLOCKS:
        base = Path(clock_root) / name
        record = {'name': name, 'role': role}
        for field in ('clk_rate', 'clk_enable_count', 'clk_prepare_count'):
            record[field] = reader(base / field, 64)
        clocks.append(record)
    return {'status': 'ok', 'clocks': clocks,
            'scope': 'fixed SOC5E9925 UAIF1 clocks mapped from the bound device tree',
            'scan_truncated': False,
            'start_monotonic_ns': scan_start, 'end_monotonic_ns': time.monotonic_ns()}


def _capture_dapm_observations(asoc_root=ASOC, reader=timed_text):
    """Read only the pinned card/component widget paths, never enumerate dirs."""
    result = {}
    for owner, widgets in DAPM_WIDGET_PATHS.items():
        owner_observations = {}
        for widget in widgets:
            owner_observations[widget] = reader(
                Path(asoc_root) / owner / 'dapm' / widget,
                MAX_DAPM_READ_BYTES)
        result[owner] = owner_observations
    return result


def _rich_observations(*, clock_access_allowed=False):
    result = {
        'alsa_hw_params': timed_text(PCM / 'hw_params', 4096),
        'alsa_sw_params': timed_text(PCM / 'sw_params', 4096),
        'abox_runtime_active_time': timed_text(ABOX / 'power/runtime_active_time', 64),
        'abox_runtime_suspended_time': timed_text(ABOX / 'power/runtime_suspended_time', 64),
        'abox_runtime_usage': timed_text(ABOX / 'power/runtime_usage', 64),
        'calliope_version_hex': timed_read(ABOX / 'calliope_version', 32, binary=True),
        # soc_dpcm_debugfs_add() creates the FE directory directly under the
        # card root: Rainbow-Prince/RDMA2/state (there is no dpcm/ component).
        'dpcm_rdma2': timed_text(ASOC_CARD / 'RDMA2/state', 4096),
        'asoc_dais': timed_text(ASOC / 'dais', 8192),
        'dapm': _capture_dapm_observations(),
        'clocks': _capture_clock_observations(allow_reads=clock_access_allowed),
    }
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
        clock_access_allowed = (runtime_status == 'active' and cache_only == 'N'
                                and service == '1')
        result['observations'].update(_rich_observations(
            clock_access_allowed=clock_access_allowed))
        result['hw_params_parsed'] = parse_hw_params(
            result['observations']['alsa_hw_params'].get('value', ''))
    if not read_status or runtime_status != 'active' or cache_only != 'N' or service != '1':
        result['status_read_skipped'] = True
        if not read_status:
            result['status_read_skip_reason'] = 'read_status_not_requested'
        elif runtime_status != 'active':
            result['status_read_skip_reason'] = 'abox_runtime_not_active'
        elif cache_only != 'N':
            result['status_read_skip_reason'] = 'abox_regmap_cache_only'
        else:
            result['status_read_skip_reason'] = 'abox_service_unavailable'
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
        if '1230' in result['registers']:
            result['rdma2_status'] = decode_rdma2_status(result['registers']['1230'])
        if '1238' in result['registers']:
            result['rdma2_status_add'] = decode_rdma2_status_add(result['registers']['1238'])
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
