#!/usr/bin/env python3
"""Export only curated, non-sensitive measurement receipts, not vendor blobs."""
import hashlib
import json
import re
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
source=ROOT/'rootfs/gpu-compat-20260921/trials'
dest=ROOT/'evidence/gpu-compat-20260921'
dest.mkdir(exist_ok=True)
names=['real-load-nodev','headless-load-nodev','headless-load-proc',
       'headless-enumerate-nodev','headless-enumerate-render','headless-enumerate-devtmpfs',
       'headless-enumerate-sgpu','headless-compute256-first',
       'headless-compute256-after-recovery','headless-compute1024-clean',
       'headless-compute256-clean-third','headless-capabilities',
       'vulkan-load-nodev','vulkan-load-closure','vulkan-enumerate-sgpu',
       'vulkan-compute256-first','vulkan-compute256-second','vulkan-compute256-third',
       'llama-vulkan-help','llama-vulkan-list-v2','llama-vulkan-list-v2-traced',
       'llama-vulkan-list-v3-stages','llama-vulkan-list-v4-clearerror',
       'llama-vulkan-mulmat-small']
bench_names={'llama-vulkan-qwen08-gpu-bench',
             'llama-vulkan-qwen08-gpu-bench-repeat',
             'llama-vulkan-qwen08-cpu-strict-repeat',
             'llama-vulkan-qwen08-gpu-text',
             'llama-vulkan-qwen08-cpu-text'}
names.extend(sorted(bench_names))
vulkan_compute_re = re.compile(
    r'^COMPUTE256 checksum=(?P<checksum>[0-9]+) device=(?P<device>.+) PASS$', re.MULTILINE)
llama_operation_re = re.compile(r'^\s*MUL_MAT\([^\n]+\): .*OK\s*$', re.MULTILINE)
ansi_re = re.compile(r'\x1b\[[0-9;]*m')
trace_gipa_re = re.compile(r'^VULKAN_BRIDGE_TRACE GIPA:.*\n?', re.MULTILINE)
hmi_pointer_re = re.compile(r'\b(module|methods|open)=0x[0-9a-fA-F]+')
offload_re = re.compile(r'offloaded (?P<loaded>[0-9]+)/(?P<total>[0-9]+) layers to GPU')
model_buffer_re = re.compile(r'(?:Vulkan0|CPU_Mapped) model buffer size =\s+(?P<mib>[0-9.]+) MiB')
eval_speed_re = re.compile(
    r'(?m)^(?!.*prompt eval time).*eval time =.*?(?P<tps>[0-9]+\.[0-9]+) tokens per second')
