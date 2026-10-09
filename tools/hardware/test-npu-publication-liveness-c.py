#!/usr/bin/env python3
"""Extract pinned NPU C and exercise POWER_CTL publication/lifetime boundaries.

This is a host-only C regression. It composes the reviewed six-patch source,
then tests a separate seventh mailbox cleanup patch. Linux locking,
completion, queue, mailbox, and scheduler behavior are shimmed explicitly.
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
FULL_PROFILE_TEST = ROOT / "tools/hardware/test-npu-full-lifecycle-profile.py"
SHUTDOWN_ERROR_TEST = ROOT / "tools/hardware/test-npu-shutdown-error-propagation.py"
WAIT_HARNESS = ROOT / "tools/hardware/npu-power-wait-harness.c"
MAILBOX_PATCH = ROOT / "tools/hardware/npu-mailbox-missing-callback-reclaim.patch"
MAILBOX_PATCH_SHA256 = "f109b57381b3f2afcf2638b518f50db59ef8c9f784debd949488c74f8ea5c39b"

MAILBOX_C = "drivers/vision/npu/core/npu-if-protodrv-mbox2.c"
MSGID_C = "drivers/vision/npu/core/npu-util-msgidgen.c"
MSGID_H = "drivers/vision/npu/core/npu-util-msgidgen.h"
EXTRA_SOURCE_SHA256 = {
    MAILBOX_C: "d13247bd90ab27dd58cfe9241ec1a07460ab3b353d86a268f8a6fadefc6ff755",
    MSGID_C: "271dfe4f0b591a9a6d5a3f996e5fd2fe7a05d9b839513ce8691863bae79d3e2e",
    MSGID_H: "ff3cd551102edf433e3ef0c372ee2b8ed2cd8caf672ac00853d3ea9a134736a8",
}


def check(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    check(spec is not None and spec.loader is not None,
          f"cannot load reviewed host helper {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


FULL = load_module(FULL_PROFILE_TEST, "s22_npu_publication_full_profile")
ERROR = load_module(SHUTDOWN_ERROR_TEST, "s22_npu_publication_error_helpers")
RECON = FULL.RECON
STACK = FULL.STACK
OWN = FULL.OWN


def function_body(source: str, marker: str) -> str:
    body = OWN.function_body(source, marker)
    check(bool(body), f"pinned source is missing function {marker}")
    return body


def braced_declaration(source: str, marker: str) -> str:
    start = source.find(marker)
    check(start >= 0, f"pinned source is missing declaration {marker}")
    brace = source.find("{", start)
    check(brace >= 0, f"pinned declaration has no body: {marker}")
    depth = 0
    for end in range(brace, len(source)):
        if source[end] == "{":
            depth += 1
        elif source[end] == "}":
            depth -= 1
            if depth == 0:
                finish = end + 1
                if source[finish:finish + 1] == ";":
                    finish += 1
                return source[start:finish]
    raise RuntimeError(f"pinned declaration is incomplete: {marker}")


def source_block(source: str, start_marker: str, end_marker: str) -> str:
    start = source.find(start_marker)
    end = source.find(end_marker, start + len(start_marker)) if start >= 0 else -1
    check(start >= 0 and end > start,
          f"pinned source block boundaries changed: {start_marker!r}, {end_marker!r}")
    return source[start:end]


def source_sha256(label: str, data: bytes, expected: str) -> None:
    actual = hashlib.sha256(data).hexdigest()
    check(actual == expected,
          f"SHA-256 mismatch for {label}: expected {expected}, got {actual}")


def load_exact_sources() -> tuple[dict[str, bytes], Path | None, str, int]:
    sources, source_root, identity, total = FULL.load_exact_sources()
    added_bytes = 0
    for relative, expected in EXTRA_SOURCE_SHA256.items():
        data = STACK.load_extra_fixture(relative, expected)
        check(len(data) <= STACK.HELPERS.MAX_SOURCE_BYTES,
              f"mailbox source extra exceeds per-file bound: {relative}")
        if relative in sources:
            check(sources[relative] == data,
                  f"overlapping mailbox source fixture differs: {relative}")
        else:
            sources[relative] = data
            added_bytes += len(data)
    extra_limit = len(EXTRA_SOURCE_SHA256) * STACK.HELPERS.MAX_SOURCE_BYTES
    check(added_bytes <= extra_limit,
          "mailbox source extras exceed the explicit aggregate bound")
    return sources, source_root, identity, total + added_bytes


def apply_patch(root: Path, patch: Path, label: str) -> None:
    FULL.apply_plain_patch(root, patch, label)


def exact_session_block(session: str) -> str:
    start_marker = "#ifdef CONFIG_NPU_USE_BOOT_IOCTL\n#define NPU_POWER_WAIT_TIMEOUT_MS"
    start = session.find(start_marker)
    check(start >= 0, "patched kernel source has no POWER_CTL waiter block")
    wait = braced_declaration(session, "static int npu_session_wait_power_request(")
    wait_end = session.find(wait, start)
    check(wait_end > start, "POWER_CTL waiter body is detached from its source block")
    close = session.find("\n#endif", wait_end + len(wait))
    check(close > wait_end, "POWER_CTL waiter source block has no CONFIG guard end")
    block = session[start:close + len("\n#endif")]
    check("wait_for_completion(&waiter->publish_done)" in block and
          "npu_power_wait_cancel_and_drain(&waiter)" in block,
          "extracted waiter block lost its publication drain")
    drain = braced_declaration(
        session, "static void npu_power_wait_cancel_and_drain(")
    check("wait_for_completion(&waiter->publish_done)" in drain and
          "wait_for_completion_timeout" not in drain,
          "publication drain is no longer the unbounded completion wait under review")
    return block


def exact_proto_helper_block(proto: str) -> str:
    start_marker = (
        "#ifdef CONFIG_NPU_USE_BOOT_IOCTL\n"
        "extern int npu_session_power_wait_assign_req_id"
    )
    start = proto.find(start_marker)
    finish = braced_declaration(proto, "static void nw_power_wait_finish(")
    finish_start = proto.find(finish, start)
    check(start >= 0 and finish_start > start,
          "pinned protodrv waiter adapters are missing")
    close = proto.find("\n#endif", finish_start + len(finish))
    check(close > finish_start, "pinned protodrv adapter guard is incomplete")
    return proto[start:close + len("\n#endif")]


def exact_power_ctl_case(proto: str) -> str:
    handler = braced_declaration(
        proto, "static int  npu_protodrv_handler_nw_requested(void)")
    start = handler.find("case NPU_NW_CMD_POWER_CTL:")
    end = handler.find("\n#else", start)
    check(start >= 0 and end > start,
          "pinned protodrv POWER_CTL case boundaries changed")
    case = handler[start:end]
    for marker in (
        "nw_power_wait_begin_publish(&entry->nw)",
        "nw_power_wait_authorize_publish(&entry->nw)",
        "__mbox_nw_ops_put(entry)",
        "nw_power_wait_finish(publish_cookie, publish_req_id)",
    ):
        check(marker in case, f"actual POWER_CTL branch omits {marker}")
    check(case.find("nw_power_wait_begin_publish") <
          case.find("nw_power_wait_authorize_publish") <
          case.find("__mbox_nw_ops_put(entry)") <
          case.rfind("nw_power_wait_finish") and
          case.count("nw_power_wait_finish") >= 2,
          "actual POWER_CTL branch lease/authorization/post/finish order changed")
    return case


def exact_msgid_functions(msgid: str) -> str:
    markers = (
        "int msgid_issue(struct msgid_pool *handle, struct npu_session *session)",
        "int msgid_issue_save_ref(struct msgid_pool *handle, const int pt_type,",
        "static inline int __msgid_claim(struct msgid_pool *handle, const int msg_id)",
        "static inline void __validate_handle_msgid(struct msgid_pool *handle, const int msg_id)",
        "void msgid_claim(struct msgid_pool *handle, const int msg_id)",
    )
    # Keep the source's CONFIG_DSP_USE_VS4L branches intact. The target build
    # enables that config, and the generated C unit defines it explicitly.
    return "\n\n".join(braced_declaration(msgid, marker) for marker in markers)


def make_translation_unit(session: bytes, proto: bytes, mailbox: bytes,
                          msgid: bytes, msgid_header: bytes,
                          hw_device_header: bytes, *,
                          expected_reclaim: bool) -> str:
    host = WAIT_HARNESS.read_text(encoding="utf-8")
    anchor = "/* Exact production C is emitted here by the Python test; do not hand-copy it. */"
    check(host.count(anchor) == 1, "existing NPU C shim insertion point changed")
    prelude = host.split(anchor, 1)[0]

    session_source = session.decode("utf-8")
    proto_source = proto.decode("utf-8")
    mailbox_source = mailbox.decode("utf-8")
    msgid_source = msgid.decode("utf-8")
    msgid_header_text = msgid_header.decode("utf-8")
    hw_header_text = hw_device_header.decode("utf-8")
    msgid_functions = exact_msgid_functions(msgid_source)
    session_block = exact_session_block(session_source)
    proto_helper_block = exact_proto_helper_block(proto_source)
    power_case = exact_power_ctl_case(proto_source)
    mailbox_function = braced_declaration(
        mailbox_source, "int npu_nw_mbox_ops_put(")
    legacy_mailbox_function = (
        "#undef CONFIG_NPU_USE_BOOT_IOCTL\n"
        "#define npu_nw_mbox_ops_put npu_nw_mbox_ops_put_legacy\n"
        + mailbox_function + "\n"
        "#undef npu_nw_mbox_ops_put\n"
        "#define CONFIG_NPU_USE_BOOT_IOCTL 1\n"
    )

    session_close = braced_declaration(session_source, "int npu_session_close(")
    notify = braced_declaration(
        session_source, "int npu_session_NW_CMD_POWER_NOTIFY(")
    check("mutex_lock(session->global_lock)" in session_close and
          "NPU_SESSION_STATE_CLOSE" in session_close,
          "session close no longer gates teardown on the session lock")
    check("mutex_lock(session->global_lock)" in notify and
          "npu_session_wait_power_request(session, NPU_NW_CMD_POWER_CTL)" in notify,
          "POWER_NOTIFY no longer holds the session lock across the actual waiter")

    magic_match = re.search(r"^#define MSGID_POOL_MAGIC\s+(0x[0-9A-Fa-f]+)",
                            msgid_header_text, re.MULTILINE)
    check(magic_match is not None,
          "pinned msgid header does not expose MSGID_POOL_MAGIC on this fixture")
    msgid_magic = magic_match.group(1)
    npu_id_match = re.search(r"\bNPU_HWDEV_ID_NPU\s*=\s*(0x[0-9A-Fa-f]+|\d+)",
                             hw_header_text)
    dsp_id_match = re.search(r"\bNPU_HWDEV_ID_DSP\s*=\s*(0x[0-9A-Fa-f]+|\d+)",
                             hw_header_text)
    check(npu_id_match is not None and dsp_id_match is not None,
          "pinned hardware-device header has no NPU/DSP IDs")
    npu_hwdev_id = npu_id_match.group(1)
    dsp_hwdev_id = dsp_id_match.group(1)

    c_types = r"""
