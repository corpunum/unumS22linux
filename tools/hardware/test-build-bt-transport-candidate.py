#!/usr/bin/env python3
"""Portable, hardware-free tests for the pinned BT candidate builder."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
BUILDER_PATH = ROOT / "tools/hardware/build-bt-transport-candidate-20261002.py"
SPEC = importlib.util.spec_from_file_location("bt_candidate_builder", BUILDER_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("candidate builder cannot be loaded")
BUILDER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = BUILDER
SPEC.loader.exec_module(BUILDER)


def isolated_cli(*args: str) -> list[str]:
    # -I ignores PYTHONOPTIMIZE, so propagate the tested mode explicitly.
    optimize = ["-O"] if sys.flags.optimize else []
    return [sys.executable, *optimize, "-I", "-S", "-B",
            str(BUILDER_PATH), *args]


def fixture_git(*args: str, cwd: Path) -> str:
    result = subprocess.run(
        ["/usr/bin/git", "--no-pager", "-C", str(cwd), *args],
        check=False,
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"},
        timeout=10,
    )
    if result.returncode:
        raise AssertionError(result.stderr or "fixture git command failed")
    return result.stdout.strip()


def init_source_fixture(root: Path) -> tuple[str, str, Path]:
    source = root / "tools/hardware/source.c"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"int source_value = 7;\n")
    fixture_git("init", "--quiet", cwd=root)
    fixture_git("config", "user.name", "BT fixture", cwd=root)
    fixture_git("config", "user.email", "bt-fixture@example.invalid", cwd=root)
    fixture_git("add", "--", "tools/hardware/source.c", cwd=root)
    fixture_git("commit", "--quiet", "-m", "source fixture", cwd=root)
    source_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    commit = fixture_git("rev-parse", "HEAD", cwd=root)
    return commit, source_sha, source


class CandidateBuilderPortableTests(unittest.TestCase):
    def test_runtime_reports_effective_python_optimization_mode(self):
        print(f"PYTHON_OPTIMIZE={sys.flags.optimize}")
        self.assertIn(sys.flags.optimize, (0, 1))

    def test_source_gate_accepts_exact_fixture_and_unrelated_new_files(self):
        with tempfile.TemporaryDirectory(prefix="bt-source-gate-") as temporary:
            root = Path(temporary)
            commit, digest, _source = init_source_fixture(root)
            (root / "tools/hardware/new-helper.py").write_text(
                "# unrelated fixture helper\n", encoding="ascii")
            (root / "docs").mkdir()
            (root / "docs/review-note.md").write_text(
                "unrelated fixture note\n", encoding="ascii")
            with patch.object(BUILDER, "ROOT", root), \
                    patch.object(BUILDER, "PINNED_SOURCE_COMMIT", commit), \
                    patch.object(BUILDER, "PUBLIC_INPUT_SHA256",
                                 {"tools/hardware/source.c": digest}):
                self.assertEqual(BUILDER.verify_git_source_tree(), commit)

    def test_source_gate_rejects_dirty_and_staged_pinned_inputs(self):
        with tempfile.TemporaryDirectory(prefix="bt-source-dirty-") as temporary:
            root = Path(temporary)
            commit, digest, source = init_source_fixture(root)
            with patch.object(BUILDER, "ROOT", root), \
                    patch.object(BUILDER, "PINNED_SOURCE_COMMIT", commit), \
                    patch.object(BUILDER, "PUBLIC_INPUT_SHA256",
                                 {"tools/hardware/source.c": digest}):
                source.write_bytes(b"int source_value = 8;\n")
                with self.assertRaises(BUILDER.GateError):
                    BUILDER.verify_git_source_tree()
                fixture_git("add", "--", "tools/hardware/source.c", cwd=root)
                source.write_bytes(b"int source_value = 7;\n")
                with self.assertRaises(BUILDER.GateError):
                    BUILDER.verify_git_source_tree()

    def test_source_gate_rejects_nonancestor_pin(self):
        with tempfile.TemporaryDirectory(prefix="bt-source-ancestry-") as temporary:
            root = Path(temporary)
            _commit, digest, _source = init_source_fixture(root)
            with patch.object(BUILDER, "ROOT", root), \
                    patch.object(BUILDER, "PINNED_SOURCE_COMMIT", "f" * 40), \
                    patch.object(BUILDER, "PUBLIC_INPUT_SHA256",
                                 {"tools/hardware/source.c": digest}):
                with self.assertRaises(BUILDER.GateError):
                    BUILDER.verify_git_source_tree()

    def test_environment_redirection_inputs_are_refused(self):
        blocked = (
            "CC", "CFLAGS", "CPATH", "C_INCLUDE_PATH", "COMPILER_PATH",
            "GCC_EXEC_PREFIX", "LIBRARY_PATH", "DEPENDENCIES_OUTPUT",
            "GIT_DIR", "KCONFIG_CONFIG", "KBUILD_OUTPUT", "TMPDIR",
        )
        for name in blocked:
            with self.subTest(name=name):
                with self.assertRaises(BUILDER.GateError):
                    BUILDER.check_environment({name: "/tmp/redirect"})
        BUILDER.check_environment({"GIT_PAGER": "cat"})

    def test_real_cli_subprocess_refuses_inherited_compiler_redirection(self):
        environment = BUILDER.clean_git_env()
        environment["CPATH"] = "/tmp/bt-include-redirect"
        completed = subprocess.run(
            isolated_cli("--check-only"),
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(completed.returncode, 2)
        self.assertIn("unsafe environment variable set: CPATH", completed.stderr)

    def test_real_cli_refuses_redirected_tmpdir_without_relaxing_guard(self):
        environment = BUILDER.clean_git_env()
        environment["TMPDIR"] = "/tmp/bt-temp-redirect"
        completed = subprocess.run(
            isolated_cli("--check-only"), cwd=ROOT, env=environment,
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(completed.returncode, 2)
        self.assertIn("unsafe environment variable set: TMPDIR", completed.stderr)

    def test_nonisolated_cli_is_refused_before_preflight(self):
        completed = subprocess.run(
            [sys.executable, "-B", str(BUILDER_PATH), "--check-only"],
            cwd=ROOT, env=BUILDER.clean_git_env(), capture_output=True,
            text=True, timeout=10,
        )
        self.assertEqual(completed.returncode, 2)
        self.assertIn("requires Python -I -S", completed.stderr)

    def test_isolated_cli_does_not_load_injected_sitecustomize(self):
        with tempfile.TemporaryDirectory(prefix="bt-python-startup-") as temporary:
            root = Path(temporary)
            marker = root / "startup-executed"
            (root / "sitecustomize.py").write_text(
                "from pathlib import Path\n"
                f"Path({str(marker)!r}).touch()\n", encoding="ascii")
            environment = BUILDER.clean_git_env()
            environment["PYTHONPATH"] = str(root)
            completed = subprocess.run(
                isolated_cli("--help"), cwd=ROOT, env=environment,
                capture_output=True, text=True, timeout=10,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertFalse(marker.exists())

    def test_preflight_child_environment_is_allowlisted(self):
        self.assertEqual(BUILDER.clean_git_env(), {
            "PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C",
        })

    def test_existing_candidate_path_is_not_reusable(self):
        with tempfile.TemporaryDirectory(prefix="bt-output-guard-") as temporary:
            existing = Path(temporary) / "candidate"
            existing.mkdir()
            with self.assertRaises(BUILDER.GateError):
                BUILDER.verify_output_absent(existing)

    def test_wrong_architecture_and_wrong_expected_artifact_hash_are_rejected(self):
        wrong_arch = bytearray(64)
        wrong_arch[:4] = b"\x7fELF"
        wrong_arch[4] = 2
        wrong_arch[5] = 1
        wrong_arch[6] = 1
        wrong_arch[16:20] = b"\x02\x00\x3e\x00"
        with self.assertRaises(BUILDER.GateError):
            BUILDER.elf_identity(bytes(wrong_arch))
        with self.assertRaises(BUILDER.GateError):
            BUILDER.verify_artifact(bytes(wrong_arch), expected_sha256="f" * 64)

    def test_compile_command_is_single_target_static_and_fixed(self):
        command = BUILDER.build_command(Path("/tmp/bt-safe-check"))
        self.assertEqual(command[0], str(BUILDER.COMPILER))
        self.assertTrue({"-static", "-std=c11", "-Wall", "-Wextra", "-Werror", "-O2"}
                        .issubset(set(command)))
        self.assertEqual(command.count(str(BUILDER.ROOT / BUILDER.MAIN_SOURCE)), 1)
        self.assertEqual(command[-1], str(Path("/tmp/bt-safe-check") / BUILDER.ARTIFACT_NAME))
        self.assertNotIn("--execute", command)

    def test_current_legacy_runner_and_consumed_receipt_pins_are_exact(self):
        receipt = BUILDER.verify_legacy_trial_guards()
        self.assertEqual(receipt["legacy_runner_sha256"],
                         "7696d15fcdd7af33d130cb4cdfa78c823553d84a3eb2d830142c5bbd0a09198a")
        self.assertEqual(receipt["legacy_source_pin_sha256"],
                         "476f148246dfac8330f7ade2790627b64a38ee7b1cb4f713934b6d438864a7a4")
        self.assertEqual(receipt["legacy_artifact_sha256"],
                         "f4ba76613e1339314898ebbf067338d846ed9ee231907a44306b82f54f2f1684")
        self.assertEqual(receipt["legacy_artifact_build_id"],
                         "a5be9451d95335ae2a5d292d721a766208f55a87")
        self.assertEqual(receipt["trial_identity"], "bt-hci-plain-h4-20260927")
        self.assertEqual(receipt["trial_attempts_preserved"], 1)
        self.assertEqual(receipt["trial_receipt_sha256"],
                         "eb8f1963049cb4cdbca7236169bbdc91f2c929227ea995bdbb310972399518dc")
        self.assertTrue(receipt["trial_receipt_unchanged"])
        self.assertTrue(receipt["candidate_artifact_is_distinct"])

    def test_previous_legacy_runner_pin_does_not_accept_current_runner(self):
        with patch.object(
                BUILDER, "LEGACY_RUNNER_SHA256",
                "26bbe8bdb5ff687de29d5ce67f17d58cfa460a48fe9fc5be9043dccd54cf9253"):
            with self.assertRaisesRegex(BUILDER.GateError,
                                        "legacy runner fingerprint mismatch"):
                BUILDER.verify_legacy_trial_guards()

    def _verify_legacy_fixture(self, evidence_bytes: bytes) -> dict:
        with tempfile.TemporaryDirectory(prefix="bt-legacy-receipt-") as temporary:
            root = Path(temporary)
            runner_path = root / BUILDER.LEGACY_RUNNER
            runner_path.parent.mkdir(parents=True)
            runner_path.write_text(
                "\n".join((
                    BUILDER.LEGACY_BRIDGE_SHA256,
                    BUILDER.LEGACY_ARTIFACT_SHA256,
                    BUILDER.LEGACY_ARTIFACT_BUILD_ID,
                )) + "\n",
                encoding="ascii",
            )
            evidence_path = root / BUILDER.TRIAL_EVIDENCE
            evidence_path.parent.mkdir(parents=True)
            evidence_path.write_bytes(evidence_bytes)
            candidate = root / "new-output" / "candidate"
            old_artifact = root / "old-output" / "bridge"
            with patch.multiple(
                    BUILDER,
                    ROOT=root,
                    LEGACY_RUNNER_SHA256=hashlib.sha256(
                        runner_path.read_bytes()).hexdigest(),
                    TRIAL_EVIDENCE_SHA256=hashlib.sha256(evidence_bytes).hexdigest(),
                    ARTIFACT_PATH=candidate,
                    OUTPUT_DIR=candidate.parent,
                    LEGACY_ARTIFACT_PATH=old_artifact):
                return BUILDER.verify_legacy_trial_guards()

    def test_malformed_consumed_receipt_is_rejected(self):
        with self.assertRaisesRegex(BUILDER.GateError,
                                    "consumed trial evidence cannot be validated"):
            self._verify_legacy_fixture(b"{not-json\n")

    def test_wrong_consumed_receipt_cannot_change_identity_or_attempt_count(self):
        evidence = {
            "bluetooth_after_transport_fix": {
                "trial_id": BUILDER.TRIAL_ID,
                "attempts_for_this_identity": 2,
                "durable_guard_outcome": "success",
                "artifact_sha256": BUILDER.LEGACY_ARTIFACT_SHA256,
                "artifact_gnu_build_id": BUILDER.LEGACY_ARTIFACT_BUILD_ID,
            },
        }
        with self.assertRaisesRegex(BUILDER.GateError,
                                    "consumed trial identity/receipt differs"):
            self._verify_legacy_fixture(json.dumps(evidence).encode("utf-8"))


class CandidateBuilderLocalFixtureTests(unittest.TestCase):
    """Exact private/profile/compiler checks are optional outside the host fixture."""

    @classmethod
    def setUpClass(cls):
        cls.fixture_present = (
            BUILDER.COMPILER.is_file()
            and all(path.is_file() for path, _digest in BUILDER.PRIVATE_INPUTS.values())
        )

    def require_local_fixture(self):
        if not self.fixture_present:
            self.skipTest("exact local private headers and pinned cross-compiler are unavailable")

    def test_exact_local_private_inputs_pass_when_present(self):
        self.require_local_fixture()
        inputs = BUILDER.verify_private_inputs()
        self.assertEqual([item["id"] for item in inputs],
                         ["qca_patch_profile", "runtime_nvm_profile"])
        self.assertTrue(all(set(item) == {"id", "sha256"} for item in inputs))

    def test_exact_local_compiler_and_read_headers_pass_when_present(self):
        self.require_local_fixture()
        compiler = BUILDER.compiler_identity()
        dependencies = BUILDER.discover_dependencies()
        self.assertEqual(compiler["target"], "aarch64-linux-gnu")
        self.assertEqual(compiler["major"], "13")
        self.assertEqual(len(compiler["static_runtime_inputs"]), 8)
        self.assertEqual(len(compiler["compiler_components"]), 4)
        self.assertGreater(dependencies["system_header_count"], 0)
        self.assertEqual(dependencies["private_header_count"], 2)
        self.assertEqual(dependencies["public_source_count"], 8)

    def test_exact_local_preflight_passes_without_creating_output_when_present(self):
        self.require_local_fixture()
        output_before = BUILDER.OUTPUT_DIR.exists()
        completed = subprocess.run(
            isolated_cli("--check-only"),
            cwd=ROOT,
            env=BUILDER.clean_git_env(),
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        receipt = json.loads(completed.stdout)
        self.assertEqual(receipt["status"], "preflight_only_no_compile")
        self.assertEqual(receipt["compiler"]["target"], "aarch64-linux-gnu")
        self.assertEqual(len(receipt["source_inputs"]), 8)
        self.assertEqual(len(receipt["private_inputs"]), 2)
        self.assertEqual(BUILDER.OUTPUT_DIR.exists(), output_before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
