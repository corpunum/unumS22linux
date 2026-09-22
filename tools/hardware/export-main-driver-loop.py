#!/usr/bin/env python3
"""Export selected measured metadata, never raw phone logs/vendor assets."""
import json
import importlib.util
from pathlib import Path
import re

ROOT=Path(__file__).resolve().parents[2]
RAW=ROOT/'rootfs/main-driver-loop-20260921'
OUT=ROOT/'evidence/main-driver-loop-20260922'
CONT_OUT=ROOT/'evidence/main-driver-loop-20260922'


def load(name):return json.loads((RAW/name).read_text())


def export_audio_extras_checkpoint():
    flash=load('audio-extra-recovery-flash.json');reboot=load('audio-extras-reboot/result.json')
    assert flash['partition_written']=='recovery' and flash['readback_sha256']=='758fc9d30491e17b7c829a89d338ba69476efa15a1280deb8a1b9b8009687f4b'
    assert reboot['new_boot'] and reboot['actual_mode']=='recovery' and reboot['continuous_uptime_seconds']>=90
    checks=load('audio-extras-postboot-v3/receipt.json')['checks']
    assert all(x['returncode']==0 for x in checks.values())
    state=checks['state']['result'];control=checks['arch-control']['result'];wifi=checks['wifi']['result']
    assert control['control_count']==1754 and len(state['arch_sound_nodes'])==1 and state['arch_sound_nodes'][0]['name']=='controlC0'
    firmware=load('audio-extras-firmware-postboot-v3.json')['result']
    assert len(firmware['files'])==20 and all(x['exact'] for x in firmware['files'].values())
    old_controls=load('audio-control-metadata-v4.json')['tools']['amixer']['stdout'].splitlines()
    names=lambda lines:{x.split(',name=',1)[1] for x in lines if ',name=' in x}
    old_names=names(old_controls);new_names=names(firmware['controls'])
    assert len(new_names-old_names)==18 and not old_names-new_names
    progress=load('audio-extras-progress/receipt.json')
    assert progress['assessment']['cleanup_verified'] and progress['same_boot']
    assert progress['assessment']['child_interrupted'] and not progress['assessment']['diagnostic_completed']
    running=[s for s in progress['result']['progress_samples'] if 'state: RUNNING' in s.get('alsa_status','')]
    assert len(running)==7 and all(s['registers']=={'1230':0,'1238':0} for s in running)
    assert all(re.search(r'^hw_ptr\s*:\s*0$',s['alsa_status'],re.M) for s in running)
    bore=re.search(r'^\[\s*(\d+)\].*> RECOVERY >',state['boot_reset'],re.M);assert bore
    result={'format':'audio-extra-firmware-public-v1','recovery_flash':flash,'recovery_boot':reboot,
            'boot_record_number':int(bore[1]),'firmware':firmware['files'],
            'arch_control_read':control,'added_control_names':sorted(new_names-old_names),'removed_control_names':[],
            'wifi':wifi,'digital_zero_trial':{
                'elapsed_seconds':progress['elapsed_seconds'],'assessment':progress['assessment'],
                'same_boot':progress['same_boot'],'running_snapshots':len(running),
                'hardware_pointer_in_all_running_snapshots':0,'rdma_status_registers':{'0x1230':0,'0x1238':0},
                'route_controls_restored':True,'amplifiers_enabled':False},
            'rollback_image_sha256':flash['before_sha256'],
            'limitations':['Twenty recovered firmware assets and18 newBluetooth codec controls do not fix the observed RDMA2 stall.',
                'Physical speaker/microphone, BluetoothHCI/pairing, NPUinference andcellular remainunaccepted.',
                'First hash probe usedthewrongroot; first controlprobe assumed1736; neither counted as acceptance.',
                'NativeDNSfailed onanearliercheckinthisboot; later13/13doesnotprovesustainedreliability.']}
    OUT.mkdir(exist_ok=True);path=OUT/'audio-extras.json';path.write_text(json.dumps(result,indent=2,sort_keys=True)+'\n')
    (OUT/'audio-extras-manifest.json').write_text(json.dumps({'files':{path.name:__import__('hashlib').sha256(path.read_bytes()).hexdigest()},'format':result['format']},indent=2)+'\n')