#define CONFIG_NPU_USE_BOOT_IOCTL 1
#define CONFIG_NPU_MAILBOX_VERSION 9
#define CONFIG_DSP_USE_VS4L 1
#define EXPECT_MISSING_RECLAIM EXPECT_RECLAIM_VALUE
#define NPU_NW_JUST_STARTED 77
#define NPU_NW_MAGIC_TAIL 0xface
#define NPU_ERR_NO_ERROR 0
#define NPU_ERR_NPU_TIMEOUT 1
#define NPU_ERR_QUEUE_TIMEOUT 2
#define NPU_ERR_SCHED_TIMEOUT 3
#define NPU_ERR_NPU_HW_TIMEOUT_RECOVERED 4
#define NPU_ERR_NPU_HW_TIMEOUT_NOTRECOVERABLE 5
#define NPU_ERR_NO_MEMORY 6
#define NPU_ERR_INVALID_UID 7
#define NPU_ERR_INVALID_STATE 8
#define NPU_ERR_SIZE_NOT_MATCH 9
#define NPU_ERR_IN_EMERGENCY 10
#define NPU_ERR_CODE(value) (value)
#define NPU_ERR_DRIVER(value) (1000 + (value))
#define NPU_CRITICAL_DRIVER(value) (2000 + (value))
#define MSGID_POOL_MAGIC MSGID_MAGIC_VALUE
#define NPU_HWDEV_ID_NPU NPU_ID_VALUE
#define NPU_HWDEV_ID_DSP DSP_ID_VALUE
#define NPU_MAX_MSG_ID_CNT 4
#define PROTO_DRV_REQ_TYPE_NW 1
#define FW_LOGSIZE 0
#define likely(value) (value)
#define unlikely(value) (value)
#define npu_dbg(...) ((void)0)
#define npu_warn(...) ((void)0)
#define npu_err(...) ((void)0)
#define npu_uinfo(...) ((void)0)
#define npu_uwarn(...) ((void)0)
#define npu_utrace(...) ((void)0)
#define npu_log_protodrv_set_data(...) ((void)0)
#define BUG_ON(value) do { if (value) abort(); } while (0)
#define WARN_ON(value) (value)

typedef struct { int counter; } atomic_t;
static atomic_int host_atomic_xchg_calls;
static inline void atomic_set(atomic_t *value, int next) { value->counter = next; }
static inline int atomic_cmpxchg(atomic_t *value, int old, int next)
{
    return __sync_val_compare_and_swap(&value->counter, old, next);
}
static inline int atomic_xchg(atomic_t *value, int next)
{
    atomic_fetch_add(&host_atomic_xchg_calls, 1);
    return __sync_lock_test_and_set(&value->counter, next);
}
static inline u64 atomic64_inc_return(atomic64_t *value)
{
    return __atomic_add_fetch(value, 1, __ATOMIC_SEQ_CST);
}

