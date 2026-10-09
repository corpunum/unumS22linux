#!/usr/bin/env python3
"""Test safe pre-authorization waiter unlinking in exact composed target C.

The fixture is pinned/capped and composes the reviewed first nine patches.
Only patch 10 changes npu-session.c. Linux locks, completion, queue, close-lock,
and mailbox behavior are bounded host shims; this is not a kernel/device test.
"""
from __future__ import annotations

import hashlib
import importlib.util
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
OWNERSHIP_TEST = ROOT / "tools/hardware/test-npu-publication-ownership.py"
HARNESS = ROOT / "tools/hardware/npu-publication-drain-ownership-harness.c"
PATCH = ROOT / "tools/hardware/npu-publication-drain-ownership.patch"
PATCH_SHA256 = "7137b797c9b4658e1ec7e7829054b11ed684e51552933203d5af45b7a9743709"
SESSION_C = "drivers/vision/npu/core/npu-session.c"
PROTO_C = "drivers/vision/npu/core/npu-protodrv.c"
MAILBOX_C = "drivers/vision/npu/core/npu-if-protodrv-mbox2.c"
MSGID_C = "drivers/vision/npu/core/npu-util-msgidgen.c"
MSGID_H = "drivers/vision/npu/core/npu-util-msgidgen.h"


