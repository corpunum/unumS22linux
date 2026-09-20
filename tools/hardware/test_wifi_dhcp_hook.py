#!/usr/bin/env python3
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).parent
spec = importlib.util.spec_from_file_location("wifi_dhcp_hook", ROOT / "wifi-dhcp-hook.py")
hook = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hook)


class HookTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        hook.STATE = Path(self.tmp.name) / "private" / "lease.json"
        hook.RESOLV = Path(self.tmp.name) / "etc" / "resolv.conf"
        hook.RESOLV.parent.mkdir()
        hook.RESOLV.write_text("# preserved\nnameserver 9.9.9.9\noptions ndots:5\n")
        self.env = {"interface": "wlan0", "ip": "192.0.2.10",
                    "subnet": "255.255.255.0", "router": "192.0.2.1"}

    def tearDown(self):
        self.tmp.cleanup()

    def test_bound_only_changes_wlan_and_saves_private_state(self):
        calls = []
        with patch.dict(os.environ, self.env, clear=False), patch.object(
                hook, "run_ip", side_effect=lambda *args, **kwargs: calls.append((args, kwargs))):
            self.assertEqual(hook.bound(), 0)
        self.assertEqual(calls, [
            (("route", "show", "default", "metric", "600"), {}),
            (("addr", "replace", "192.0.2.10/24", "dev", "wlan0"), {}),
            (("route", "replace", "default", "via", "192.0.2.1", "dev", "wlan0", "metric", "600"), {})])
        self.assertEqual(json.loads(hook.STATE.read_text())["gateway"], "192.0.2.1")
        self.assertEqual(hook.STATE.stat().st_mode & 0o777, 0o600)

    def test_bad_interface_and_rescue_overlap_write_nothing(self):
        for changes in ({"interface": "ecm0"}, {"ip": "10.55.0.20", "router": "10.55.0.1"}):
            with self.subTest(changes=changes):
                env = dict(self.env, **changes)
                with patch.dict(os.environ, env, clear=False), patch.object(hook, "run_ip") as command:
                    self.assertNotEqual(hook.bound(), 0)
                    command.assert_not_called()
                self.assertFalse(hook.STATE.exists())

    def test_multiple_router_and_dns_values_are_recorded_and_applied(self):
        env = dict(self.env, router="192.0.2.1 192.0.2.2",
                   domain_name_servers="192.0.2.53 192.0.2.54")
        calls = []
        with patch.dict(os.environ, env, clear=False), patch.object(
                hook, "run_ip", side_effect=lambda *args, **kwargs: calls.append((args, kwargs))):
            self.assertEqual(hook.bound(), 0)
        self.assertEqual(json.loads(hook.STATE.read_text())["dns"], ["192.0.2.53", "192.0.2.54"])
        self.assertEqual(hook.RESOLV.read_text().splitlines()[:2],
                         ['nameserver 192.0.2.53', 'nameserver 192.0.2.54'])

    def test_fresh_dnsless_lease_does_not_write_resolver(self):
        before = hook.RESOLV.read_text()
        inode = hook.RESOLV.stat().st_ino
        with patch.dict(os.environ, self.env, clear=False), patch.object(
                hook, "run_ip", side_effect=lambda *args, **kwargs: None):
            self.assertEqual(hook.bound(), 0)
        self.assertEqual(hook.RESOLV.read_text(), before)
        self.assertEqual(hook.RESOLV.stat().st_ino, inode)
        self.assertFalse((hook.STATE.parent / "resolv.conf.before-wifi").exists())
        self.assertNotIn("resolver_content", json.loads(hook.STATE.read_text()))

    def test_other_interface_metric_conflict_is_rejected_before_writes(self):
        conflict = subprocess.CompletedProcess([], 0, "default via 198.51.100.1 dev ecm0 metric 600\n", "")
        with patch.dict(os.environ, self.env, clear=False), patch.object(
                hook, "run_ip", return_value=conflict) as command:
            self.assertNotEqual(hook.bound(), 0)
            self.assertEqual(command.call_count, 1)

    def test_deconfig_removes_only_owned_wlan_state(self):
        hook.STATE.parent.mkdir(mode=0o700)
        hook.STATE.write_text(json.dumps({"address": "192.0.2.10", "prefix": 24,
                                          "gateway": "192.0.2.1", "dns": []}))
        calls = []
        with patch.dict(os.environ, {"interface": "wlan0"}, clear=False), patch.object(
                hook, "run_ip", side_effect=lambda *args, **kwargs: calls.append((args, kwargs))):
            self.assertEqual(hook.deconfig(), 0)
        self.assertEqual(calls, [
            (("route", "del", "default", "via", "192.0.2.1", "dev", "wlan0", "metric", "600"), {"check": False}),
            (("addr", "del", "192.0.2.10/24", "dev", "wlan0"), {"check": False})])
        self.assertFalse(hook.STATE.exists())

    def test_dns_update_preserves_inode_and_fallback_then_restores(self):
        inode = hook.RESOLV.stat().st_ino
        env = dict(self.env, domain_name_servers="192.0.2.53")
        with patch.dict(os.environ, env, clear=False), patch.object(
                hook, "run_ip", side_effect=lambda *args, **kwargs: None):
            self.assertEqual(hook.bound(), 0)
        text = hook.RESOLV.read_text()
        self.assertEqual(hook.RESOLV.stat().st_ino, inode)
        self.assertIn("nameserver 192.0.2.53", text)
        self.assertIn("nameserver 9.9.9.9", text)
        self.assertIn("options ndots:5 timeout:2 attempts:2", text)
        with patch.dict(os.environ, {"interface": "wlan0"}, clear=False), patch.object(
                hook, "run_ip", side_effect=lambda *args, **kwargs: None):
            self.assertEqual(hook.deconfig(), 0)
        self.assertEqual(hook.RESOLV.read_text(), "# preserved\nnameserver 9.9.9.9\noptions ndots:5\n")

    def test_ipv6_fallback_and_inline_comment_are_preserved(self):
        hook.RESOLV.write_text("nameserver 2001:db8::53 # local v6\noptions ndots:5 ndots:5\n")
        env = dict(self.env, domain_name_servers="192.0.2.53")
        with patch.dict(os.environ, env, clear=False), patch.object(
                hook, "run_ip", side_effect=lambda *args, **kwargs: None):
            self.assertEqual(hook.bound(), 0)
        text = hook.RESOLV.read_text()
        self.assertIn("nameserver 2001:db8::53", text)
        self.assertIn("# local v6", text)
        self.assertEqual(text.count("ndots:5"), 1)

    def test_dnsless_renew_keeps_owned_resolver_and_deconfig_restores(self):
        original = hook.RESOLV.read_text()
        with patch.dict(os.environ, dict(self.env, domain_name_servers="192.0.2.53"), clear=False), patch.object(
                hook, "run_ip", side_effect=lambda *args, **kwargs: None):
            self.assertEqual(hook.bound(), 0)
        generated = hook.RESOLV.read_text()
        state = json.loads(hook.STATE.read_text())
        self.assertEqual(state["resolver_content"], generated)
        with patch.dict(os.environ, self.env, clear=False), patch.object(
                hook, "run_ip", side_effect=lambda *args, **kwargs: None):
            self.assertEqual(hook.bound(), 0)
        self.assertEqual(hook.RESOLV.read_text(), generated)
        self.assertEqual(json.loads(hook.STATE.read_text())["resolver_content"], generated)
        with patch.dict(os.environ, {"interface": "wlan0"}, clear=False), patch.object(
                hook, "run_ip", side_effect=lambda *args, **kwargs: None):
            self.assertEqual(hook.deconfig(), 0)
        self.assertEqual(hook.RESOLV.read_text(), original)

    def test_user_modified_resolver_is_not_overwritten_or_restored(self):
        env = dict(self.env, domain_name_servers="192.0.2.53")
        with patch.dict(os.environ, env, clear=False), patch.object(
                hook, "run_ip", side_effect=lambda *args, **kwargs: None):
            self.assertEqual(hook.bound(), 0)
        user_text = "# user changed\nnameserver 1.1.1.1\n"
        hook.RESOLV.write_text(user_text)
        with patch.dict(os.environ, {"interface": "wlan0"}, clear=False), patch.object(
                hook, "run_ip", side_effect=lambda *args, **kwargs: None):
            self.assertEqual(hook.deconfig(), 0)
        self.assertEqual(hook.RESOLV.read_text(), user_text)
        self.assertFalse((hook.STATE.parent / "resolv.conf.before-wifi").exists())

    def test_dns_only_renew_does_not_delete_current_address_or_route(self):
        old = {"address": "192.0.2.10", "prefix": 24, "gateway": "192.0.2.1",
               "dns": ["192.0.2.53"]}
        hook.STATE.parent.mkdir(mode=0o700)
        hook.STATE.write_text(json.dumps(old))
        env = dict(self.env, domain_name_servers="192.0.2.54")
        calls = []
        with patch.dict(os.environ, env, clear=False), patch.object(
                hook, "run_ip", side_effect=lambda *args, **kwargs: calls.append(args)):
            self.assertEqual(hook.bound(), 0)
        self.assertFalse(any(args[0:2] == ("addr", "del") or args[0:2] == ("route", "del") for args in calls))

    def test_repeated_renew_does_not_reclaim_user_modified_resolver(self):
        env = dict(self.env, domain_name_servers="192.0.2.53")
        with patch.dict(os.environ, env, clear=True), patch.object(hook, 'run_ip'):
            self.assertEqual(hook.bound(), 0)
            hook.RESOLV.write_text('# user override\nnameserver 1.1.1.1\n')
            for _ in range(2):
                self.assertEqual(hook.bound(), 0)
                self.assertEqual(hook.RESOLV.read_text(), '# user override\nnameserver 1.1.1.1\n')
                self.assertTrue(json.loads(hook.STATE.read_text())['resolver_user_override'])
            self.assertEqual(hook.deconfig(), 0)
            self.assertEqual(hook.RESOLV.read_text(), '# user override\nnameserver 1.1.1.1\n')


if __name__ == "__main__":
    unittest.main()