typedef enum { NPU_NW_CMD_POWER_CTL = 1 } nw_cmd_e;
#define NPU_NW_CMD_POWER_DOWN ((nw_cmd_e)2)
struct npu_nw;
typedef int (*save_result_func)(struct npu_session *, struct nw_result);
struct npu_nw {
    int uid;
    int bound_id;
    npu_req_id_t npu_req_id;
    int result_code;
    int result_value;
    struct npu_session *session;
    u32 param0;
    u32 param1;
    nw_cmd_e cmd;
    int ncp_addr;
    int magic_tail;
    int msgid;
    save_result_func notify_func;
};
struct npu_session {
    int uid;
    int hids;
    struct { int bound_id; } sched_param;
    struct { int ncp_addr; } ncp_info;
    struct nw_result nw_result;
};
struct msgid_pool {
    struct { atomic_t occupied; int pt_type; void *ref; } pool[NPU_MAX_MSG_ID_CNT];
    u32 magic;
};
struct proto_req_nw { struct npu_nw nw; };
struct npu_if_protodrv_mbox_ops {
    int (*nw_post_request)(int msgid, struct npu_nw *nw);
};
struct { struct npu_if_protodrv_mbox_ops *npu_if_protodrv_mbox_ops; } protodrv_mbox;
struct { struct msgid_pool msgid_pool; void *npu_device; } npu_proto_drv;
enum { FREE = 0, PROCESSING = 1, COMPLETED = 2 };
struct { void (*lsm_move_entry)(int state, struct proto_req_nw *entry); } proto_nw_lsm;

struct caller_state;
enum harness_mode {
    MODE_BEFORE_ASSIGN = 1,
    MODE_BEFORE_CASE = 2,
    MODE_BEFORE_AUTHORIZE = 3,
    MODE_STALLED_POST = 4,
    MODE_FAILED_POST = 5,
    MODE_MISSING_CALLBACK = 6,
};
static int harness_mode;
static atomic_uint_fast64_t active_cookie;
static atomic_uint_fast64_t active_req_id;
static atomic_int request_queued;
static atomic_int request_assigned;
static atomic_int begin_paused;
static atomic_int release_begin;
static atomic_int release_before_assign;
static atomic_int release_before_case;
static atomic_int mailbox_entered;
static atomic_int release_mailbox;
static atomic_int worker_done;
static atomic_int caller_returned;
static atomic_int worker_result;
static atomic_int lsm_move_count;
static atomic_int lsm_last_state;
static atomic_int mailbox_callback_count;
static atomic_int mailbox_lock_was_free;
static int mailbox_result;
static pthread_t publisher_thread;
static struct npu_nw queued_request;
static struct proto_req_nw queue_entry;
static pthread_mutex_t host_gate_lock = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t host_gate_changed = PTHREAD_COND_INITIALIZER;

static int npu_ncp_mgmt_put(const struct npu_nw *request);
static int npu_ncp_mgmt_get(struct npu_nw *request);
static npu_req_id_t get_next_npu_req_id(void);
static int npu_device_is_emergency_err(void *device);
static const char *__cmd_name(nw_cmd_e command) __attribute__((unused));
static unsigned long wait_for_completion_timeout(struct completion *completion,
                                                 unsigned long timeout);
static unsigned long msecs_to_jiffies(unsigned long milliseconds);
static int msgid_issue_save_ref(struct msgid_pool *handle, const int pt_type,
                                void *ref, struct npu_session *session);
void msgid_claim(struct msgid_pool *handle, const int msg_id);
int npu_nw_mbox_ops_put(struct msgid_pool *pool, struct proto_req_nw *src);
int npu_nw_mbox_ops_put_legacy(struct msgid_pool *pool, struct proto_req_nw *src);
static void fw_will_note(int code);
"""
    c_types = (c_types.replace("EXPECT_RECLAIM_VALUE", "1" if expected_reclaim else "0")
                       .replace("MSGID_MAGIC_VALUE", msgid_magic)
                       .replace("NPU_ID_VALUE", npu_hwdev_id)
                       .replace("DSP_ID_VALUE", dsp_hwdev_id))

    branch_wrapper = (
        "\nstatic int run_actual_power_ctl_case(struct proto_req_nw *entry) {\n"
        "    int proc_handle_cnt = 0;\n"
        "    int compl_handle_cnt = 0;\n"
        "    switch (entry->nw.cmd) {\n"
        + power_case +
        "\n    }\n"
        "    return proc_handle_cnt + compl_handle_cnt;\n"
        "}\n"
    )

    c_support = r"""
static void fail(const char *message)
{
    fprintf(stderr, "publication-liveness-c: %s\n", message);
    exit(1);
}

static void fw_will_note(int code)
{
    (void)code;
}

static void expect(bool condition, const char *message)
{
    if (!condition)
        fail(message);
}

static void gate_signal(atomic_int *flag)
{
    pthread_mutex_lock(&host_gate_lock);
    atomic_store(flag, 1);
    pthread_cond_broadcast(&host_gate_changed);
    pthread_mutex_unlock(&host_gate_lock);
}

static bool wait_flag(atomic_int *flag, unsigned int timeout_ms)
{
    struct timespec deadline;
    clock_gettime(CLOCK_REALTIME, &deadline);
    deadline.tv_sec += timeout_ms / 1000;
    deadline.tv_nsec += (long)(timeout_ms % 1000) * 1000000L;
    if (deadline.tv_nsec >= 1000000000L) {
        deadline.tv_sec++;
        deadline.tv_nsec -= 1000000000L;
    }
    pthread_mutex_lock(&host_gate_lock);
    while (!atomic_load(flag)) {
        int status = pthread_cond_timedwait(&host_gate_changed, &host_gate_lock,
                                            &deadline);
        if (status == ETIMEDOUT) {
            pthread_mutex_unlock(&host_gate_lock);
            return false;
        }
    }
    pthread_mutex_unlock(&host_gate_lock);
    return true;
}

static void wait_or_fail(atomic_int *flag, const char *what)
{
    if (!wait_flag(flag, 3000)) {
        char message[180];
        snprintf(message, sizeof(message), "timed out waiting for %s", what);
        fail(message);
    }
}

