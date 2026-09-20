#!/usr/bin/env python3
"""Offline terminal chat to the phone's loopback-only CPU model server."""
import json
import sys
import time
import urllib.error
import urllib.request

URL = 'http://127.0.0.1:8089'
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def prompt_for(messages):
    prompt = ('<|im_start|>system\nYou are a concise assistant running locally '
              'on a Samsung S22 under Linux. Do not pretend to execute commands. '
              'Answer directly and briefly.\n<|im_end|>\n')
    for role, content in messages:
        prompt += f'<|im_start|>{role}\n{content}\n<|im_end|>\n'
    return prompt + '<|im_start|>assistant\n<think>\n\n</think>\n\n'


def complete(prompt):
    request = urllib.request.Request(
        URL + '/completion',
        data=json.dumps({'prompt': prompt, 'n_predict': 256,
                         'temperature': 0.6, 'cache_prompt': True,
                         'stream': True, 'stop': ['<|im_end|>']}).encode(),
        headers={'Content-Type': 'application/json'})
    with OPENER.open(request, timeout=120) as response:
        for line in response:
            if not line.startswith(b'data: '):
                continue
            payload = line[6:].strip()
            if payload == b'[DONE]':
                return
            event = json.loads(payload)
            if 'error' in event:
                raise RuntimeError(str(event['error']))
            content = event.get('content', '')
            if content:
                yield content
            if event.get('stop'):
                return


def main():
    try:
        with OPENER.open(URL + '/health', timeout=3) as response:
            if json.load(response).get('status') != 'ok':
                raise RuntimeError('Model is not ready')
    except (OSError, ValueError, RuntimeError) as exc:
        print(f'Local model server is unavailable: {exc}')
        print('Host restore: tools/model-bench/stage-phone.sh, then start-phone-server.sh')
        return 1
    print('\nS22 local chat | Qwen3.5 2B Q4 | CPU')
    print('Offline. No command execution. /quit opens the shell.')
    print('Model stays in RAM between questions.\n')
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
            print('Please shorten that message to fit the local context.')
            continue
        print('S22> ', end='', flush=True)
        started = time.monotonic()
        answer = []
        first_token = None
        try:
            for part in complete(prompt):
                if first_token is None:
                    first_token = time.monotonic() - started
                answer.append(part)
                print(part, end='', flush=True)
        except (OSError, ValueError, RuntimeError, KeyboardInterrupt) as exc:
            print(f'\nRequest stopped: {exc}\n')
            continue
        if not answer:
            print('\nNo answer returned.\n')
            continue
        messages = candidate + [('assistant', ''.join(answer).strip())]
        print(f'\n[{time.monotonic() - started:.1f}s; first text {first_token:.1f}s]\n')


if __name__ == '__main__':
    sys.exit(main())