def export_late_control_checkpoint():
    reboot=load('audio-control-late-reboot/result.json')
    assert reboot['new_boot'] and reboot['actual_mode']=='recovery'
    assert reboot['continuous_uptime_seconds']>=90 and reboot['model_healthy']
    assert not reboot['pending_kill_tasks']
    checks=load('audio-control-late-postboot/receipt.json')['checks']
    assert all(x['returncode']==0 for x in checks.values())
    state=checks['state']['result']; control=checks['arch-control']['result']; wifi=checks['wifi']['result']
    assert state['card_id']=='RainbowPrince' and control['control_count']==1736
    assert state['startup_sha256']=='76a759049766100e57127363c3cd5141a486c061df4783560e8a3c2a918dae96'
    assert len(state['control_mount_lines'])==1
    nodes=state['arch_sound_nodes']
    assert len(nodes)==1 and nodes[0]['name']=='controlC0' and nodes[0]['character']
    assert (nodes[0]['major'],nodes[0]['minor'],nodes[0]['uid'],nodes[0]['mode'])==(116,114,0,'0o660')
    assert 'late optional ALSA controlC0 bound; PCM nodes remain hidden' in state['startup_log']
    assert wifi['all_checks_passed'] and len(wifi['checks'])==13
    bore=re.search(r'^\[\s*(\d+)\].*> RECOVERY >',state['boot_reset'],re.M)
    assert bore
    bt=json.loads((ROOT/'rootfs/hardware-reuse-20260921/bt-trials/nvm-3m-first/receipt.json').read_text())
    assert bt['returncode']==1 and bt['same_boot'] and bt['after_vote_check']==0
    assert 'non_event_byte=ff' in bt['uart_output'] and 'patch_transmitted_bytes=' not in bt['uart_output']
    qwen=json.loads((ROOT/'rootfs/main-driver-loop-usb-20260922/nvm03/receipt.json').read_text())
    assert qwen['pi_transport']['valid_transport'] and qwen['pi_transport']['requested_output_only']
    bt_success={}
    for trial_name in ('baud-precomputed-first','nvm-precomputed-first'):
        r=json.loads((ROOT/'rootfs/hardware-reuse-20260921/bt-trials'/trial_name/'receipt.json').read_text())
        assert r['returncode']==0 and r['same_boot'] and r['after_vote_check']==0
        assert r['before']['model']==r['after']['model']=='ok'
        assert r['after_metadata_exit']==r['kernel_capture_exit']==r['strace_capture_exit']==0
        assert 'precomputed_termios=yes' in r['uart_output'] and 'baud_transport_3m_identity=PASS' in r['uart_output']
        item={k:r[k] for k in ('started_at','elapsed','binary_sha256','returncode','same_boot','after_vote_check')}
        if trial_name=='nvm-precomputed-first':
            events=re.findall(r'^nvm_segment=(\d+) bytes=(\d+) reply=(.*)$',r['uart_output'],re.M)
            assert [int(e[0]) for e in events]==list(range(1,30))
            assert [int(e[1]) for e in events]==[243]*28+[219]
            assert all(e[2]=='04 0e 05 01 00 fc 00 1e' for e in events)
            assert 'nvm_transmitted_bytes=7023 acknowledged_segments=29 reset_sent=no hci_attached=no power_off_next=yes' in r['uart_output']
            item.update({'configuration_bytes':7023,'acknowledged_segments':29,
                         'diagnostic_address_all_zero':True,'hci_reset_sent':False,'hci_attached':False,'pairing_accepted':False})
        bt_success[trial_name]=item
    result={'format':'main-driver-loop-late-control-v1','recovery_reboot':reboot,
            'boot_record_number':int(bore[1]),'audio_control_startup':{
                'startup_sha256':state['startup_sha256'],'arch_control_count':control['control_count'],
                'arch_sound_nodes':nodes,'automatic_control_only_bind':True,
                'speaker_or_microphone_accepted':False},
            'wifi':wifi,'qwen_usb_review':{k:qwen[k] for k in ('provider','model','pi_transport','review_sha256','timestamp')},
            'bluetooth_precomputed_transport':bt_success,
            'bluetooth_configuration_trial':{'returncode':bt['returncode'],'elapsed_seconds':bt['elapsed'],
                'binary_sha256':bt['binary_sha256'],'same_boot':bt['same_boot'],
                'failure_phase':'baud-change response; received non-event ff before timeout',
                'patch_or_nvm_sent':False,'bluetooth_power_vote_released':True,
                'hci_pairing_accepted':False},
            'limitations':['An earlier same-image boot exposed missing control-node creation; this late hook fixes that specific startup gap.',
                'Earlier native DNS checks failed intermittently. This fresh 13/13 result is not a sustained-reliability claim.',
                'Qwen source review is advisory, not hardware acceptance. Invalid output-path attempts are not counted.',
                'No UUIDs, raw UART/kernel traces, vendor bytes, or identities exported.']}
    OUT.mkdir(exist_ok=True)
    path=OUT/'late-control.json';path.write_text(json.dumps(result,indent=2,sort_keys=True)+'\n')
    (OUT/'late-control-manifest.json').write_text(json.dumps({'files':{path.name:__import__('hashlib').sha256(path.read_bytes()).hexdigest()},
        'format':result['format']},indent=2)+'\n')