static void host_spin_unlock_irqrestore(pthread_mutex_t *lock)
{
    pthread_mutex_unlock(lock);
    if (lock != &npu_power_waiters_lock || harness_mode != MODE_BEFORE_AUTHORIZE)
        return;

    pthread_mutex_lock(lock);
    struct npu_power_waiter *waiter =
        npu_power_waiter_find(atomic_load(&active_cookie));
    bool pause = waiter && waiter->publishing && !waiter->publish_committed &&
                 !waiter->cancelled;
    pthread_mutex_unlock(lock);
    if (pause && atomic_exchange(&begin_paused, 1) == 0) {
        pthread_mutex_lock(&host_gate_lock);
        pthread_cond_broadcast(&host_gate_changed);
        while (!atomic_load(&release_begin))
            pthread_cond_wait(&host_gate_changed, &host_gate_lock);
        pthread_mutex_unlock(&host_gate_lock);
    }
}

static unsigned long msecs_to_jiffies(unsigned long milliseconds)
{
    return milliseconds;
}

static unsigned long wait_for_completion_timeout(struct completion *completion,
                                                 unsigned long timeout)
{
    (void)completion;
    (void)timeout;
    switch (harness_mode) {
    case MODE_BEFORE_ASSIGN:
        wait_or_fail(&request_queued, "queued request before assignment");
        break;
    case MODE_BEFORE_CASE:
        wait_or_fail(&request_assigned, "assigned request before publication case");
        break;
    case MODE_BEFORE_AUTHORIZE:
        wait_or_fail(&begin_paused, "publisher pause after lease begin");
        break;
    case MODE_STALLED_POST:
    case MODE_FAILED_POST:
        wait_or_fail(&mailbox_entered, "synchronous mailbox post entry");
        break;
    case MODE_MISSING_CALLBACK:
        wait_or_fail(&worker_done, "missing-callback publication return");
        break;
    default:
        fail("unknown timeout shim mode");
    }
    return 0; /* Controlled expired response timeout; publication may still hold its lease. */
}

static int npu_ncp_mgmt_get(struct npu_nw *request)
{
    *request = queued_request;
    return 1;
}

static npu_req_id_t get_next_npu_req_id(void)
{
    return (npu_req_id_t)atomic_load(&active_req_id);
}

static int npu_device_is_emergency_err(void *device)
{
    (void)device;
    return 0;
}

static const char *__cmd_name(nw_cmd_e command)
{
    (void)command;
    return "POWER_CTL";
}

static void record_lsm_move(int state, struct proto_req_nw *entry)
{
    (void)entry;
    atomic_store(&lsm_last_state, state);
    atomic_fetch_add(&lsm_move_count, 1);
}

static int fake_nw_post_request(int msgid, struct npu_nw *nw)
{
    int tries;
    atomic_fetch_add(&mailbox_callback_count, 1);
    expect(nw->npu_req_id == atomic_load(&active_req_id),
           "actual mailbox callback saw the wrong request identity");
    expect(nw->msgid == msgid, "actual mailbox callback saw the wrong allocated msgid");
    for (tries = 0; tries < 1000; tries++) {
        if (pthread_mutex_trylock(&npu_power_waiters_lock) == 0) {
            atomic_store(&mailbox_lock_was_free, 1);
            pthread_mutex_unlock(&npu_power_waiters_lock);
            break;
        }
        struct timespec pause = { .tv_sec = 0, .tv_nsec = 1000000L };
        nanosleep(&pause, NULL);
    }
    gate_signal(&mailbox_entered);
    pthread_mutex_lock(&host_gate_lock);
    while (!atomic_load(&release_mailbox))
        pthread_cond_wait(&host_gate_changed, &host_gate_lock);
    pthread_mutex_unlock(&host_gate_lock);
    return mailbox_result;
}

static void *publisher_main(void *unused)
{
    int ret;
    (void)unused;
    if (harness_mode == MODE_BEFORE_ASSIGN)
        wait_or_fail(&release_before_assign, "release before request assignment");
    ret = nw_mgmt_op_get_request(&queue_entry);
    atomic_store(&worker_result, ret);
    if (ret > 0) {
        gate_signal(&request_assigned);
        if (harness_mode == MODE_BEFORE_CASE)
            wait_or_fail(&release_before_case, "release before POWER_CTL case");
        run_actual_power_ctl_case(&queue_entry);
    }
    gate_signal(&worker_done);
    return NULL;
}

static int npu_ncp_mgmt_put(const struct npu_nw *request)
{
    expect(request->session == NULL,
           "POWER_CTL request must not retain its originating session pointer");
    queued_request = *request;
    atomic_store(&active_cookie, ((u64)request->param1 << 32) | request->param0);
    memset(&queue_entry, 0, sizeof(queue_entry));
    gate_signal(&request_queued);
    if (pthread_create(&publisher_thread, NULL, publisher_main, NULL) != 0)
        return -EAGAIN;
    return 1;
}

struct caller_state {
    struct npu_session session;
    int result;
};

static void *caller_main(void *opaque)
{
    struct caller_state *state = opaque;
    state->result = npu_session_wait_power_request(&state->session,
                                                    NPU_NW_CMD_POWER_CTL);
    gate_signal(&caller_returned);
    return NULL;
}

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

static void start_caller(struct caller_state *state, pthread_t *thread)
{
    memset(state, 0, sizeof(*state));
    state->session.uid = 19;
    state->session.sched_param.bound_id = 7;
    state->session.ncp_info.ncp_addr = 99;
    expect(pthread_create(thread, NULL, caller_main, state) == 0,
           "failed to start actual session waiter");
}

static bool registered_waiter_state(bool *cancelled, bool *done,
                                    bool *publishing, bool *committed)
{
    unsigned long flags = 0;
    struct npu_power_waiter *waiter;
    bool found;
    spin_lock_irqsave(&npu_power_waiters_lock, flags);
    waiter = npu_power_waiter_find(atomic_load(&active_cookie));
    found = waiter != NULL;
    if (found) {
        *cancelled = waiter->cancelled;
        *done = waiter->done;
        *publishing = waiter->publishing;
        *committed = waiter->publish_committed;
    }
    spin_unlock_irqrestore(&npu_power_waiters_lock, flags);
    return found;
}

static bool wait_until_cancelled(void)
{
    unsigned int i;
    for (i = 0; i < 3000; i++) {
        bool cancelled = false, done = false, publishing = false, committed = false;
        if (registered_waiter_state(&cancelled, &done, &publishing, &committed) &&
            cancelled)
            return true;
        struct timespec pause = { .tv_sec = 0, .tv_nsec = 1000000L };
        nanosleep(&pause, NULL);
    }
    return false;
}

static struct nw_result matching_response(void)
{
    struct nw_result response = { 0 };
    u64 cookie = atomic_load(&active_cookie);
    response.nw.param0 = (u32)cookie;
    response.nw.param1 = (u32)(cookie >> 32);
    response.nw.npu_req_id = (npu_req_id_t)atomic_load(&active_req_id);
    response.result_code = -EIO;
    return response;
}

static int occupied_count(void)
{
    int i, count = 0;
    for (i = 0; i < NPU_MAX_MSG_ID_CNT; i++)
        count += npu_proto_drv.msgid_pool.pool[i].occupied.counter != 0;
    return count;
}

