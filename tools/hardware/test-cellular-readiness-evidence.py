#!/usr/bin/env python3
"""Hardware-free tests for the passive cellular readiness inventory."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


TOOL = Path(__file__).with_name("cellular-readiness-evidence.py")
SPEC = importlib.util.spec_from_file_location("cellular_readiness_evidence", TOOL)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("cellular readiness collector could not be imported")
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class FakeSysfs:
    def __init__(self, root: Path):
        self.root = root
        self.sysfs = root / "sys"
        self.cpif = self.sysfs / "devices" / "platform" / "cpif"
        self.cpif_link = self.sysfs / "bus" / "platform" / "devices" / "cpif"
        self.net = self.sysfs / "class" / "net"
        self.misc = self.sysfs / "class" / "misc"
        self.modules = self.sysfs / "module"

    def cpif_device(self, state: str | None, driver: str = "cp_interface") -> None:
        self.cpif.mkdir(parents=True, exist_ok=True)
        if state is not None:
            (self.cpif / "modem_state").write_text(state, encoding="ascii")
        self.cpif_link.parent.mkdir(parents=True, exist_ok=True)
        self.cpif_link.unlink(missing_ok=True)
        self.cpif_link.symlink_to(self.cpif)
        target = self.sysfs / "bus" / "platform" / "drivers" / driver
        target.mkdir(parents=True, exist_ok=True)
        (self.cpif / "driver").symlink_to(target)

    def replace_driver_link(self, target: Path, *, symlink: bool = True) -> None:
        link = self.cpif / "driver"
        if link.is_symlink() or link.is_file():
            link.unlink()
        elif link.is_dir():
            link.rmdir()
        if symlink:
            link.symlink_to(target)
        else:
            link.mkdir()

    def interface(self, name: str, operstate: str) -> None:
        path = self.net / name
        path.mkdir(parents=True, exist_ok=True)
        (path / "operstate").write_text(operstate, encoding="ascii")

    def module(self, name: str) -> None:
        (self.modules / name).mkdir(parents=True, exist_ok=True)


class CellularReadinessEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="cellular-readiness-")
        self.fake = FakeSysfs(Path(self.temp.name))

    def tearDown(self) -> None:
        self.temp.cleanup()

    def collect(self) -> dict:
        return MODULE.collect(self.fake.sysfs)

    def assert_all_higher_layers_unknown(self, report: dict) -> None:
        readiness = report["readiness_observations"]
        self.assertEqual(readiness["sim_presence"], "unknown")
        self.assertEqual(readiness["sim_network_registration"], "unknown")
        self.assertEqual(readiness["cellular_data_session"], "unknown")
        self.assertEqual(readiness["cellular_route_forced"], "unknown")
        self.assertEqual(readiness["dns_over_cellular"], "unknown")
        self.assertEqual(readiness["https_over_cellular"], "unknown")
        self.assertEqual(readiness["ims_registration"], "unknown")
        self.assertEqual(readiness["outgoing_voice_call"], "unknown")
        self.assertEqual(readiness["incoming_voice_call"], "unknown")
        self.assertEqual(readiness["two_way_voice_audio"], "unknown")
        self.assertEqual(readiness["portable_assistant_cellular_mission"], "unknown")

    def test_inventory_and_even_cp_online_do_not_imply_portable_readiness(self) -> None:
        self.fake.cpif_device("ONLINE\n")
        self.fake.module("cpif")
        self.fake.interface("rmnet0", "up\n")
        (self.fake.misc / "umts_boot0").parent.mkdir(parents=True, exist_ok=True)
        (self.fake.misc / "umts_boot0").touch()

        report = self.collect()

        self.assertEqual(report["cpif"]["platform_device"], "present")
        self.assertEqual(report["cpif"]["driver_binding"], "bound_cp_interface")
        self.assertEqual(report["cpif"]["modem_state"]["value"], "ONLINE")
        self.assertEqual(report["cpif"]["module_inventory"]["listed"], ["cpif"])
        self.assertEqual(len(report["cpif"]["module_inventory"]["not_listed"]), 6)
        self.assertEqual(report["readiness_observations"]["cp_online_state"], "observed")
        self.assertEqual(report["rmnet_inventory"]["interfaces"], [
            {"name": "rmnet0", "operstate": "up", "read_status": "ok"},
        ])
        self.assert_all_higher_layers_unknown(report)

    def test_valid_other_cpif_driver_is_classified_separately(self) -> None:
        self.fake.cpif_device("INIT\n", driver="other_cp_driver")

        report = self.collect()

        self.assertEqual(report["cpif"]["driver_binding"], "bound_other")

    def test_dangling_cp_interface_driver_link_is_unknown(self) -> None:
        self.fake.cpif_device("INIT\n")
        expected = self.fake.sysfs / "bus" / "platform" / "drivers" / "cp_interface"
        expected.rmdir()

        report = self.collect()

        self.assertEqual(report["cpif"]["driver_binding"], "unknown")

    def test_out_of_namespace_cp_interface_basename_is_unknown(self) -> None:
        self.fake.cpif_device("INIT\n")
        outside = self.fake.sysfs / "devices" / "platform" / "unrelated" / "cp_interface"
        outside.mkdir(parents=True)
        self.fake.replace_driver_link(outside)

        report = self.collect()

        self.assertEqual(report["cpif"]["driver_binding"], "unknown")

    def test_non_symlink_driver_entry_is_unknown(self) -> None:
        self.fake.cpif_device("INIT\n")
        expected = self.fake.sysfs / "bus" / "platform" / "drivers" / "cp_interface"
        self.fake.replace_driver_link(expected, symlink=False)

        report = self.collect()

        self.assertEqual(report["cpif"]["driver_binding"], "unknown")

    def test_per_module_stat_error_is_explicit_and_marks_partial(self) -> None:
        self.fake.modules.mkdir(parents=True)
        failed_path = self.fake.modules / "mcu_ipc"
        real_lstat = Path.lstat

        def injected_lstat(path: Path):
            if path == failed_path:
                raise PermissionError("fixture permission failure")
            return real_lstat(path)

        with mock.patch.object(Path, "lstat", autospec=True, side_effect=injected_lstat):
            inventory = MODULE._module_inventory(self.fake.sysfs)

        self.assertEqual(inventory["status"], "partial")
        self.assertIn("mcu_ipc", inventory["unknown"])
        self.assertEqual(inventory["errors"]["mcu_ipc"], "permission_denied")
        self.assertEqual(inventory["per_module"]["mcu_ipc"], "unknown")
        self.assertIn("cpif", inventory["not_listed"])

    def test_generic_mif_names_and_wlan_up_are_not_cpif_evidence(self) -> None:
        self.fake.interface("wlan0", "up\n")
        (self.fake.sysfs / "bus" / "platform" / "devices").mkdir(parents=True)
        (self.fake.sysfs / "devices" / "platform" / "camera-mif").mkdir(parents=True)
        (self.fake.modules / "mif").mkdir(parents=True)

        report = self.collect()

        self.assertEqual(report["cpif"]["platform_device"], "absent")
        self.assertEqual(report["cpif"]["driver_binding"], "unknown")
        self.assertEqual(report["cpif"]["modem_state"]["classification"], "unknown")
        self.assertEqual(report["cpif"]["module_inventory"]["listed"], [])
        self.assertEqual(report["rmnet_inventory"]["interfaces"], [])
        self.assertEqual(report["readiness_observations"]["cp_online_state"], "unknown")
        self.assert_all_higher_layers_unknown(report)

    def test_coordinator_supplied_redacted_snapshot_keeps_sim_and_voice_unknown(self) -> None:
        # Mirrors the coordinator's 2026-10-02 11:32-11:36 UTC static sample:
        # cp_interface bound, modem_state INIT, rmnet0..7 down, wlan0 up.
        self.fake.cpif_device("INIT\n")
        for index in range(8):
            self.fake.interface(f"rmnet{index}", "down\n")
        self.fake.interface("wlan0", "up\n")

        report = self.collect()

        self.assertEqual(report["cpif"]["driver_binding"], "bound_cp_interface")
        self.assertEqual(report["cpif"]["modem_state"], {
            "value": "INIT", "status": "ok", "classification": "init",
        })
        self.assertEqual(report["readiness_observations"]["cp_online_state"], "not_observed")
        self.assertEqual(
            [item["operstate"] for item in report["rmnet_inventory"]["interfaces"]],
            ["down"] * 8,
        )
        self.assertNotIn("wlan0", [item["name"] for item in report["rmnet_inventory"]["interfaces"]])
        self.assert_all_higher_layers_unknown(report)

    def test_transitional_and_offline_states_never_mean_cp_online(self) -> None:
        for raw, classification in (
            ("BOOTING\n", "transitional"),
            ("SIM_ATTACH\n", "transitional"),
            ("SIM_DETACH\n", "transitional"),
            ("OFFLINE\n", "offline"),
            ("WDT_RESET\n", "crash"),
        ):
            with self.subTest(state=raw.strip()):
                self.fake = FakeSysfs(Path(self.temp.name) / raw.strip())
                self.fake.cpif_device(raw)
                report = self.collect()
                self.assertEqual(report["cpif"]["modem_state"]["classification"], classification)
                self.assertEqual(report["readiness_observations"]["cp_online_state"], "not_observed")
                self.assertEqual(report["readiness_observations"]["sim_network_registration"], "unknown")

    def test_malformed_missing_and_oversized_modem_state_stay_unknown(self) -> None:
        for raw in ("ONLINE\nINIT\n", "ONLINE \n", "online\n", "\x00\n", "X" * 129):
            with self.subTest(raw=raw[:16]):
                self.fake = FakeSysfs(Path(self.temp.name) / str(len(raw)) / str(ord(raw[0])))
                self.fake.cpif_device(raw)
                report = self.collect()
                self.assertIsNone(report["cpif"]["modem_state"]["value"])
                self.assertEqual(report["cpif"]["modem_state"]["classification"], "unknown")
                self.assertEqual(report["readiness_observations"]["cp_online_state"], "unknown")

        missing = FakeSysfs(Path(self.temp.name) / "missing")
        missing.cpif_device(None)
        report = MODULE.collect(missing.sysfs)
        self.assertIsNone(report["cpif"]["modem_state"]["value"])
        self.assertEqual(report["cpif"]["modem_state"]["status"], "missing")
        self.assertEqual(report["readiness_observations"]["cp_online_state"], "unknown")

    def test_cli_serializes_fake_inventory_without_touching_host_paths(self) -> None:
        self.fake.cpif_device("INIT\n")
        command = [sys.executable, str(TOOL), "--sysfs-root", str(self.fake.sysfs)]
        result = subprocess.run(command, check=True, capture_output=True, text=True)
        report = json.loads(result.stdout)
        self.assertEqual(report["schema"], "s22-cellular-readiness-evidence/v1")
        self.assertEqual(report["cpif"]["modem_state"]["value"], "INIT")

    def test_cli_is_stable_under_optimized_python_modes(self) -> None:
        self.fake.cpif_device("SIM_ATTACH\n")
        modes = (("normal", None, False), ("-O", None, True), ("PYTHONOPTIMIZE=1", "1", False))
        for label, optimize, use_flag in modes:
            with self.subTest(mode=label):
                environment = os.environ.copy()
                environment.pop("PYTHONOPTIMIZE", None)
                if optimize is not None:
                    environment["PYTHONOPTIMIZE"] = optimize
                invocation = [sys.executable]
                if use_flag:
                    invocation.append("-O")
                invocation.extend([str(TOOL), "--sysfs-root", str(self.fake.sysfs)])
                result = subprocess.run(invocation, check=True, capture_output=True,
                                        text=True, env=environment)
                report = json.loads(result.stdout)
                self.assertEqual(report["cpif"]["modem_state"]["classification"], "transitional")
                self.assertEqual(report["readiness_observations"]["sim_network_registration"], "unknown")


if __name__ == "__main__":
    unittest.main(verbosity=2)
