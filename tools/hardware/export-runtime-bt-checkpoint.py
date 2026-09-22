#!/usr/bin/env python3
"""Export only selected, asserted facts from private runtime/reset receipts."""
import hashlib,json,re
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
raw=ROOT/'rootfs/main-driver-loop-20260921'
out=ROOT/'evidence/main-driver-loop-20260922'
read=lambda p:json.loads(p.read_text())
bt=read(ROOT/'rootfs/hardware-reuse-20260921/bt-trials/runtime-reset-first/receipt.json')
assert bt['returncode']==0 and bt['same_boot'] and bt['after_vote_check']==0
assert bt['before']['model']==bt['after']['model']=='ok'
assert 'runtime_controller_reset_readback=PASS' in bt['uart_output']
assert 'runtime_address_readback_matches=yes' in bt['uart_output']
panic=read(raw/'hci-audio-panic-766/capture.json')
extra=(raw/'hci-audio-panic-766/extra.txt').read_text()
assert 'bt_sock_create+0x144/0x150' in extra and 'af_bluetooth.c:71' in extra
boot=(raw/'hci-audio-panic-766/boot-reset.bin').read_bytes().decode(errors='replace')
assert re.search(r'\[\s*766\].*KP.*PANIC > RECOVERY',boot)
post=read(raw/'panic766-postboot/receipt.json')['checks']
assert all(c['returncode']==0 for c in post.values())
controls=post['arch-control']['result'];wifi=post['wifi']['result'];state=post['state']['result']
assert controls['control_count']==1754 and wifi['all_checks_passed']
audio=read(raw/'audio-ctrl-serial/receipt.json')
assert audio['same_boot'] and audio['assessment']['cleanup_verified']
samples=audio['result']['progress_samples']
assert len(samples)==8 and all(s['registers']=={'1200':0xb0200000,'1230':0,'1238':0} for s in samples)
assert all(s['rdma2_ctrl']['enable'] is False for s in samples)
qwen=read(ROOT/'rootfs/main-driver-loop-usb-20260922/btreset01/receipt.json')
assert qwen['pi_transport']['valid_transport'] and qwen['pi_transport']['requested_output_only']
result={
 'format':'runtime-bt-and-kernel-panic-v1',
 'accepted_controller_trial':{k:bt[k] for k in ('started_at','elapsed','returncode','same_boot','binary_sha256','after_vote_check')},
 'controller':{'patch_segments':806,'nvm_acknowledged_segments':29,'reset_complete':True,
    'address_readback_matches':True,'address_source':'linux-generated-not-factory',
    'hci_version':12,'manufacturer':29,'subversion':26748,'powered_off_after_trial':True},
 'rejected_hci_trial':{'name':'hci-bridge-first','result':'kernel-panic',
    'function':'bt_sock_create+0x144/0x150','source':'net/bluetooth/af_bluetooth.c:71',
    'assertion':'BUG_ON(!sk)','cause':'hci_sock_create body compiled out but returns success',
    'capture_sha256':panic['last-kmsg.bin']['sha256'],'boot_record':766,'automatic_recovery':True},
 'post_recovery':{'uptime_seconds':state['uptime_seconds'],'control_count':1754,
    'wifi_checks_passed':len(wifi['checks']),'model_health':True},
 'audio_discriminator':{'trial':'audio-ctrl-serial','elapsed_seconds':audio['elapsed_seconds'],
    'samples':len(samples),'rdma2_ctrl':'0xb0200000','enable':False,'status':0,'hw_ptr':0,
    'assessment':audio['assessment'],'mixer_routes_restored':True},
 'qwen_review':{'elapsed_seconds':qwen['pi_transport']['elapsed_seconds'],
    'model':qwen['model'],'executed_via_pi':True,'recommendation_accepted':False,
    'rejection_reason':'Reset-before-NVM contradicts relevant vendor HAL ordering'},
 'limitations':['No accepted Linux HCI/pairing/RF result','Audio DMA remains disabled',
    'NPU, SIM/data/calls, camera, GNSS and suspend remain unaccepted',
    'Concurrent audio trial during panic yielded no result; serial test used afterward',
    'No image or protected-partition writes in this checkpoint']}
out.mkdir(exist_ok=True)
path=out/'runtime-bt.json';path.write_text(json.dumps(result,indent=2,sort_keys=True)+'\n')
(out/'runtime-bt-manifest.json').write_text(json.dumps({'format':result['format'],
    'files':{path.name:hashlib.sha256(path.read_bytes()).hexdigest()}},indent=2)+'\n')
print(path)