static void reset_msgid_pool(void)
{
    memset(&npu_proto_drv.msgid_pool, 0, sizeof(npu_proto_drv.msgid_pool));
    npu_proto_drv.msgid_pool.magic = MSGID_POOL_MAGIC;
}

static void test_actual_msgid_allocator_partitions(void)
{
    struct npu_session session = { 0 };
    int i;

    reset_msgid_pool();
    session.hids = NPU_HWDEV_ID_NPU;
    expect(msgid_issue(&npu_proto_drv.msgid_pool, &session) == 0 &&
           msgid_issue(&npu_proto_drv.msgid_pool, &session) == 1 &&
           msgid_issue(&npu_proto_drv.msgid_pool, &session) == -1,
           "actual NPU allocation branch did not stay in the first half");

    reset_msgid_pool();
    session.hids = NPU_HWDEV_ID_DSP;
    expect(msgid_issue(&npu_proto_drv.msgid_pool, &session) == 2 &&
           msgid_issue(&npu_proto_drv.msgid_pool, &session) == 3 &&
           msgid_issue(&npu_proto_drv.msgid_pool, &session) == -1,
           "actual DSP allocation branch did not stay in the second half");

    reset_msgid_pool();
    session.hids = 0;
    for (i = 0; i < NPU_MAX_MSG_ID_CNT; i++)
        expect(msgid_issue(&npu_proto_drv.msgid_pool, &session) == i,
               "actual unclassified-session branch did not use the whole pool");
    expect(msgid_issue(&npu_proto_drv.msgid_pool, &session) == -1,
           "actual unclassified-session branch exceeded the whole pool");

    reset_msgid_pool();
    for (i = 0; i < NPU_MAX_MSG_ID_CNT; i++)
        expect(msgid_issue(&npu_proto_drv.msgid_pool, NULL) == i,
               "actual null-session branch did not use the whole pool");
    expect(msgid_issue(&npu_proto_drv.msgid_pool, NULL) == -1,
           "actual null-session branch exceeded the whole pool");
    puts("PASS actual msgid allocator C: NPU/DSP split plus unclassified and NULL session use");
}

static void test_legacy_missing_callback_no_boot_ioctl(void)
{
    struct proto_req_nw request = { 0 };
    struct npu_if_protodrv_mbox_ops ops = { .nw_post_request = NULL };
    int i;

    reset_msgid_pool();
    atomic_set(&npu_proto_drv.msgid_pool.pool[0].occupied, 1);
    npu_proto_drv.msgid_pool.pool[0].ref = &npu_proto_drv;
    protodrv_mbox.npu_if_protodrv_mbox_ops = &ops;
    request.nw.cmd = NPU_NW_CMD_POWER_DOWN;
    atomic_store(&host_atomic_xchg_calls, 0);
    expect(npu_nw_mbox_ops_put_legacy(&npu_proto_drv.msgid_pool, &request) == 0,
           "legacy absent-callback POWER_DOWN did not preserve its return value");
    expect(request.nw.msgid == 0 && occupied_count() == 2 &&
           npu_proto_drv.msgid_pool.pool[0].occupied.counter == 1 &&
           npu_proto_drv.msgid_pool.pool[0].ref == &npu_proto_drv &&
           npu_proto_drv.msgid_pool.pool[1].occupied.counter == 1 &&
           npu_proto_drv.msgid_pool.pool[1].ref == &request &&
           atomic_load(&host_atomic_xchg_calls) == 0,
           "legacy forced msgid zero claimed a pre-existing ID or changed failure ownership");

    reset_msgid_pool();
    for (i = 0; i < NPU_MAX_MSG_ID_CNT; i++)
        atomic_set(&npu_proto_drv.msgid_pool.pool[i].occupied, 1);
    request.nw.msgid = -1;
    atomic_store(&host_atomic_xchg_calls, 0);
    expect(npu_nw_mbox_ops_put_legacy(&npu_proto_drv.msgid_pool, &request) == 0 &&
           request.nw.msgid == 0 && occupied_count() == NPU_MAX_MSG_ID_CNT &&
           atomic_load(&host_atomic_xchg_calls) == 0,
           "legacy forced zero caused a claim after the underlying allocator was exhausted");
    puts("PASS legacy no-BOOT_IOCTL C: missing-op claim stays excluded; slot-zero ownership is preserved");
}

static void test_msgid_missing_callback(void)
{
    struct proto_req_nw request = { 0 };
    struct npu_if_protodrv_mbox_ops ops = { .nw_post_request = NULL };
    int i;
    memset(&npu_proto_drv.msgid_pool, 0, sizeof(npu_proto_drv.msgid_pool));
    npu_proto_drv.msgid_pool.magic = MSGID_POOL_MAGIC;
    protodrv_mbox.npu_if_protodrv_mbox_ops = &ops;
    atomic_store(&host_atomic_xchg_calls, 0);
    for (i = 0; i < NPU_MAX_MSG_ID_CNT + 2; i++) {
        expect(npu_nw_mbox_ops_put(&npu_proto_drv.msgid_pool, &request) == 0,
               "missing callback must report publication failure");
#if EXPECT_MISSING_RECLAIM
        expect(occupied_count() == 0,
               "missing callback leaked an issued msgid");
#else
        expect(occupied_count() == (i < NPU_MAX_MSG_ID_CNT ? i + 1 : NPU_MAX_MSG_ID_CNT),
               "baseline missing callback no longer reproduces msgid pool exhaustion");
#endif
    }
    expect(atomic_load(&host_atomic_xchg_calls) ==
           (EXPECT_MISSING_RECLAIM ? NPU_MAX_MSG_ID_CNT + 2 : 0),
           "missing-callback path claimed the msgid an unexpected number of times");
    expect(atomic_load(&mailbox_callback_count) == 0,
           "missing callback fixture unexpectedly posted a mailbox request");
#if EXPECT_MISSING_RECLAIM
    puts("PASS patched actual mailbox C: absent nw_post_request reclaims every issued msgid");
#else
    puts("BASELINE REPRO actual mailbox C: absent nw_post_request exhausts the four-slot test pool");
#endif
}

static int fixed_post_result;
static int fixed_post_calls;
static int fake_fixed_post(int msgid, struct npu_nw *nw)
{
    (void)msgid;
    (void)nw;
    fixed_post_calls++;
    return fixed_post_result;
}

