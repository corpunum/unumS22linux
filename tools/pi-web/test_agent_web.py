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

    def test_status_query_uses_only_dedicated_socket_and_never_starts_session(self):
        args=m.tmux_query_command()
        self.assertEqual(args[args.index('-S')+1], m.TMUX_SOCKET)
        self.assertEqual(args[args.index('list-panes')+1:args.index('list-panes')+4],
                         ['-a','-F','#{session_name}\t#{pane_pid}'])
        self.assertNotIn('new-session', args)
        self.assertNotIn('kill-server', args)

    def test_status_keeps_idle_web_service_ready_when_pi_session_is_absent(self):
        with mock.patch.object(m, 'runtime_process_readiness',
                               return_value={'desktop':True,'model_process':True}), \
             mock.patch.object(m, 'running', return_value=123), \
             mock.patch.object(m, 'endpoint_ready', side_effect=[True,True]) as endpoint, \
             mock.patch.object(m, 'inspect_pi_session',
                               return_value={'status':'absent','ready':False}) as inspect:
            result=m.collect_readiness()

        self.assertTrue(result['required_services_ready'])
        self.assertEqual(result['required_services'], {'web':True,'model':True,'desktop':True})
        self.assertEqual(result['pi_session_status'], 'absent')
        self.assertFalse(result['pi_session_ready'])
        self.assertEqual(endpoint.call_args_list, [
            mock.call('http://127.0.0.1:8089/health', expected_json_status='ok'),
            mock.call('http://127.0.0.1:8093/',
                      {'Tailscale-User-Login':'local-readiness-check'}),
        ])
        self.assertEqual(inspect.call_args.args[0], m.ARCH / m.TMUX_SOCKET.lstrip('/'))
        self.assertIs(inspect.call_args.kwargs['query'], m.tmux_panes)

    def test_status_fails_missing_or_unhealthy_required_services_independently(self):
        cases=(
            ('web', {'desktop':True,'model_process':True}, 123, [True,False]),
            ('model', {'desktop':True,'model_process':False}, 123, [True]),
            ('desktop', {'desktop':False,'model_process':True}, 123, [True,True]),
            ('web process', {'desktop':True,'model_process':True}, None, [True]),
        )
        for failed,processes,pid,probes in cases:
            with self.subTest(failed=failed), \
                 mock.patch.object(m, 'runtime_process_readiness', return_value=processes), \
                 mock.patch.object(m, 'running', return_value=pid), \
                 mock.patch.object(m, 'endpoint_ready', side_effect=probes), \
                 mock.patch.object(m, 'inspect_pi_session',
                                   return_value={'status':'absent','ready':False}):
                result=m.collect_readiness()
                self.assertFalse(result['required_services_ready'])
                self.assertEqual(result['pi_session_status'], 'absent')
                if failed in ('web','web process'):
                    self.assertFalse(result['required_services']['web'])
                elif failed == 'model':
                    self.assertFalse(result['required_services']['model'])
                else:
                    self.assertFalse(result['required_services']['desktop'])

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
