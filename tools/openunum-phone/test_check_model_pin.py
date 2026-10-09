#!/usr/bin/env python3
"""Host tests for tools/openunum-phone/check-model-pin.py (read-only checker)."""
import importlib.util, io, json, tempfile, unittest
from contextlib import redirect_stdout
from pathlib import Path

SPEC = importlib.util.spec_from_file_location('check_model_pin', Path(__file__).with_name('check-model-pin.py'))
MOD = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(MOD)
TEMPLATE = Path(__file__).with_name('openunum.json')


def run(*argv):
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = MOD.main(list(argv))
    return rc, buf.getvalue()


class CheckModelPinTest(unittest.TestCase):
    def test_repo_template_is_pinned_to_luna_with_routing_guards(self):
        rc, out = run('--config', str(TEMPLATE), '--routing')
        self.assertEqual((rc, out.strip()), (0, 'pinned'))

    def test_drift_and_unreadable(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'openunum.json'
            p.write_text(json.dumps({'model': {'provider': 'llama-cpp-local', 'model': 'llama-cpp-local/qwen'}}))
            before = p.read_text()
            rc, out = run('--config', str(p), '--json')
            self.assertEqual(rc, 1)
            self.assertEqual(len(json.loads(out)['problems']), 2)
            self.assertEqual(p.read_text(), before)              # never writes
            p.write_text(json.dumps({'model': {'provider': 'openai', 'model': 'openai/gpt-6-luna'}}))
            self.assertEqual(run('--config', str(p))[0], 0)
            rc, out = run('--config', str(p), '--routing')
            self.assertEqual(rc, 1)
            self.assertIn('fallbackEnabled', out)
            p.write_text('{"model": ')
            self.assertEqual(run('--config', str(p))[0], 2)
            self.assertEqual(run('--config', str(Path(d) / 'missing.json'))[0], 2)

    def test_custom_pin(self):
        doc = {'model': {'provider': 'openai', 'model': 'openai/gpt-6.1-sol'}}
        self.assertEqual(MOD.check(doc, 'openai', 'openai/gpt-6.1-sol'), [])
        self.assertTrue(MOD.check(doc, 'openai', 'openai/gpt-6-luna'))


if __name__ == '__main__':
    unittest.main()