static void test_other_msgid_ownership_edges(void)
{
    struct proto_req_nw request = { 0 };
    struct npu_if_protodrv_mbox_ops ops = { .nw_post_request = fake_fixed_post };
    int status;

    memset(&npu_proto_drv.msgid_pool, 0, sizeof(npu_proto_drv.msgid_pool));
    npu_proto_drv.msgid_pool.magic = MSGID_POOL_MAGIC;
    protodrv_mbox.npu_if_protodrv_mbox_ops = &ops;
    fixed_post_calls = 0;
    atomic_store(&host_atomic_xchg_calls, 0);
    fixed_post_result = 1;
    status = npu_nw_mbox_ops_put(&npu_proto_drv.msgid_pool, &request);
    expect(status == 1 && fixed_post_calls == 1 && occupied_count() == 1,
           "successful mailbox post must retain its msgid for the response");
    expect(npu_proto_drv.msgid_pool.pool[request.nw.msgid].ref == &request,
           "successful mailbox post lost its request ownership reference");
    msgid_claim(&npu_proto_drv.msgid_pool, request.nw.msgid);
    expect(occupied_count() == 0 && atomic_load(&host_atomic_xchg_calls) == 1,
           "successful request response cleanup did not release exactly once");

    for (int failure = 0; failure < 2; failure++) {
        memset(&npu_proto_drv.msgid_pool, 0, sizeof(npu_proto_drv.msgid_pool));
        npu_proto_drv.msgid_pool.magic = MSGID_POOL_MAGIC;
        atomic_store(&host_atomic_xchg_calls, 0);
        fixed_post_result = failure == 0 ? 0 : -EIO;
        status = npu_nw_mbox_ops_put(&npu_proto_drv.msgid_pool, &request);
        expect(status == fixed_post_result && occupied_count() == 0,
               "zero/negative post result must reclaim its msgid");
        expect(atomic_load(&host_atomic_xchg_calls) == 1,
               "zero/negative post result must reclaim exactly once");
    }

    memset(&npu_proto_drv.msgid_pool, 0, sizeof(npu_proto_drv.msgid_pool));
    npu_proto_drv.msgid_pool.magic = MSGID_POOL_MAGIC;
    for (int i = 0; i < NPU_MAX_MSG_ID_CNT; i++)
        expect(msgid_issue(&npu_proto_drv.msgid_pool, NULL) >= 0,
               "test could not fill msgid pool before exhausted allocation");
    atomic_store(&host_atomic_xchg_calls, 0);
    fixed_post_calls = 0;
    status = npu_nw_mbox_ops_put(&npu_proto_drv.msgid_pool, &request);
    expect(status == 0 && request.nw.msgid == -1 && fixed_post_calls == 0,
           "exhausted allocation must not invoke callback or claim an invalid ID");
    expect(occupied_count() == NPU_MAX_MSG_ID_CNT &&
           atomic_load(&host_atomic_xchg_calls) == 0,
           "exhausted allocation changed pool ownership");
    puts("PASS actual msgid C: success retains; 0/negative post reclaim once; exhausted ID is untouched");
}

static void test_cancel_before_assignment(void)
{
    struct caller_state caller;
    pthread_t thread;
    reset_runtime(MODE_BEFORE_ASSIGN);
    start_caller(&caller, &thread);
    wait_or_fail(&request_queued, "request queue before assigned-ID cancellation");
    wait_or_fail(&caller_returned, "waiter return before delayed dequeue");
    expect(caller.result == -ETIMEDOUT,
           "timeout before protocol dequeue did not return after removing waiter");
    gate_signal(&release_before_assign);
    expect(pthread_join(publisher_thread, NULL) == 0,
           "pre-assignment publisher thread did not join");
    expect(pthread_join(thread, NULL) == 0, "pre-assignment caller did not join");
    expect(atomic_load(&worker_result) == -ECANCELED &&
           atomic_load(&mailbox_callback_count) == 0 &&
           atomic_load(&lsm_move_count) == 0,
           "cancelled waiter was admitted or published after delayed dequeue");
    puts("PASS actual protodrv C: cancellation before request-ID assignment rejects stale dequeue");
}

static void test_cancel_before_publish_begin(void)
{
    struct caller_state caller;
    pthread_t thread;
    reset_runtime(MODE_BEFORE_CASE);
    start_caller(&caller, &thread);
    wait_or_fail(&request_assigned, "request-ID assignment before begin cancellation");
    wait_or_fail(&caller_returned, "waiter return before delayed publication case");
    expect(caller.result == -ETIMEDOUT,
           "timeout before publish begin did not return after waiter removal");
    gate_signal(&release_before_case);
    expect(pthread_join(publisher_thread, NULL) == 0,
           "pre-begin publisher thread did not join");
    expect(pthread_join(thread, NULL) == 0, "pre-begin caller did not join");
    expect(atomic_load(&worker_result) == 1 &&
           atomic_load(&mailbox_callback_count) == 0 &&
           atomic_load(&lsm_move_count) == 1 &&
           atomic_load(&lsm_last_state) == FREE,
           "actual begin-publish failure did not return stale request to FREE");
    puts("PASS actual protodrv C: begin-publish failure returns stale request to FREE without mailbox post");
}

static void test_cancel_between_begin_and_authorize(void)
{
    struct caller_state caller;
    pthread_t thread;
    reset_runtime(MODE_BEFORE_AUTHORIZE);
    start_caller(&caller, &thread);
    wait_or_fail(&begin_paused, "test pause after actual publish lease begin");
    expect(wait_until_cancelled(), "timeout did not cancel waiter before publish authorization");
    expect(!atomic_load(&caller_returned),
           "caller returned while it owned an uncommitted publication lease");
    bool cancelled = false, done = false, publishing = false, committed = false;
    expect(registered_waiter_state(&cancelled, &done, &publishing, &committed) &&
           cancelled && !done && publishing && !committed,
           "waiter was not retained between begin and failed authorization");
    gate_signal(&release_begin);
    wait_or_fail(&caller_returned, "drain after failed publish authorization");
    expect(pthread_join(publisher_thread, NULL) == 0,
           "authorization-race publisher thread did not join");
    expect(pthread_join(thread, NULL) == 0, "authorization-race caller did not join");
    expect(caller.result == -ETIMEDOUT && atomic_load(&mailbox_callback_count) == 0 &&
           atomic_load(&lsm_move_count) == 1 && atomic_load(&lsm_last_state) == FREE &&
           occupied_count() == 0,
           "failed authorization did not drain/drop without mailbox resource ownership");
    puts("PASS actual protodrv C: cancellation after begin revokes authorization, drains, then drops request");
}

