import importlib.util
from pathlib import Path
import unittest
from unittest import mock

spec=importlib.util.spec_from_file_location('agent_web', Path(__file__).with_name('start-agent-web.py'))
m=importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class CommandTest(unittest.TestCase):
    def test_private_listener(self):
        args=m.command()
        self.assertEqual(args[args.index('--interface')+1], '127.0.0.1')
        self.assertEqual(args[args.index('--port')+1], '8093')

    def test_unprivileged_server(self):
        args=m.command()
        for flag in ['--reuid=1000','--regid=1000','--clear-groups','--no-new-privs','--bounding-set=-all']:
            self.assertIn(flag,args)
        self.assertLess(args.index('/usr/bin/setpriv'), args.index(m.TTYD))

    def test_origin_identity_and_single_client(self):
        args=m.command()
        self.assertIn('--check-origin',args)
        self.assertEqual(args[args.index('--auth-header')+1], 'Tailscale-User-Login')
        self.assertEqual(args[args.index('--max-clients')+1], '1')
        self.assertNotIn('--url-arg', args)
        self.assertNotIn('--browser', args)

    def test_dedicated_pi_history(self):
        script=Path(__file__).with_name('pi-web-session').read_text()
        self.assertIn('/usr/local/bin/pi --session-dir /home/alarm/.pi/agent/web-sessions --continue',script)
        self.assertIn('new-session -A -s pi',script)
        self.assertIn(m.TMUX_SOCKET,script)
        self.assertNotIn('pi-rig',script)
        self.assertEqual(m.command()[-1], '/usr/local/bin/pi-web-session')

    def test_reuses_existing_correct_virtual_ptmx(self):
        import os, stat
        node=mock.Mock(st_mode=stat.S_IFCHR|0o666, st_rdev=os.makedev(5,2))
        with mock.patch.object(m.Path,'stat',return_value=node), \
             mock.patch.object(m.Path,'is_symlink',return_value=False), \
             mock.patch.object(m.os,'mknod') as mknod:
            m.prepare_ptmx()
            mknod.assert_not_called()

    def test_rejects_wrong_virtual_ptmx(self):
        import os, stat
        node=mock.Mock(st_mode=stat.S_IFCHR|0o666, st_rdev=os.makedev(5,2))
        wrong=mock.Mock(st_mode=stat.S_IFCHR|0o666, st_rdev=os.makedev(1,3))
        with mock.patch.object(m.Path,'stat',side_effect=[node,wrong]), \
             mock.patch.object(m.Path,'is_symlink',return_value=False), \
             mock.patch.object(m.os,'mknod') as mknod:
            with self.assertRaises(AssertionError): m.prepare_ptmx()
            mknod.assert_not_called()


if __name__=='__main__': unittest.main()