def export_continuation():
    """Export bounded continuation metadata without raw logs or identities."""
    audio = load('audio-progress-first/receipt.json')
    samples = audio['result']['progress_samples']
    assert len(samples) == 8
    running = [s for s in samples if 'state: RUNNING' in s['alsa_status']]
    assert len(running) == 7
    assert all(s['registers'] == {'1230': 0, '1238': 0} for s in running)
    assert audio['same_boot'] and audio['kernel_capture_exit'] == 0
    assert audio['trace_capture_exit'] == audio['after_audio_exit'] == 0
    assert audio['result']['child_deadline_exceeded']
    assert audio['before']['model'] == audio['after']['model'] == 'ok'
    assert audio['result']['after_status'] == 'closed'
    assert audio['result']['restored']['ABOX SPUS OUT2']['value'] == '0'
    assert audio['result']['restored']['ABOX UAIF1 SPK']['value'] == '0'
    assert all(s['reset_count'] == '0' for s in samples)
    assert all(s['runtime_status']=='active' and s['cache_only']=='N' and s['service']=='1' for s in samples)
    pointers = [[int(re.search(r'^'+name+r'\s*:\s*(\d+)$',s['alsa_status'],re.M)[1])
                 for s in running] for name in ('hw_ptr','appl_ptr')]
    assert pointers == [[0]*7,[8192]*7]
    progress = {
        'started_at': audio['started_at'],
        'elapsed_seconds': audio['elapsed_seconds'],
        'same_boot': audio['same_boot'],
        'helper_sha256': audio['helper_sha256'],
        'observer_sha256': audio['observer_sha256'],
        'supervisor_sha256': audio['supervisor_sha256'],
        'snapshot_count': len(samples),
        'running_snapshot_count': len(running),
        'running_status_registers': {'0x1230': 0, '0x1238': 0},
        'running_hw_ptr_values': pointers[0],
        'running_appl_ptr_values': pointers[1],
        'runtime_status_values': sorted({s['runtime_status'] for s in samples}),
        'reset_count_values': ['0'],
        'timeout_cleanup_verified': True,
        'route_controls_restored': True,
        'physical_playback_verified': False,
    }

    fwlog = load('audio-fw-log-after-progress-v2/receipt.json')
    assert fwlog['runtime_pm_before_after'] == 'suspended'
    assert not fwlog['firmware_flush_requested']
    assert not fwlog['register_or_sram_dump']
    fwlog_public = {
        'offset': fwlog['offset'], 'bytes': fwlog['bytes'],
        'sha256': fwlog['sha256'],
        'runtime_pm_before_after': fwlog['runtime_pm_before_after'],
        'firmware_flush_requested': fwlog['firmware_flush_requested'],
        'register_or_sram_dump': fwlog['register_or_sram_dump'],
        'log_cursor_consumed': fwlog['log_cursor_consumed'],
    }
    review = load('audio-progress-source-review/review-receipt.json')
    assert review['returncode'] == 0 and review['ok']
    source_review = {k: review[k] for k in
                     ('returncode', 'elapsed_seconds', 'tool_count', 'ok')}

    bluetooth = {}
    for name in ('baud-3m-first', 'segment-3m-first', 'full-patch-3m-first', 'postpatch-board-3m-first'):
        r = json.loads((ROOT / 'rootfs/hardware-reuse-20260921/bt-trials' /
                        name / 'receipt.json').read_text())
        assert r['same_boot'] and r['kernel_capture_exit'] == 0
        assert r['after_metadata_exit'] == 0 and r['after_vote_check'] == 0
        assert r['strace_capture_exit'] == 0
        assert r['before']['model'] == r['after']['model'] == 'ok'
        uart=r['uart_output']
        assert 'baud_transport_3m_identity=PASS' in uart
        assert 'raw=04 0e 04 01 48 fc 01\n' in uart
        item = {k: r[k] for k in ('started_at', 'returncode', 'elapsed',
                                  'source_sha256', 'accepted_source_sha256',
                                  'binary_sha256', 'same_boot',
                                  'kernel_capture_exit', 'after_metadata_exit',
                                  'after_vote_check', 'strace_capture_exit')}
        if name == 'baud-3m-first':
            assert r['returncode'] == 0
            item.update({'protocol': 'baud-change-command-complete',
                         'baud_3m_transport': True, 'hci_attached': False,
                         'firmware_transmitted': False,
                         'baud_reply_hex':'040e040148fc01',
                         'standard_zero_status_not_claimed':True})
        elif name == 'segment-3m-first':
            assert r['returncode'] == 1
            assert 'patch_prefix_transmitted_bytes=243' in uart
            item.update({'protocol': 'patch-segment-transport',
                         'bytes_transmitted':243,'segments_sent': 1, 'intermediate_ack_expected': False,
                         'timeout_mode_expected': True, 'full_firmware_transmitted': False,
                         'nvm_transmitted': False, 'rejection_proven': False})
        else:
            assert r['returncode'] == 0
            assert 'patch_transmitted_bytes=195848 segments=806 download_mode=3 nvm_transmitted=no' in uart
            assert 'raw=04 0e 05 01 00 fc 00 1e\n' in uart
            item.update({'protocol': 'full-patch-download',
                         'bytes_transmitted': 195848, 'segments': 806,
                         'download_mode': 3, 'final_status': 0,
                         'final_ack_parameter': '0x1e',
                         'ram_transfer_proven': True, 'hci_accepted': False,
                         'nvm_transmitted': False})
            if name=='postpatch-board-3m-first':
                assert 'postpatch_board raw=04 0e 08 01 00 fc 00 23 02 00 00\n' in uart
                assert 'postpatch_board_response=PASS nvm_transmitted=no hci_attached=no' in uart
                item['postpatch_board_query_accepted']=True
                item['postpatch_board_payload_hex']='020000'
        bluetooth[name] = item

    result = {'format': 'main-driver-loop-continuation-public-v1',
              'audio_progress_first': progress,
              'audio_fw_log_after_progress_v2': fwlog_public,
              'audio_progress_source_review': source_review,
              'bluetooth': bluetooth,
              'limitations': [
                  'Audio status-register non-progress is not physical playback acceptance.',
                  'Firmware log read is metadata/hash only; raw log bytes are excluded.',
                  'Bluetooth RAM transfer does not prove HCI, modem, NVM, or pairing acceptance.',
                  'No UUIDs, device identifiers, raw UART/kernel/strace logs, or vendor bytes exported.']}
    CONT_OUT.mkdir(exist_ok=True)
    (CONT_OUT / 'continuation.json').write_text(
        json.dumps(result, indent=2, sort_keys=True) + '\n')
    digest = __import__('hashlib').sha256(
        (CONT_OUT / 'continuation.json').read_bytes()).hexdigest()
    (CONT_OUT / 'continuation-manifest.json').write_text(json.dumps(
        {'format': result['format'], 'files': {'continuation.json': digest},
         'excluded': ['raw logs', 'UUIDs', 'device IDs', 'vendor payload bytes']},
        indent=2, sort_keys=True) + '\n')


def main():
    export_audio_extras_checkpoint()
    export_late_control_checkpoint()
    export_continuation()
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