static void test_stalled_mailbox_post(bool fail_post)
{
    struct caller_state caller;
    pthread_t thread;
    enum harness_mode mode = fail_post ? MODE_FAILED_POST : MODE_STALLED_POST;
    reset_runtime(mode);
    mailbox_result = fail_post ? 0 : 1;
    start_caller(&caller, &thread);
    wait_or_fail(&mailbox_entered, "actual synchronous mailbox post stall");
    expect(atomic_load(&mailbox_lock_was_free),
           "actual synchronous mailbox post ran while waiter spinlock remained held");
    expect(wait_until_cancelled(), "timeout did not enter cancellation/drain during mailbox stall");
    expect(!atomic_load(&caller_returned),
           "caller returned while actual synchronous mailbox callback remained stalled");
    bool cancelled = false, done = false, publishing = false, committed = false;
    expect(registered_waiter_state(&cancelled, &done, &publishing, &committed) &&
           cancelled && !done && publishing && committed,
           "timed-out stack waiter was not retained under the committed publisher lease");
    expect(atomic_load(&lsm_move_count) == 0,
           "protocol entry transitioned while synchronous mailbox callback still used its request storage");

    struct nw_result response = matching_response();
    npu_session_save_power_result(NULL, response);
    npu_session_save_power_result(NULL, response);
    bool after_cancel = false;
    expect(registered_waiter_state(&cancelled, &after_cancel, &publishing, &committed) &&
           !after_cancel,
           "late/duplicate callback revived the cancelled waiter");
    expect(!atomic_load(&caller_returned),
           "callback activity let caller leave before the mailbox post finished");

    gate_signal(&release_mailbox);
    wait_or_fail(&caller_returned, "caller drain after synchronous mailbox return");
    expect(pthread_join(publisher_thread, NULL) == 0,
           "stalled mailbox publisher did not join after release");
    expect(pthread_join(thread, NULL) == 0, "stalled mailbox waiter did not join");
    expect(caller.result == -ETIMEDOUT && occupied_count() == (fail_post ? 0 : 1),
           "mailbox return did not preserve timeout and expected msgid ownership");
    if (fail_post) {
        expect(atomic_load(&lsm_move_count) == 0,
               "failed mailbox post unexpectedly transitioned to PROCESSING");
        expect(atomic_load(&host_atomic_xchg_calls) == 1,
               "failed mailbox post did not reclaim its msgid exactly once");
        puts("PASS actual protodrv/mailbox C: failed post reclaims msgid and finishes waiter lease");
    } else {
        expect(atomic_load(&lsm_move_count) == 1 &&
               atomic_load(&lsm_last_state) == PROCESSING,
               "successful mailbox post did not retain PROCESSING ownership");
        msgid_claim(&npu_proto_drv.msgid_pool, queue_entry.nw.msgid);
        expect(occupied_count() == 0,
               "simulated completion did not release successful publication msgid");
        puts("PASS actual protodrv/mailbox C: stalled post pins waiter until return; late callback ignored");
    }
}

static void test_missing_callback_publication_path(void)
{
    struct caller_state caller;
    pthread_t thread;
    struct npu_if_protodrv_mbox_ops ops = { .nw_post_request = NULL };
    reset_runtime(MODE_MISSING_CALLBACK);
    protodrv_mbox.npu_if_protodrv_mbox_ops = &ops;
    start_caller(&caller, &thread);
    wait_or_fail(&caller_returned, "missing-callback publisher completion and drain");
    expect(pthread_join(publisher_thread, NULL) == 0,
           "missing-callback publisher thread did not join");
    expect(pthread_join(thread, NULL) == 0, "missing-callback caller did not join");
    expect(caller.result == -ETIMEDOUT && occupied_count() == 0 &&
           atomic_load(&host_atomic_xchg_calls) == 1 &&
           atomic_load(&mailbox_callback_count) == 0,
           "actual missing-callback publication leaked msgid or failed its waiter drain");
    puts("PASS actual POWER_CTL C: absent mailbox op releases msgid and finishes publication lease");
}

static int main_final(void)
{
    test_actual_msgid_allocator_partitions();
    test_legacy_missing_callback_no_boot_ioctl();
    test_msgid_missing_callback();
    test_other_msgid_ownership_edges();
    if (EXPECT_MISSING_RECLAIM) {
        test_cancel_before_assignment();
        test_cancel_before_publish_begin();
        test_cancel_between_begin_and_authorize();
        test_stalled_mailbox_post(false);
        test_stalled_mailbox_post(true);
        test_missing_callback_publication_path();
        puts("UNRESOLVED LIVENESS actual C reproduction: caller stays blocked while synchronous post is held; production drain has no timeout");
        puts("LIMIT pthread/list/completion/queue/mailbox shims are not Linux locking, kernel build, or hardware evidence");
    }
    return 0;
}