summary=[]
for name in names:
    raw=source/name
    r=json.loads((raw/'receipt.json').read_text())
    # Exclude boot UUID, full proc/sys logs, raw traces, and firmware binaries.
    public={key:r[key] for key in ['started_at','variant','name','command','returncode',
                                  'same_boot','kernel_capture_exit','gpu_messages','helper_sha256']}
    if 'bridge' in r:
        public['bridge']=r['bridge']
    public['read_only_views']=r.get('read_only_views',[])
    public['elapsed_seconds']=r.get('elapsed_seconds')
    public['phone_before']={key:r['before'][key] for key in ['pid1','temperature','profile','model']}
    public['phone_after']={key:r['after'][key] for key in ['pid1','temperature','profile','model']}
    output=(raw/'stdout.txt').read_text()
    errors=(raw/'stderr.txt').read_text()
    normalized_output=ansi_re.sub('', output)
    # Raw stdout/stderr/trace files remain in the private rootfs trial tree;
    # public evidence receives only the curated receipt and measurements.
    public_errors=trace_gipa_re.sub('', errors)
    public_errors=hmi_pointer_re.sub(r'\1=0xREDACTED', public_errors)
    public['backend_numerical_tests'] = None
    public['operation_numerical_pass'] = None
    public['operation_pass'] = None
    public['reference_correct'] = None
    public['cpu_fallback_used'] = None
    if r.get('variant') == 'vulkan-headless':
        match = vulkan_compute_re.search(output)
        public['numerical_checksum'] = match.group('checksum') if match else None
        public['numerical_device'] = match.group('device') if match else None
        public['numerical_compute_pass'] = bool(
            match and match.group('checksum') == '99712' and
            match.group('device') == 'Samsung Xclipse 920' and r['returncode'] == 0)
    elif r.get('variant') == 'llama-vulkan':
        public['numerical_checksum'] = None
        public['numerical_device'] = 'Samsung Xclipse 920' if 'Samsung Xclipse 920' in normalized_output else None
        public['numerical_compute_pass'] = None
        if name == 'llama-vulkan-mulmat-small':
            operation_count=len(llama_operation_re.findall(normalized_output))
            public['backend_numerical_tests'] = operation_count
            public['operation_numerical_pass'] = bool(
                operation_count == 6 and '6/6 tests passed' in normalized_output and
                'Backend Vulkan0:' in normalized_output and 'Backend 2/2: CPU' in normalized_output and
                'Skipping' in normalized_output and r['returncode'] == 0)
            public['operation_pass'] = public['operation_numerical_pass'] and r['same_boot'] and not r['gpu_messages'] and r['kernel_capture_exit'] == 0
            public['reference_correct'] = public['operation_pass']
            public['cpu_fallback_used'] = False if public['operation_pass'] else None
    else:
        public['numerical_checksum'] = 'verified' if 'checksum=verified PASS' in output else None
        public['numerical_device'] = None
        public['numerical_compute_pass']='checksum=verified PASS' in output and r['returncode']==0
        public['backend_numerical_tests'] = None
        public['operation_numerical_pass'] = None
        public['operation_pass'] = None
        public['reference_correct'] = None
        public['cpu_fallback_used'] = None
    if name in bench_names:
        public['raw_output_private_only'] = True
        public['benchmark_role'] = 'cpu-control' if 'cpu-' in name else 'vulkan-gpu'
        public['warmup_class'] = (
            'cold-no-warmup' if '--no-warmup' in r['command'] else 'warmed-repeat')
        public['benchmark_results'] = []
        json_start=output.find('[', output.find('\n'))
        if json_start >= 0:
            try:
                results=json.loads(output[json_start:])
            except json.JSONDecodeError:
                results=[]
            for result in results if isinstance(results, list) else []:
                public['benchmark_results'].append({key: result.get(key) for key in (
                    'n_prompt','n_gen','avg_ts','stddev_ts','samples_ts')})
        public['text_output_observed'] = None
        public['text_payload_sha256'] = None
        public['text_reference_match'] = None
        if name.endswith('-text'):
            payload=output.split('\n', 1)[1] if '\n' in output else output
            public['text_output_observed'] = bool(payload.strip())
            public['text_payload_sha256'] = hashlib.sha256(payload.encode()).hexdigest()
            counterpart=('llama-vulkan-qwen08-cpu-text' if 'gpu-text' in name
                         else 'llama-vulkan-qwen08-gpu-text')
            counterpart_output=(source/counterpart/'stdout.txt').read_text()
            counterpart_payload=(counterpart_output.split('\n', 1)[1]
                                 if '\n' in counterpart_output else counterpart_output)
            public['text_reference_match'] = payload == counterpart_payload
        offload=offload_re.findall(errors)
        public['offloaded_layers'] = (
            {'loaded': int(offload[-1][0]), 'total': int(offload[-1][1])}
            if offload else None)
        model_buffer=model_buffer_re.search(errors)
        public['model_buffer_mib'] = float(model_buffer.group('mib')) if model_buffer else None
        eval_speed=eval_speed_re.search(errors)
        public['completion_eval_tokens_per_second'] = float(eval_speed.group('tps')) if eval_speed else None
    public['clean_numerical_pass']=public['numerical_compute_pass'] and not r['gpu_messages'] and r['same_boot'] and r['kernel_capture_exit']==0
    (dest/(name+'.json')).write_text(json.dumps(public,indent=2)+'\n')
    if name not in bench_names:
        public_output=hmi_pointer_re.sub(r'\1=0xREDACTED', output)
        # Raw bytes remain private; normalize only public presentation whitespace.
        (dest/(name+'.txt')).write_text('\n'.join(
            line.rstrip() for line in (public_output+public_errors).splitlines())+'\n')
    summary.append(dict(name=name,returncode=r['returncode'],
                        numerical_compute_pass=public['numerical_compute_pass'],
                        backend_numerical_tests=public['backend_numerical_tests'],
                        operation_numerical_pass=public['operation_numerical_pass'],
                        operation_pass=public['operation_pass'],
                        reference_correct=public['reference_correct'],
                        cpu_fallback_used=public['cpu_fallback_used'],
                        benchmark_role=public.get('benchmark_role'),
                        warmup_class=public.get('warmup_class'),
                        offloaded_layers=public.get('offloaded_layers'),
                        text_reference_match=public.get('text_reference_match'),
                        clean_numerical_pass=public['clean_numerical_pass'],
                        new_gpu_messages=len(r['gpu_messages'])))
(dest/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
helper=(ROOT/'tools/gpu-compat/exec-isolated.py').read_bytes()
if hashlib.sha256(helper).hexdigest()=='21fcf21e62e5ab722dea16b19acf0aa0f127f6582c7acc0b5166889f5679b27d':
    (dest/'exec-isolated-tested.py').write_bytes(helper)
print(json.dumps(summary,indent=2))
