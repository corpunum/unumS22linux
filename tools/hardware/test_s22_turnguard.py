#!/usr/bin/env python3
"""Host tests for s22-turnguard (no phone needed): python3 tools/hardware/test_s22_turnguard.py"""
import importlib.machinery
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
loader = importlib.machinery.SourceFileLoader('s22_turnguard', str(HERE / 's22-turnguard.py'))
spec = importlib.util.spec_from_loader('s22_turnguard', loader)
G = importlib.util.module_from_spec(spec)
loader.exec_module(G)


def check(cond, msg='check failed'):
    if not cond:          # not a bare assert: the host runner also runs python -O
        raise AssertionError(msg)


def iso(t):
    import datetime
    return datetime.datetime.fromtimestamp(t, datetime.timezone.utc).isoformat().replace('+00:00', 'Z')


def pend(sid, turn, started, preview='what time is it'):
    return {'sessionId': sid, 'turnId': turn, 'startedAt': iso(started), 'messagePreview': preview}


class GuardTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        G.STATE = self.tmp.name
        G.LOG = os.path.join(self.tmp.name, 'turnguard.jsonl')
        self.cancels, self.escalations, self.saved = [], [], []
        self.g = self.make()

    def make(self, state=None, cfg=None):
        return G.Guard(dict(G.CONFIG, **(cfg or {})), state, cancel=self.cancel,
                       escalate=lambda: self.escalations.append(1) or {'ok': True, 'aborted': 1},
                       save=lambda st: self.saved.append(json.loads(json.dumps(st))))

    def cancel(self, sid):
        self.cancels.append(sid)
        return {'ok': True, 'cancelled': True}

    def tearDown(self):
        self.tmp.cleanup()

    def events(self):
        return [json.loads(x)['event'] for x in Path(G.LOG).read_text().splitlines()] if Path(G.LOG).exists() else []

    def test_classes(self):
        c = G.CONFIG
        check(G.classify('voice', c) == ('voice', 300.0))
        check(G.classify('voice-conv-20261022-101010', c) == ('voice', 300.0))
        check(G.classify('voiceover', c)[0] == 'other')
        check(G.classify('touch', c) == ('touch', 900.0))
        check(G.classify('mission-abc', c) == ('other', 0.0))

    def test_voice_turn_cancelled_at_limit_and_ends(self):
        t0 = 1000.0
        self.g.step([pend('voice', 'a', t0)], t0 + 10)
        check(self.cancels == [])
        self.g.step([pend('voice', 'a', t0)], t0 + 299)
        check(self.cancels == [])
        self.g.step([pend('voice', 'a', t0)], t0 + 301)
        check(self.cancels == ['voice'])
        self.g.step([], t0 + 305)
        check(self.events() == ['turn_start', 'timeout_cancel', 'turn_end'], self.events())
        end = json.loads(Path(G.LOG).read_text().splitlines()[-1])
        check(end['cancelled'] is True and end['after_cancel_s'] == 4.0, end)

    def test_unlimited_sessions_are_only_tracked(self):
        self.g.step([pend('mission-1', 'x', 0)], 99999)
        check(self.cancels == [] and self.events() == ['turn_start'])

    def test_recancel_then_escalate_to_proxy(self):
        t0 = 0.0
        p = [pend('touch', 'b', t0)]
        self.g.step(p, 901)
        self.g.step(p, 910)                     # within grace: nothing
        check(self.cancels == ['touch'])
        self.g.step(p, 921)
        self.g.step(p, 941)
        check(self.cancels == ['touch'] * 3)
        self.g.step(p, 961)
        check(self.escalations == [1])
        self.g.step(p, 990)
        check(self.escalations == [1])           # once
        check('escalate_abort' in self.events())

    def test_retry_budget_cuts_the_same_message_at_once(self):
        self.g.step([pend('voice', 't1', 0)], 301)
        self.g.step([], 302)
        self.g.step([pend('voice', 't2', 310)], 611)          # second attempt also times out
        self.g.step([], 612)
        check(self.cancels == ['voice', 'voice'])
        self.g.step([pend('voice', 't3', 620)], 621)          # third attempt: cut immediately
        check(self.cancels == ['voice'] * 3)
        check(self.events().count('retry_budget_cancel') == 1)
        self.g.step([pend('voice', 't4', 630, preview='something else')], 631)
        check(len(self.cancels) == 3)                          # other message: normal limit
        self.g.step([pend('voice', 't5', 3000)], 3001)         # window over: allowed again
        check(len(self.cancels) == 3)

    def test_state_survives_guard_restart_and_openunum_start_time_wins(self):
        self.g.step([pend('voice', 'a', 100)], 150)
        st = self.saved[-1]
        g2 = self.make(state=st)               # guard restarted: the clock is not reset
        g2.step([pend('voice', 'a', 100)], 401)
        check(self.cancels == ['voice'])
        self.g = self.make()
        self.g.step([{'sessionId': 'voice', 'turnId': 'z', 'ageMs': 250000}], 1000)   # no startedAt: ageMs
        self.g.step([{'sessionId': 'voice', 'turnId': 'z', 'ageMs': 250000}], 1051)
        check(self.cancels == ['voice', 'voice'])

    def test_cancel_errors_are_logged_not_raised(self):
        g = G.Guard(dict(G.CONFIG), None, cancel=lambda sid: (_ for _ in ()).throw(OSError('down')),
                    escalate=lambda: {}, save=lambda st: None)
        g.step([pend('voice', 'a', 0)], 400)
        rec = [json.loads(x) for x in Path(G.LOG).read_text().splitlines()][-1]
        check(rec['event'] == 'timeout_cancel' and rec['ok'] is False, rec)


if __name__ == '__main__':
    unittest.main()