int main(void)
{
    npu_power_wait_cookie = 0;
    return main_final();
}
"""

    # Turn the inherited shim's spin-unlock macro into a test-only scheduling
    # hook. It pauses after the real begin helper releases the waiter lock.
    instrument = (
        "#undef spin_unlock_irqrestore\n"
        "static void host_spin_unlock_irqrestore(pthread_mutex_t *lock);\n"
        "#define spin_unlock_irqrestore(lock, flags) do { (void)(flags); host_spin_unlock_irqrestore((lock)); } while (0)\n"
    )
    host = prelude + instrument + c_types + "\n" + session_block + "\n" + \
        proto_helper_block + "\n" + msgid_functions + "\n" + \
        legacy_mailbox_function + mailbox_function + "\n" + \
        braced_declaration(proto_source, "static int nw_mbox_ops_put(") + "\n" + \
        braced_declaration(proto_source, "static int __mbox_nw_ops_put(") + "\n" + \
        braced_declaration(proto_source, "static int nw_mgmt_op_get_request(") + "\n" + \
        branch_wrapper + c_support
    check("wait_for_completion(&waiter->publish_done)" in host and
          "__mbox_nw_ops_put(entry)" in host and
          "msgid_claim(pool, msgid)" in braced_declaration(
              mailbox_source, "int npu_nw_mbox_ops_put("),
          "generated C translation unit omitted exact production boundaries")
    return host


def compile_and_run(compiler: str, directory: Path, name: str, unit: str,
                    optimization: str, expected_reclaim: bool) -> str:
    source = directory / f"{name}.c"
    binary = directory / name
    source.write_text(unit, encoding="utf-8")
    compiled = subprocess.run(
        [compiler, "-std=gnu11", "-Wall", "-Wextra", "-Werror",
         optimization, "-pthread", str(source), "-o", str(binary)],
        capture_output=True, text=True, check=False, timeout=20,
    )
    check(compiled.returncode == 0,
          f"{name} did not compile at {optimization}:\n{compiled.stderr}")
    try:
        executed = subprocess.run([str(binary)], capture_output=True, text=True,
                                  check=False, timeout=15)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"{name} stalled beyond its host bound at {optimization}")
    check(executed.returncode == 0,
          f"{name} failed at {optimization}:\n{executed.stdout}{executed.stderr}")
    required = (
        "BASELINE REPRO actual mailbox C: absent nw_post_request exhausts the four-slot test pool"
        if not expected_reclaim else
        "PASS patched actual mailbox C: absent nw_post_request reclaims every issued msgid"
    )
    check(required in executed.stdout,
          f"{name} omitted baseline/final missing-callback marker at {optimization}")
    check("PASS actual msgid allocator C: NPU/DSP split plus unclassified and NULL session use"
          in executed.stdout,
          f"{name} omitted production allocator branch coverage at {optimization}")
    check("PASS legacy no-BOOT_IOCTL C: missing-op claim stays excluded; slot-zero ownership is preserved"
          in executed.stdout,
          f"{name} omitted the preserved legacy POWER_DOWN branch at {optimization}")
    if expected_reclaim:
        for marker in (
            "PASS actual msgid allocator C: NPU/DSP split plus unclassified and NULL session use",
            "PASS actual msgid C: success retains; 0/negative post reclaim once; exhausted ID is untouched",
            "PASS actual protodrv C: cancellation before request-ID assignment rejects stale dequeue",
            "PASS actual protodrv C: begin-publish failure returns stale request to FREE without mailbox post",
            "PASS actual protodrv C: cancellation after begin revokes authorization, drains, then drops request",
            "PASS actual protodrv/mailbox C: stalled post pins waiter until return; late callback ignored",
            "PASS actual protodrv/mailbox C: failed post reclaims msgid and finishes waiter lease",
            "PASS actual POWER_CTL C: absent mailbox op releases msgid and finishes publication lease",
            "UNRESOLVED LIVENESS actual C reproduction: caller stays blocked while synchronous post is held; production drain has no timeout",
            "LIMIT pthread/list/completion/queue/mailbox shims are not Linux locking, kernel build, or hardware evidence",
        ):
            check(marker in executed.stdout,
                  f"{name} omitted source-C path marker {marker!r} at {optimization}")
    return executed.stdout


def main() -> int:
    check(WAIT_HARNESS.is_file(), "existing bounded host synchronization shim is missing")
    sources, source_root, source_identity, source_bytes = load_exact_sources()
    patch_paths = RECON.patch_paths()
    FULL.patch_bytes(FULL.PROFILE_PATCH, FULL.PROFILE_PATCH.name,
                     FULL.PROFILE_SHA256)
    FULL.patch_bytes(OWN.OWN_PATCH, OWN.OWN_PATCH.name,
                     FULL.FROZEN_SHUTDOWN_SHA256)
    FULL.patch_bytes(ERROR.ERROR_PATCH, ERROR.ERROR_PATCH.name,
                     ERROR.ERROR_PATCH_SHA256)
    patch_data = MAILBOX_PATCH.read_bytes()
    source_sha256(MAILBOX_PATCH.name, patch_data, MAILBOX_PATCH_SHA256)
    check(len(patch_data) <= STACK.HELPERS.MAX_SOURCE_BYTES,
          "new mailbox patch exceeds the existing bounded input limit")

    compiler = shutil.which("cc") or shutil.which("gcc")
    check(compiler is not None, "host C compiler cc/gcc is required")
    print(f"SOURCE_FIXTURE {source_identity}")
    print(f"SOURCE_UNION_BYTES {source_bytes}")
    print("PATCH_ORDER " + " -> ".join(
        [*(path.name for path in patch_paths), FULL.PROFILE_PATCH.name,
         ERROR.ERROR_PATCH.name, MAILBOX_PATCH.name]))
    print("PASS SHA-256 loader: pinned source extras and six existing patches verified")

    with tempfile.TemporaryDirectory(prefix="s22-npu-publication-c-") as name:
        temp = Path(name)
        full_root = temp / "six-patch-source"
        full_root.mkdir()
        FULL.write_exact_fixture(full_root, sources)
        RECON.apply_patch_series(full_root, patch_paths)
        apply_patch(full_root, FULL.PROFILE_PATCH, "fifth shutdown lifecycle profile")
        apply_patch(full_root, ERROR.ERROR_PATCH, "sixth shutdown error propagation")
        before_mailbox = {relative: (full_root / relative).read_bytes()
                          for relative in sources}
        apply_patch(full_root, MAILBOX_PATCH,
                    "seventh missing-mailbox-op msgid reclaim")
        final_sources = {relative: (full_root / relative).read_bytes()
                         for relative in sources}
        changed = {relative for relative in sources
                   if before_mailbox[relative] != final_sources[relative]}
        check(changed == {MAILBOX_C},
              f"seventh patch changed unexpected files: {sorted(changed)!r}")
        base_mbox = before_mailbox[MAILBOX_C].decode("utf-8")
        final_mbox = final_sources[MAILBOX_C].decode("utf-8")
        base_put = braced_declaration(base_mbox, "int npu_nw_mbox_ops_put(")
        final_put = braced_declaration(final_mbox, "int npu_nw_mbox_ops_put(")
        expected_put = base_put.replace(
            '\t\tnpu_warn("not defined: nw_post_request()\\n");\n'
            '\t\treturn 0;',
            '\t\tnpu_warn("not defined: nw_post_request()\\n");\n'
            '#ifdef CONFIG_NPU_USE_BOOT_IOCTL\n'
            '\t\tmsgid_claim(pool, msgid);\n'
            '#endif\n'
            '\t\treturn 0;', 1,
        )
        check(expected_put == final_put and final_put != base_put,
              "seventh patch is not the single missing-callback msgid cleanup insertion")
        check("msgid_claim(pool, msgid);" in expected_put,
              "missing-callback cleanup patch effect is absent")
        check("#ifdef CONFIG_NPU_USE_BOOT_IOCTL\n\t\tmsgid_claim(pool, msgid);\n#endif"
              in final_put,
              "missing-callback claim escaped its BOOT_IOCTL safety guard")
        print("PASS ordinary six-patch composition plus seventh patch; only mbox2.c changed")

        before_sources = dict(before_mailbox)
        final_sources = dict(final_sources)
        session = final_sources["drivers/vision/npu/core/npu-session.c"]
        proto = final_sources["drivers/vision/npu/core/npu-protodrv.c"]
        msgid = final_sources[MSGID_C]
        base_unit = make_translation_unit(
            before_sources["drivers/vision/npu/core/npu-session.c"],
            before_sources["drivers/vision/npu/core/npu-protodrv.c"],
            before_mailbox[MAILBOX_C], msgid, sources[MSGID_H],
            sources[OWN.HW_H], expected_reclaim=False)
        final_unit = make_translation_unit(
            session, proto, final_sources[MAILBOX_C], msgid,
            sources[MSGID_H], sources[OWN.HW_H], expected_reclaim=True)
        # Force the generated host table shape to match the SHA-verified input.
        check("MSGID_POOL_MAGIC" in sources[MSGID_H].decode("utf-8"),
              "pinned msgid header constant is missing")

        for optimization in ("-O0", "-O2"):
            baseline = compile_and_run(
                compiler, temp, f"publication-baseline-{optimization[2:]}",
                base_unit, optimization, expected_reclaim=False)
            print(baseline, end="")
            final = compile_and_run(
                compiler, temp, f"publication-final-{optimization[2:]}",
                final_unit, optimization, expected_reclaim=True)
            print(final, end="")

        STACK.run_preflight(full_root, final_sources, temp)
        print("PASS preflight retains publication_drain_wait_unbounded and BOOTUP refusal")
        print("LIMIT host-extracted source C only; no kernel build, module, firmware, service, inference, or device evidence")
    return 0


if __name__ == "__main__":
    sys.exit(main())
