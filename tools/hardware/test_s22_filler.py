#!/usr/bin/env python3
"""Host-only tests for s22_filler.py (shared filler clip player) and the filler wiring of every voice path."""
import importlib.util
import json
import os
import stat
import tempfile
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, HERE / file)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


F = load('s22_filler', 's22_filler.py')


class FillerPlay(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.chroot = root / 'chroot'
        self.bank = self.chroot / 'root/.openunum/assets/voice-fillers'
        for lang, cat in (('en', 'general'), ('el', 'general')):
            (self.bank / lang / cat).mkdir(parents=True)
            for n in (1, 2):
                (self.bank / lang / cat / f'{n}.wav').write_bytes(b'RIFF')
        (self.bank / 'manifest.json').write_text(json.dumps({'version': 1, 'clips': {
            l: {'general': [{'n': n, 'phrase': f'p{n}', 'durationSec': 1} for n in (1, 2)]} for l in ('en', 'el')}}))
        self.log = root / 'say.log'
        self.say = root / 'say'
        self.say.write_text(f'#!/bin/sh\necho "$@" >> {self.log}\n')
        self.say.chmod(self.say.stat().st_mode | stat.S_IXUSR)
        F.CHROOT, self.state = self.chroot, root / 'last.json'

    def tearDown(self):
        self.tmp.cleanup()

    def test_pick_returns_chroot_path_and_never_repeats(self):
        seen = []
        for _ in range(6):
            wav, info = F.pick('do the thing', root=self.bank, state_file=self.state)
            self.assertTrue(wav.startswith('/root/.openunum/assets/voice-fillers/en/general/'), wav)
            seen.append(info['n'])
        self.assertTrue(all(a != b for a, b in zip(seen, seen[1:])), seen)

    def test_greek_question_picks_greek_clip(self):
        wav, info = F.pick('κάνε το πράγμα', root=self.bank, state_file=self.state)
        self.assertEqual(info['lang'], 'el')
        self.assertIn('/el/general/', wav)

    def test_play_runs_s22_say_wav_without_render_env(self):
        proc, info = F.play('do the thing', out='earpiece', wait=True, say=str(self.say), root=self.bank,
                            state_file=self.state, env={**os.environ, 'S22_SAY_OUT': '/nope'})
        self.assertEqual(proc.returncode, 0)
        line = self.log.read_text().strip()
        self.assertTrue(line.startswith('--out earpiece --wav /root/.openunum/assets/voice-fillers/en/general/'), line)

    def test_play_without_clips_returns_none(self):
        proc, info = F.play('do the thing', say=str(self.say), root=self.bank / 'missing', state_file=self.state)
        self.assertIsNone(proc)

    def test_bank_follows_the_voice_that_will_answer(self):
        """2026-11-04: while the rig voice is down the answer is the phone's copy, so are the clips."""
        offline = self.bank.with_name('voice-fillers-el-antonis')
        self.assertEqual(F.bank_root(self.bank, offline, down=True), self.bank)    # offline bank not rendered
        offline.mkdir()
        (offline / 'manifest.json').write_text('{"version": 1, "clips": {}}')
        self.assertEqual(F.bank_root(self.bank, offline, down=True), offline)
        self.assertEqual(F.bank_root(self.bank, offline, down=False), self.bank)

    def test_render_fillers_engine_writes_its_own_bank(self):
        R = load('s22_render_fillers', 'voice/s22-render-fillers.py')
        self.assertEqual(R.default_out(None), R.DEFAULT_OUT)
        self.assertEqual(R.default_out('antonis'), R.DEFAULT_OUT)
        self.assertEqual(R.default_out('el-antonis').name, 'voice-fillers-el-antonis')
        self.assertEqual(Path(F.FILLER_DIR_OFFLINE).name, 'voice-fillers-el-antonis')


class Wiring(unittest.TestCase):
    """Every voice path has a filler hook (static checks; behaviour is tested in each path's own tests)."""

    def test_paths(self):
        read = lambda p: (HERE / p).read_text()
        self.assertIn('filler_bank', read('s22-assistant.py'))            # push-to-talk + Talk button
        self.assertIn('FillerBank', read('s22-converse.py') + read('s22-assistant.py'))
        self.assertIn('play_filler', read('voip/s22-sip.py'))              # SIP classic + fast voice
        self.assertIn("'filler'", read('../touchui/s22-touchd.py'))        # agent text_to_speech / shell chat
        self.assertIn('--filler', read('../touchui/s22-ui.py'))


if __name__ == '__main__':
    unittest.main()
