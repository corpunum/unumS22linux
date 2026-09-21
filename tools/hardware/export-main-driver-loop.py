#!/usr/bin/env python3
"""Export selected measured metadata, never raw phone logs/vendor assets."""
import json
import importlib.util
from pathlib import Path
import re

ROOT=Path(__file__).resolve().parents[2]
RAW=ROOT/'rootfs/main-driver-loop-20260921'
OUT=ROOT/'evidence/main-driver-loop-20260922'


def load(name):return json.loads((RAW/name).read_text())


def main():
    trials={}
    for name in ['runtime-filter-first','runtime-posix-spawn-first',
                 'runtime-close-range-filter-first','runtime-close-range-subprocess-first',
                 'runtime-close-range-repeat','runtime-close-range-tmux']:
        r=load(name+'/receipt.json')
        trials[name]={k:r[k] for k in ['case','binary_sha256','source_sha256',
                       'started_at','returncode','elapsed_seconds','same_boot','strace_capture_exit','kernel_capture_exit']}
        trials[name]['filtered_syscall']=r.get('filtered_syscall','clone3')
        if 'filtered_syscall' not in r:
            assert r['binary_sha256']=='a54f82a7c3450942756b31666a99156357b128096207b7393e3e8efb4b1dfd10'
        trials[name]['new_pending_kill_count']=len(r['new_pending_kill'])
    failed=load('runtime-subprocess-first/timeout-recovery.json')
    trials['runtime-subprocess-first']={k:failed[k] for k in ['outcome','filter','filtered_test',
          'child_stuck_after_SIGKILL','subsequent_child_stack_observed','retry_performed']}
    pi={}
    for name in ['pi-local-spawn','pi-local-postboot']:
        r=load(name+'/spawn-compat-summary.json')
        results=[json.loads(e['result']['content'][0]['text']) for e in r['tool_results'] if not e.get('isError')]
        assert len(results)==1 and results[0]['captured_output']=='PI_SPAWN_OK'
        assert results[0]['uid']==1000 and results[0]['inherited_close_range_filter'] and results[0]['child_returncode']==0
        assert r['returncode']==0 and r['agent_end_seen'] and not r['assistant_errors']
        pi[name]={'elapsed_seconds':r['elapsed_seconds'],'returncode':r['returncode'],
                  'tool_calls':len(r['tool_calls']),'tool_result':results[0],
                  'provider':'s22-local','model':'Qwen3.5-4B-Uncensored-HauhauCS-Aggressive-Q4_K_M'}
    a=load('audio-postboot.json')
    pcm=a['alsa']['/proc/asound/pcm'].splitlines()
    machine=[line for line in pcm if line.startswith('00-')]
    assert 'Rainbow-Prince' in a['alsa']['/proc/asound/cards']
    assert all(item['exact'] for item in a['firmware']['files'].values())
    audio={'machine_card':'Rainbow-Prince','firmware':a['firmware'],'abox':a['abox'],
           'firmware_search_path':a['firmware_search_path'],'platform_bindings':a['platform_bindings'],
           'machine_playback_pcm_count':sum('playback' in line for line in machine),
           'machine_capture_pcm_count':sum('capture' in line for line in machine),
           'deferred_devices_empty':a['debug']['/sys/kernel/debug/devices_deferred']=='',
           'physical_speaker_verified':False,'physical_microphone_verified':False,
           'mixer_writes':[],'pcm_io_performed':False}
    controls=load('audio-control-metadata-v4.json')['tools']['amixer']
    assert controls['returncode']==0 and not controls['stdout_truncated']
    audio['enumerated_control_count']=len(controls['stdout'].splitlines())
    audio['control_node']=load('sound-node-control0.json')
    audio['directory_permissions']=load('sound-directory-permissions.json')
    binding=load('audio-arch-control-bind.json')
    reading=load('audio-arch-control-read-v2.json')
    assert binding['returncode']==reading['returncode']==0
    arch_read=json.loads(reading['stdout'])
    assert arch_read['control_count']==1736 and arch_read['open_mode']=='SND_CTL_READONLY'
    arch_trace=(RAW/'audio-arch-control-read-v2.strace').read_text()
    assert '"/dev/snd/controlC0", O_RDONLY|O_CLOEXEC' in arch_trace
    assert 'SNDRV_CTL_IOCTL_ELEM_WRITE' not in arch_trace and '/dev/snd/pcm' not in arch_trace
    audio['arch_control_read']={**arch_read,'elapsed_seconds':reading['elapsed_seconds'],
                               'source_sha256':reading['source_sha256'],'session_only':True}
    audio['arch_control_bind']={**json.loads(binding['stdout']),'source_sha256':binding['source_sha256']}
    audio['control_startup_install']=load('audio-control-startup-install.json')
    audio['control_startup_live']=load('audio-control-startup-live.json')
    sr=load('audio-silence-first/receipt.json')
    silence_trace=(RAW/'audio-silence-first/strace.txt').read_text()
    silence_kernel=(RAW/'audio-silence-first/kernel-delta.txt').read_text()
    assert sr['returncode']==1 and sr['same_boot'] and sr['post_audio_preflight_exit']==0
    assert re.search(r'SNDRV_PCM_IOCTL_HW_PARAMS[^\n]+= 0 ',silence_trace)
    assert re.search(r'SNDRV_PCM_IOCTL_PREPARE[^\n]+= -1 EINVAL',silence_trace)
    assert 'no backend DAIs enabled for RDMA2' in silence_kernel
    assert 'SNDRV_PCM_IOCTL_WRITEI_FRAMES' not in silence_trace and 'SNDRV_PCM_IOCTL_START' not in silence_trace
    audio['pcm_io_performed']=True
    audio['silence_probe']={k:v for k,v in sr.items() if k not in ('before','after')}
    audio['silence_probe'].update({'hw_params_accepted':True,'prepare_errno':'EINVAL',
        'kernel_diagnosis':'no backend DAIs enabled for RDMA2','audio_frames_submitted':0,
        'playback_accepted':False,'amp_enable_switches_after':'both off'})
    spec=importlib.util.spec_from_file_location('route_probe',ROOT/'tools/hardware/run-audio-route-prepare-once.py')
    route=importlib.util.module_from_spec(spec);spec.loader.exec_module(route)
    audio['digital_route_trials']={}
    for name in ('audio-two-route-prepare-first','audio-two-route-zero-first'):
        r=load(name+'/receipt.json');trace=(RAW/name/'strace.txt').read_text()
        assessment=route.classify(r,trace)
        assert assessment['cleanup_verified'] and r['same_boot']
        audio['digital_route_trials'][name]={
            **{k:r[k] for k in ('started_at','elapsed_seconds','same_boot','helper_sha256')},
            'child_returncode':r['result']['child_returncode'],
            'temporary_controls':r['result']['events'],'restored':r['result']['restored'],
            'assessment':assessment}
    assert audio['digital_route_trials']['audio-two-route-prepare-first']['assessment']['diagnostic_completed']
    stalled=audio['digital_route_trials']['audio-two-route-zero-first']['assessment']
    assert stalled['child_interrupted'] and not stalled['diagnostic_completed'] and stalled['write_eagain_count']>0
    audio['mixer_writes']=[{'control':n,'temporary_value':1,'restored_value':0}
                          for n in ('ABOX SPUS OUT2','ABOX UAIF1 SPK')]
    bt_raw=ROOT/'rootfs/hardware-reuse-20260921/bt-trials/board-first-postboot'
    bt=json.loads((bt_raw/'receipt.json').read_text())
    match=re.fullmatch(r'board_reply_len=(\d+) raw=([0-9a-f ]+)\n',(bt_raw/'stdout.txt').read_text())
    assert match
    frame=bytes.fromhex(match[2])
    assert len(frame)==int(match[1])==11 and frame[:8]==bytes.fromhex('04 0e 08 01 00 fc 00 23')
    assert bt['returncode']==0 and bt['same_boot'] and bt['after_vote_check']==0 and bt['after_metadata_exit']==0
    bluetooth={k:bt[k] for k in ['started_at','elapsed','returncode','source_sha256','accepted_source_sha256',
                                'binary_sha256','same_boot','after_vote_check','after_metadata_exit']}
    bluetooth.update({'board_response_hex':frame[8:].hex(),'firmware_downloaded':False,'hci_accepted':False})
    patch_raw=bt_raw.parent/'patch-version-first'
    pr=json.loads((patch_raw/'receipt.json').read_text())
    patch_out=(patch_raw/'stdout.txt').read_text()
    assert pr['returncode']==0 and pr['same_boot'] and pr['after_vote_check']==0
    assert pr['after_metadata_exit']==pr['kernel_capture_exit']==pr['strace_capture_exit']==0
    pm=re.search(r'event_code=0e parameter_length=18 raw=([0-9a-f ]+)\n',patch_out)
    assert pm
    pf=bytes.fromhex(pm[1])
    assert len(pf)==21 and pf[:9]==bytes.fromhex('04 0e 12 01 00 fc 00 19 0c')
    product=int.from_bytes(pf[9:13],'little');rom=int.from_bytes(pf[15:17],'little')
    soc=int.from_bytes(pf[17:21],'little')
    assert product==0x13 and rom==0x201 and soc==0x400c0210
    bluetooth['patch_version']={key:pr[key] for key in ['started_at','elapsed','source_sha256',
        'binary_sha256','same_boot','after_vote_check','after_metadata_exit','kernel_capture_exit']}
    bluetooth['patch_version'].update({'response_hex':pf.hex(),'product_id':hex(product),
        'rom_version':hex(rom),'soc_id':hex(soc),'packed_map_key':hex((soc<<32)|(product<<16)|rom),
        'firmware_transmitted':False})
    boot=load('audio-reboot/result.json')
    assert boot['new_boot'] and boot['actual_mode']=='recovery' and boot['continuous_uptime_seconds']>=90
    boot['pending_kill_count']=len(boot.pop('pending_kill_tasks'))
    wifi=load('wifi-postboot.json')
    assert wifi['all_checks_passed'] and all(wifi['checks'].values())
    # The standalone WLAN checker does not itself observe reboot. This separate
    # receipt connects its result to the preceding monitored recovery boot.
    wifi['not_tested']=[v for v in wifi['not_tested'] if v!='reboot_autostart']
    wifi['reboot_observation_source']='audio-reboot/result.json plus fresh native readiness'
    gpu_path=ROOT/'rootfs/gpu-compat-20260921/trials/vulkan-compute256-boot762'
    g=json.loads((gpu_path/'receipt.json').read_text())
    gpu_text=(gpu_path/'stdout.txt').read_text()
    assert 'COMPUTE256 checksum=99712 device=Samsung Xclipse 920 PASS' in gpu_text
    assert g['returncode']==0 and g['same_boot'] and not g['gpu_messages'] and g['kernel_capture_exit']==0
    gpu={key:g[key] for key in ['started_at','variant','name','returncode','same_boot',
                               'kernel_capture_exit','gpu_messages','helper_sha256','elapsed_seconds']}
    gpu.update({'checksum':99712,'device':'Samsung Xclipse 920','numerical_shader_pass':True,
                'model_offload_tested_this_boot':False,'hyprland_acceleration_tested':False})
    result={'runtime_trials':trials,'pi_tests':pi,'audio':audio,'bluetooth':bluetooth,'reboot':boot,
            'gpu_post_recovery':gpu,
            'npu_qwen_source_review':load('npu-ownership-source-review/review-receipt.json'),
            'flash':load('audio-recovery-flash.json'),'wifi':wifi,
            'limitations':['NPU boot/inference not tested','Bluetooth firmware/HCI not accepted',
                           'SIM/cellular not activated','No camera/GPS/suspend acceptance',
                           'Kernel clamp patch compiled only, not installed',
                           'Pi-only process filter, not a system-wide kernel repair']}
    OUT.mkdir(exist_ok=True)
    with (OUT/'acceptance.json').open('w') as f:json.dump(result,f,indent=2);f.write('\n')
    print(json.dumps({'audio_playback_pcms':audio['machine_playback_pcm_count'],
                     'audio_capture_pcms':audio['machine_capture_pcm_count'],
                     'wifi_checks':len(wifi['checks']),'runtime_trials':len(trials),'pi_tests':len(pi)},indent=2))


if __name__=='__main__':main()
