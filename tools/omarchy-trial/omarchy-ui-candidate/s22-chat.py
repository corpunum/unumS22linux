#!/usr/bin/env python3
"""Small local CPU chat client for the RAM-staged Qwen3.5 2B runtime.

This is not an agent and never executes model-generated commands. Its launcher
persists in Alpine; model weights and runtime currently do not survive reboot.
"""
import os
from pathlib import Path
import subprocess
import sys
import time

BASE = Path('/mnt/model-bench')
MODEL = BASE / 'models/Qwen3.5-2B-Q4_0.gguf'
RUNTIME = BASE / 'optimized'
LOADER = BASE / 'libc/ld-linux-aarch64.so.1'


def prompt_for(messages):
    result = ('<|im_start|>system\nYou are a concise assistant running locally '
              'on a Samsung S22 under Linux. Do not pretend to execute commands. '
              'Answer the user directly.\n<|im_end|>\n')
    for role, content in messages:
        result += f'<|im_start|>{role}\n{content}\n<|im_end|>\n'
    # Explicit non-thinking Qwen prefix. This pinned completion frontend does
    # not pass its --reasoning setting to the model template consistently.
    return result + '<|im_start|>assistant\n<think>\n\n</think>\n\n'


def main():
    if not all(path.is_file() for path in (MODEL, LOADER, RUNTIME / 'bin/llama-completion')):
        print('The chat launcher is installed, but its RAM-staged model/runtime is absent.')
        print('Restore it from the connected host with tools/model-bench/stage-phone.sh.')
        return 1
    print('\nS22 local chat | Qwen3.5 2B Q4 | CPU, four fast cores')
    print('No cloud, no command execution. Type /quit to return to Linux.')
    print('Models are currently RAM-staged and need restoring after reboot.\n')
    messages = []
    while True:
        try:
            question = input('You> ').strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if question == '/quit':
            return 0
        if not question:
            continue
        candidate = messages + [('user', question)]
        while len(prompt_for(candidate).encode()) > 3500 and len(candidate) > 1:
            candidate = candidate[2:]
        prompt = prompt_for(candidate)
        if len(prompt.encode()) > 3500:
            print('Please shorten that message to fit the small local context.')
            continue
        command = [
            'nice', '-n', '20', str(LOADER), '--library-path',
            f'{BASE}/libc:{RUNTIME}/lib', str(RUNTIME / 'bin/llama-completion'),
            '-m', str(MODEL), '-t', '4', '-C', 'f0', '--cpu-strict', '1',
            '-c', '4096', '-n', '256', '-ngl', '0', '--no-conversation',
            '--no-display-prompt', '--temp', '0.6', '-p', prompt,
        ]
        print('S22> ', end='', flush=True)
        started = time.monotonic()
        answer = []
        with open('/tmp/s22-chat-last.log', 'w') as log:
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=log,
                                       text=True, errors='replace')
            try:
                while True:
                    part = process.stdout.read(1)
                    if not part:
                        break
                    answer.append(part)
                    print(part, end='', flush=True)
                status = process.wait()
            except KeyboardInterrupt:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                print('\nStopped.\n')
                continue
        if status:
            print(f'\nModel stopped with status {status}; see /tmp/s22-chat-last.log.\n')
            continue
        response = ''.join(answer).replace('[end of text]', '').strip()
        messages = candidate + [('assistant', response)]
        print(f'\n[{time.monotonic() - started:.1f}s]\n')


if __name__ == '__main__':
    sys.exit(main())