def check(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    check(spec is not None and spec.loader is not None,
          f"cannot load pinned source helper {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


OWN = load_module(OWNERSHIP_TEST, "s22_npu_drain_ownership_composition")
LIVE = OWN.LIVE
FULL = OWN.FULL
WALK = OWN.WALK
RECON = OWN.RECON


def replace_function(unit: str, marker: str, replacement: str) -> str:
    old = OWN.braced_declaration(unit, marker)
    check(unit.count(old) == 1,
          f"generated host unit has non-unique function {marker}")
    return unit.replace(old, replacement.rstrip(), 1)


def customize_unit(unit: str, *, patched: bool, harness: str) -> str:
    marker = "struct npu_nw {"
    check(unit.count(marker) == 1, "generated C npu_nw declaration anchor changed")
    unit = unit.replace(marker,
                        "int nw_req_manager(int msgid, struct npu_nw *nw);\n" + marker,
                        1)
    marker = "static void *publisher_main(void *unused)"
    check(unit.count(marker) == 1, "generated publisher function anchor changed")
    unit = unit.replace(marker,
        "int nw_req_manager(int msgid, struct npu_nw *nw)\n"
        "{ (void)msgid; (void)nw; return -EWOULDBLOCK; }\n\n" + marker,
        1)
    session_match = re.search(r"struct npu_session \{.*?\n\};", unit, re.S)
    check(session_match is not None,
          "generated exact-C unit lacks its host npu_session declaration")
    session_decl = session_match.group(0)
    check("pthread_mutex_t *global_lock" not in session_decl,
          "generated session lock shim was unexpectedly preinstalled")
    unit = unit[:session_match.start()] + session_decl.replace(
        "\n};", "\n    pthread_mutex_t *global_lock;\n};", 1) + unit[session_match.end():]

    marker = "static atomic_int caller_returned;"
    check(unit.count(marker) == 1, "host caller-returned anchor changed")
    unit = unit.replace(marker, marker + "\n" +
        "static atomic_int drain_cancel_observed;\n"
        "static atomic_int drain_close_started;\n"
        "static atomic_int drain_close_returned;\n"
        "static pthread_mutex_t drain_session_lock = PTHREAD_MUTEX_INITIALIZER;", 1)

    unit = replace_function(unit, "static void *caller_main(void *opaque)", r"""
static void *caller_main(void *opaque)
{
    struct caller_state *state = opaque;
    pthread_mutex_lock(state->session.global_lock);
    state->result = npu_session_wait_power_request(&state->session,
                                                    NPU_NW_CMD_POWER_CTL);
    pthread_mutex_unlock(state->session.global_lock);
    gate_signal(&caller_returned);
    return NULL;
}
""")

    unit = replace_function(unit, "static void start_caller(", r"""
static void start_caller(struct caller_state *state, pthread_t *thread)
{
    memset(state, 0, sizeof(*state));
    state->session.global_lock = &drain_session_lock;
    state->session.uid = 19;
    state->session.sched_param.bound_id = 7;
    state->session.ncp_info.ncp_addr = 99;
    expect(pthread_create(thread, NULL, caller_main, state) == 0,
           "failed to start actual session waiter");
}
""")

    unit = replace_function(unit, "static void reset_runtime(", r"""
static void reset_runtime(enum harness_mode mode)
{
    expect(list_empty(&npu_power_waiters), "previous case left a registered waiter");
    harness_mode = mode;
    atomic_store(&active_cookie, 0);
    atomic_store(&active_req_id, 731);
    atomic_store(&request_queued, 0);
    atomic_store(&request_assigned, 0);
    atomic_store(&begin_paused, 0);
    atomic_store(&release_begin, 0);
    atomic_store(&release_before_assign, 0);
    atomic_store(&release_before_case, 0);
    atomic_store(&mailbox_entered, 0);
    atomic_store(&release_mailbox, 0);
    atomic_store(&worker_done, 0);
    atomic_store(&caller_returned, 0);
    atomic_store(&drain_cancel_observed, 0);
    atomic_store(&drain_close_started, 0);
    atomic_store(&drain_close_returned, 0);
    atomic_store(&worker_result, 0);
    atomic_store(&lsm_move_count, 0);
    atomic_store(&lsm_last_state, -1);
    atomic_store(&mailbox_callback_count, 0);
    atomic_store(&mailbox_lock_was_free, 0);
    atomic_store(&host_atomic_xchg_calls, 0);
    memset(&queued_request, 0, sizeof(queued_request));
    memset(&queue_entry, 0, sizeof(queue_entry));
    memset(&npu_proto_drv.msgid_pool, 0, sizeof(npu_proto_drv.msgid_pool));
    npu_proto_drv.msgid_pool.magic = MSGID_POOL_MAGIC;
    npu_proto_drv.npu_device = NULL;
    mailbox_result = 1;
    static struct npu_if_protodrv_mbox_ops ops;
    ops.nw_post_request = fake_nw_post_request;
    protodrv_mbox.npu_if_protodrv_mbox_ops = &ops;
    proto_nw_lsm.lsm_move_entry = record_lsm_move;
}
""")

    unit = replace_function(unit, "static void host_spin_unlock_irqrestore(", r"""
static void host_spin_unlock_irqrestore(pthread_mutex_t *lock)
{
    struct npu_power_waiter *waiter;
    bool pause, cancelled;

    pthread_mutex_unlock(lock);
    if (lock != &npu_power_waiters_lock)
        return;
    pthread_mutex_lock(lock);
    waiter = npu_power_waiter_find(atomic_load(&active_cookie));
    pause = waiter && waiter->publishing && !waiter->publish_committed &&
            !waiter->cancelled;
    cancelled = waiter && waiter->cancelled;
    pthread_mutex_unlock(lock);
    if (cancelled && harness_mode == MODE_BEFORE_AUTHORIZE)
        gate_signal(&drain_cancel_observed);
    if (harness_mode != MODE_BEFORE_AUTHORIZE)
        return;
    if (pause && atomic_exchange(&begin_paused, 1) == 0) {
        pthread_mutex_lock(&host_gate_lock);
        pthread_cond_broadcast(&host_gate_changed);
        while (!atomic_load(&release_begin))
            pthread_cond_wait(&host_gate_changed, &host_gate_lock);
        pthread_mutex_unlock(&host_gate_lock);
    }
}
""")

    harness = harness.replace("EXPECT_DRAIN_OWNERSHIP_PATCH_VALUE",
                              "1" if patched else "0")
    old_test = LIVE.braced_declaration(
        unit, "static void test_cancel_between_begin_and_authorize(void)")
    check(unit.count(old_test) == 1,
          "generated C lost unique pre-authorization test insertion point")
    unit = unit.replace(old_test, harness.rstrip(), 1)
    check("EXPECT_DRAIN_OWNERSHIP_PATCH_VALUE" not in unit,
          "drain ownership test retained unresolved patch selector")
    check("BASELINE REPRO actual C: canceled pre-authorization waiter remains linked" in unit and
          "PASS patch10 actual C: pre-auth cancel unlinks" in unit,
          "supplemental C harness did not provide before/after assertions")
    return unit


def compile_and_run(compiler: str, directory: Path, name: str,
                    unit: str, optimization: str) -> str:
    source = directory / f"{name}.c"
    binary = directory / name
    source.write_text(unit, encoding="utf-8")
    command = [compiler, "-std=gnu11", "-Wall", "-Wextra", "-Werror",
               "-Wno-unused-parameter", optimization, "-pthread",
               str(source), "-o", str(binary)]
    compiled = subprocess.run(command, capture_output=True, text=True,
                              check=False, timeout=30)
    check(compiled.returncode == 0,
          f"{name} compile failed at {optimization}:\n{compiled.stderr}")
    try:
        executed = subprocess.run([str(binary)], capture_output=True, text=True,
                                  check=False, timeout=20)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"{name} hung beyond host bound at {optimization}")
    check(executed.returncode == 0,
          f"{name} failed at {optimization}:\n{executed.stdout}{executed.stderr}")
    required = (
        "BASELINE REPRO actual C: canceled pre-authorization waiter remains linked"
        if "baseline" in name else
        "PASS patch10 actual C: pre-auth cancel unlinks"
    )
    check(required in executed.stdout,
          f"{name} omitted its baseline/pass marker at {optimization}")
    check("UNRESOLVED LIVENESS actual C reproduction: caller stays blocked while synchronous post is held; production drain has no timeout"
          in executed.stdout,
          f"{name} no longer records the committed-post unbounded limit")
    check("LIMIT pthread/list/completion/queue/mailbox shims are not Linux locking, kernel build, or hardware evidence"
          in executed.stdout,
          f"{name} lost the host-only evidence limit")
    return executed.stdout


def main() -> int:
    check(HARNESS.is_file(), "supplemental C harness is missing")
    data = PATCH.read_bytes()
    check(len(data) <= OWN.MAX_FILE_BYTES,
          "patch10 exceeds the existing bounded patch input limit")
    check(hashlib.sha256(data).hexdigest() == PATCH_SHA256,
          "patch10 SHA-256 does not match frozen value")

    sources, source_identity, source_bytes = OWN.load_sources()
    compiler = shutil.which("cc") or shutil.which("gcc")
    check(compiler is not None, "host C compiler cc/gcc is required")

    patch_paths = RECON.patch_paths()
    FULL.patch_bytes(FULL.PROFILE_PATCH, FULL.PROFILE_PATCH.name,
                     FULL.PROFILE_SHA256)
    FULL.patch_bytes(LIVE.ERROR.ERROR_PATCH, LIVE.ERROR.ERROR_PATCH.name,
                     LIVE.ERROR.ERROR_PATCH_SHA256)
    FULL.patch_bytes(LIVE.MAILBOX_PATCH, LIVE.MAILBOX_PATCH.name,
                     LIVE.MAILBOX_PATCH_SHA256)
    WALK.source_sha256(WALK.PATCH_PATH.name, WALK.PATCH_PATH.read_bytes(),
                       WALK.PATCH_SHA256)
    FULL.patch_bytes(OWN.PATCH, OWN.PATCH.name, OWN.PATCH_SHA256)
    print(f"SOURCE_FIXTURE {source_identity}")
    print(f"SOURCE_UNION_BYTES {source_bytes}")
    print(f"PYTHON_OPTIMIZE {sys.flags.optimize}")
    print("PATCH_ORDER " + " -> ".join([
        *(path.name for path in patch_paths), FULL.PROFILE_PATCH.name,
        LIVE.ERROR.ERROR_PATCH.name, LIVE.MAILBOX_PATCH.name,
        WALK.PATCH_PATH.name, OWN.PATCH.name, PATCH.name]))

    with tempfile.TemporaryDirectory(prefix="s22-npu-publication-drain-") as temp_name:
        temp = Path(temp_name)
        source_root = temp / "composed"
        source_root.mkdir()
        OWN.write_fixture(source_root, sources)
        RECON.apply_patch_series(source_root, patch_paths)
        FULL.apply_plain_patch(source_root, FULL.PROFILE_PATCH,
                               "fifth shutdown lifecycle profile")
        FULL.apply_plain_patch(source_root, LIVE.ERROR.ERROR_PATCH,
                               "sixth shutdown error propagation")
        FULL.apply_plain_patch(source_root, LIVE.MAILBOX_PATCH,
                               "seventh missing callback message-ID reclaim")
        FULL.apply_plain_patch(source_root, WALK.PATCH_PATH,
                               "eighth mailbox debug walk bound")
        eighth_sources = OWN.snapshot(source_root, sources)
        FULL.apply_plain_patch(source_root, OWN.PATCH,
                               "ninth publication ownership fix")
        ninth_sources = OWN.snapshot(source_root, sources)

        FULL.apply_plain_patch(source_root, PATCH,
                               "tenth pre-authorization waiter unlink")
        tenth_sources = OWN.snapshot(source_root, sources)
        changed = {relative for relative in sources
                   if ninth_sources[relative] != tenth_sources[relative]}
        check(changed == {SESSION_C},
              f"patch10 changed unexpected paths: {sorted(changed)!r}")

        before_session = ninth_sources[SESSION_C].decode("utf-8")
        after_session = tenth_sources[SESSION_C].decode("utf-8")
        before_drain = LIVE.braced_declaration(
            before_session, "static void npu_power_wait_cancel_and_drain(")
        after_drain = LIVE.braced_declaration(
            after_session, "static void npu_power_wait_cancel_and_drain(")
        expected_drain = before_drain.replace(
            "if (!waiter->publishing) {",
            "/* Before authorization, the publisher has only a cookie and must\n"
            "\t\t * look the waiter up again under this lock before it can post.  Once\n"
            "\t\t * authorized, retain the existing drain: the synchronous publisher\n"
            "\t\t * may still be using the mailbox request and its completion path.\n"
            "\t\t */\n"
            "\t\tif (!waiter->publishing || !waiter->publish_committed) {", 1)
        check("if (!waiter->publishing) {" in before_drain and
              expected_drain == after_drain,
              "patch10 is not exactly the pre-authorization cancel branch change")
        check("wait_for_completion(&waiter->publish_done);" in after_drain and
              "wait_for_completion_timeout" not in after_drain,
              "patch10 altered the existing unbounded committed-publication drain")

        OWN.verify_stucked_and_close_lifetimes(ninth_sources)
        proto = ninth_sources[PROTO_C].decode("utf-8")
        session_close = LIVE.braced_declaration(
            before_session, "int npu_session_close(")
        notify = LIVE.braced_declaration(
            before_session, "int npu_session_NW_CMD_POWER_NOTIFY(")
        handler = LIVE.exact_power_ctl_case(proto)
        check("mutex_lock(session->global_lock)" in session_close and
              "NPU_SESSION_STATE_CLOSE" in session_close and
              "mutex_lock(session->global_lock)" in notify and
              "npu_session_wait_power_request(session, NPU_NW_CMD_POWER_CTL)" in notify,
              "source no longer serializes the waiter caller and session close")
        for marker in (
            "nw_power_wait_begin_publish(&entry->nw)",
            "nw_power_wait_authorize_publish(&entry->nw)",
            "__mbox_nw_ops_put(entry)",
            "nw_power_wait_finish(publish_cookie, publish_req_id)",
        ):
            check(marker in handler,
                  f"exact requested-worker path lacks {marker}")
        check(handler.find("nw_power_wait_begin_publish") <
              handler.find("nw_power_wait_authorize_publish") <
              handler.find("__mbox_nw_ops_put(entry)"),
              "worker no longer authorizes before the synchronous mailbox post")
        cookie_adapter = LIVE.braced_declaration(
            proto, "static u64 nw_power_wait_cookie(")
        adapters = [
            LIVE.braced_declaration(proto,
                                    "static int nw_power_wait_begin_publish("),
            LIVE.braced_declaration(proto,
                                    "static int nw_power_wait_authorize_publish("),
        ]
        finish_adapter = LIVE.braced_declaration(
            proto, "static void nw_power_wait_finish(")
        authorize = LIVE.braced_declaration(
            after_session, "int npu_session_power_wait_authorize_publish(")
        finish = LIVE.braced_declaration(
            after_session, "void npu_session_power_wait_finish_publish(")
        wait_body = LIVE.braced_declaration(
            after_session, "static int npu_session_wait_power_request(")
        check("nw->param0" in cookie_adapter and "nw->param1" in cookie_adapter and
              all("nw_power_wait_cookie(nw)" in adapter and
                  "struct npu_power_waiter" not in adapter and
                  "struct npu_power_waiter *" not in adapter
                  for adapter in adapters) and
              "u64 cookie" in finish_adapter and
              "struct npu_power_waiter" not in finish_adapter,
              "publisher adapter no longer carries only cookie/reqid, or retains a waiter pointer")
        check("spin_lock_irqsave(&npu_power_waiters_lock" in authorize and
              "npu_power_waiter_find(cookie)" in authorize and
              "!waiter->cancelled" in authorize and
              "waiter->publish_committed = true" in authorize and
              "spin_unlock_irqrestore(&npu_power_waiters_lock" in authorize,
              "authorization no longer relooks up and commits under the waiter lock")
        check("npu_power_waiter_find(cookie)" in finish and
              "publish_done" in finish and
              "npu_power_waiter_find(cookie)" in LIVE.braced_declaration(
                  after_session, "int npu_session_save_power_result(") and
              "!waiter->cancelled" in LIVE.braced_declaration(
                  after_session, "int npu_session_save_power_result("),
              "finish/callback no longer reacquire waiter only by cookie")
        check("npu_power_wait_cancel_and_drain(&waiter)" in wait_body and
              wait_body.find("list_del_init(&waiter.list)",
                             wait_body.find("npu_power_wait_cancel_and_drain(&waiter)")) >=
              wait_body.find("npu_power_wait_cancel_and_drain(&waiter)"),
              "caller no longer unlinks only after cancel/drain returns")
        print("PASS source audit: cancel and authorize serialize on waiter lock; publisher carries cookie/request ID, not waiter pointer")
        print("PASS source audit: POWER_NOTIFY and CLOSE share session global_lock; pre-authorization worker can later reject unlinked cookie")

        baseline_unit = LIVE.make_translation_unit(
            ninth_sources[SESSION_C], ninth_sources[PROTO_C],
            ninth_sources[MAILBOX_C], ninth_sources[MSGID_C],
            sources[MSGID_H], sources[OWN.HWDEV_H],
            expected_reclaim=True)
        patched_unit = LIVE.make_translation_unit(
            tenth_sources[SESSION_C], tenth_sources[PROTO_C],
            tenth_sources[MAILBOX_C], tenth_sources[MSGID_C],
            sources[MSGID_H], sources[OWN.HWDEV_H],
            expected_reclaim=True)
        harness_text = HARNESS.read_text(encoding="utf-8")
        baseline_unit = customize_unit(baseline_unit, patched=False,
                                       harness=harness_text)
        patched_unit = customize_unit(patched_unit, patched=True,
                                      harness=harness_text)

        for optimization in ("-O0", "-O2"):
            baseline = compile_and_run(
                compiler, temp, f"drain-baseline-{optimization[2:]}",
                baseline_unit, optimization)
            print(baseline, end="")
            patched = compile_and_run(
                compiler, temp, f"drain-patch10-{optimization[2:]}",
                patched_unit, optimization)
            print(patched, end="")
        print("PASS exact nine-patch baseline vs tenth patch; patch10 changes only npu-session.c")
        print("LIMIT committed publication still drains without bound; this does not prove full callback/AST/close liveness or close/reopen quiescence")
        print("LIMIT extracted target C with pthread/list/completion/queue/mailbox shims only; no kernel build or device acceptance")
    return 0


if __name__ == "__main__":
    sys.exit(main())
