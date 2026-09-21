#!/usr/bin/env python3
"""Export bounded metadata, never firmware, raw captures or device identities."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PRIVATE = ROOT/'rootfs/hardware-reuse-20260921'
PUBLIC = ROOT/'evidence/hardware-reuse-20260921'


def read(name):
    return json.loads((PRIVATE/name).read_text())


def write(name, value):
    (PUBLIC/name).write_text(json.dumps(value,indent=2,sort_keys=True)+'\n')


def main():
    PUBLIC.mkdir(parents=True,exist_ok=True)
    for name in ['ennc-ncp-inventory-v2.json','enn-loader-preflight.json']:
        data=read(name)
        # These host reports contain artifact metadata only. Reject unexpected
        # absolute private paths rather than exporting an expanded report.
        serialized=json.dumps(data)
        assert '/home/' not in serialized and 'boot_id' not in serialized
        write(name,data)
    start=read('sensor-start-once/receipt.json')
    assert 'boot_id' not in json.dumps(start)
    trials=[]
    for name in ['accel-first','gyro-first','accel-second','gyro-second']:
        receipt=read('sensor-samples/'+name+'/receipt.json')
        sample=receipt['sampling']
        trials.append(dict(name=name,started_at=receipt['started_at'],
                           source_sha256=receipt['source_sha256'],
                           returncode=receipt['returncode'],same_boot=receipt['same_boot'],
                           kernel_capture_exit=receipt['kernel_capture_exit'],
                           model_healthy=receipt['after']['model']=='ok',
                           temperature=receipt['after']['temperature'],
                           sampling={k:v for k,v in sample.items() if k!='sample_frames'}))
    write('sensors.json',dict(startup=start,trials=trials,
        limitations=['Raw samples only; calibration, desktop integration and autostart remain unaccepted.',
                     'First accel enable replayed zero runtime calibration after the absent file-manager client timed out.',
                     'Restored state means enable mask and buffer flags only; the hub remains running.']))
    audio=json.loads((ROOT/'rootfs/audio-reuse-20260921/abox-core-once/receipt.json').read_text())
    def audio_status(status):
        selected={k:status[k] for k in ['power_control','runtime_status','reset_count','cards']}
        selected['calliope_version']=status['calliope_version'].strip('\x00\n')
        selected['pcm_entries']=len(status['pcm'].splitlines()) if status['pcm'] else 0
        return selected
    write('audio.json',dict(
        started_at=audio['started_at'], start_performed=audio['start_performed'],
        same_boot=audio['same_boot'], restored_power_control=audio['restored_power_control'],
        start_error=audio['start_error'], restore_error=audio['restore_error'],
        observe_seconds=audio['observe_seconds'],
        firmware={name:{k:v[k] for k in ['size','sha256']}
                  for name,v in audio['source_manifest'].items()},
        before=audio_status(audio['before']['status']),
        during_active=audio_status(audio['during_active']),
        after=audio_status(audio['after']['status']),
        limitations=['DSP boot and dump/debug PCM registration only; no usable Rainbow machine card.',
                     'No PCM opened, speaker playback or microphone capture attempted.',
                     'No live rebind, module reload or boot-autostart acceptance.']))
    radio=read('radio-header.json')
    assert '/home/' not in json.dumps(radio) and 'boot_id' not in json.dumps(radio)
    backup=read('radio-readonly.json')
    radio['backup']={k:backup[k] for k in ['bytes','sha256','host_sha256','complete',
                                         'device','phone_partition_writes','modem_state']}
    write('radio.json',radio)
    loader=read('npu-trials/loader-first/receipt.json')
    loader_out=(PRIVATE/'npu-trials/loader-first/stdout.txt').read_text()
    loader_public={k:loader[k] for k in ['name','deadline_seconds','read_only_proc','devices',
        'manifest_sha256','closure_missing','closure_ambiguous','probe_sha256',
        'exec_calls','started_at','returncode','elapsed_seconds','same_boot','staged',
        'kernel_capture_exit','strace_capture_exit','helper_sha256','note']}
    loader_public.update(hardware_devices_exposed=False,
                         resolved_symbol_count=loader_out.count('stage=dlsym found='),
                         all_symbols_resolved='result=all_symbols_resolved calls=none dlclose=none' in loader_out,
                         resident_cpu_4b_healthy=loader['after']['model']=='ok',
                         temperature_c=loader['after']['temperature_c'],
                         new_gpu_or_npu_kernel_messages=len(loader['gpu_or_npu_kernel_messages']),
                         warnings=['Generated /linkerconfig/ld.config.txt absent; loader completed successfully.'],
                         runtime_acceptance='Library loading and six symbol lookups only; no ENN API calls or NPU inference.')
    write('npu-loader.json',loader_public)
    init=read('npu-trials/init-first/receipt.json')
    init_out=(PRIVATE/'npu-trials/init-first/stdout.txt').read_text()
    init_trace=(PRIVATE/'npu-trials/init-first/strace.txt').read_text()
    observed=[]
    for path in ['/dev/ion','/dev/dma_heap/system-uncached','/dev/dma_heap/system',
                 '/dev/vertex10','/vendor/etc/enn/custom_mode_config.json']:
        lines=[line for line in init_trace.splitlines() if 'openat(' in line and '"'+path+'"' in line]
        observed.append(dict(path=path,attempts=len(lines),all_absent=bool(lines) and all('= -1 ENOENT' in line for line in lines)))
    init_public={k:init[k] for k in ['name','deadline_seconds','read_only_proc','devices',
        'manifest_sha256','closure_missing','closure_ambiguous','probe_sha256',
        'exec_calls','started_at','returncode','elapsed_seconds','same_boot','staged',
        'kernel_capture_exit','strace_capture_exit','helper_sha256','note']}
    init_public.update(hardware_devices_exposed=False,
                       initialize_returned_zero='result=enn_initialize status=0 deinitialize=none' in init_out,
                       resident_cpu_4b_healthy=init['after']['model']=='ok',
                       temperature_c=init['after']['temperature_c'],
                       new_gpu_or_npu_kernel_messages=len(init['gpu_or_npu_kernel_messages']),
                       hardware_requests=observed,
                       limitations=['Zero return despite unavailable allocator and NPU nodes is not hardware readiness.',
                                    'Direct device opens were attempted and failed inside the sandbox; no NPU ioctl or firmware boot.',
                                    'No model, buffer or deinitialize API called; process used _exit.'])
    write('npu-initialize.json',init_public)
    manifest=dict(format='s22-hardware-reuse-public-v1',
                  excluded=['firmware bytes','raw kernel logs','device identifiers','raw timestamped sensor frames'],
                  files={p.name:hashlib.sha256(p.read_bytes()).hexdigest()
                         for p in sorted(PUBLIC.iterdir()) if p.is_file() and p.name!='manifest.json'})
    write('manifest.json',manifest)
    print(json.dumps(dict(exported_files=len(manifest['files']),public=str(PUBLIC))))


if __name__=='__main__':
    main()
