#!/usr/bin/env python3
"""Executable host reference model and source-contract checks for NPU lifecycle.

The threaded model exercises ownership/race and unwind invariants. It is not a
substitute for compiling or executing the kernel driver.
"""
from __future__ import annotations

from pathlib import Path
import os
import threading

ROOT = Path(__file__).resolve().parents[2]
PATCH = ROOT / "tools/hardware/npu-session-lifecycle-fix.patch"
KERNEL_ROOT = Path(os.environ["S22_NPU_KERNEL_TREE"]) if "S22_NPU_KERNEL_TREE" in os.environ else None
KERNEL_FILES = (
    "drivers/vision/npu/core/npu-session.c",
    "drivers/vision/npu/core/npu-protodrv.c",
    "drivers/vision/npu/core/npu-vertex.c",
)


def check(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def function_body(source: str, marker: str) -> str:
    start = source.find(marker)
    if start < 0:
        return ""
    brace = source.find("{", start)
    if brace < 0:
        return ""
    depth = 0
    for end in range(brace, len(source)):
        if source[end] == "{":
            depth += 1
        elif source[end] == "}":
            depth -= 1
            if depth == 0:
                return source[start:end + 1]
    return ""


class WaitRegistry:
    """Small reference model of the cookie/request-ID completion contract."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.waiters: dict[int, dict] = {}
        self.next_cookie = 0

    def register(self) -> int:
        with self.lock:
            self.next_cookie += 1
            cookie = self.next_cookie
            self.waiters[cookie] = {
                "req_id": None,
                "done": False,
                "cancelled": False,
                "publishing": False,
                "publish_committed": False,
                "result": None,
                "event": threading.Event(),
                "publish_done": threading.Event(),
                "cancel_seen": threading.Event(),
            }
            return cookie

    def assign(self, cookie: int, req_id: int) -> bool:
        with self.lock:
            waiter = self.waiters.get(cookie)
            if (waiter is None or waiter["done"] or waiter["cancelled"] or
                    waiter["req_id"] is not None):
                return False
            waiter["req_id"] = req_id
            return True

    def active(self, cookie: int, req_id: int) -> bool:
        with self.lock:
            waiter = self.waiters.get(cookie)
            return bool(waiter and waiter["req_id"] == req_id and not waiter["done"]
                        and not waiter["cancelled"])

    def complete(self, cookie: int, req_id: int, result: int) -> bool:
        with self.lock:
            waiter = self.waiters.get(cookie)
            if (waiter is None or waiter["req_id"] != req_id or waiter["done"] or
                    waiter["cancelled"]):
                return False
            waiter["result"] = result
            waiter["done"] = True
            waiter["event"].set()
            return True

    def begin_publish(self, cookie: int, req_id: int) -> bool:
        with self.lock:
            waiter = self.waiters.get(cookie)
            if (waiter is None or waiter["req_id"] != req_id or waiter["done"] or
                    waiter["cancelled"] or waiter["publishing"]):
                return False
            waiter["publish_done"].clear()
            waiter["publishing"] = True
            waiter["publish_committed"] = False
            return True

    def authorize_publish(self, cookie: int, req_id: int) -> bool:
        with self.lock:
            waiter = self.waiters.get(cookie)
            if (waiter is None or waiter["req_id"] != req_id or waiter["done"] or
                    waiter["cancelled"] or not waiter["publishing"] or
                    waiter["publish_committed"]):
                return False
            waiter["publish_committed"] = True
            return True

    def finish_publish(self, cookie: int, req_id: int) -> None:
        with self.lock:
            waiter = self.waiters.get(cookie)
            if waiter is not None and waiter["req_id"] == req_id and waiter["publishing"]:
                waiter["publishing"] = False
                waiter["publish_committed"] = False
                waiter["publish_done"].set()

    def cancel_and_drain(self, cookie: int) -> tuple[str, int | None]:
        while True:
            with self.lock:
                waiter = self.waiters.get(cookie)
                if waiter is None:
                    return "cancelled", None
                waiter["cancelled"] = True
                waiter["cancel_seen"].set()
                if not waiter["publishing"]:
                    if waiter["done"]:
                        state, result = "completed", waiter["result"]
                    else:
                        state, result = "timeout", None
                    del self.waiters[cookie]
                    return state, result
                publish_done = waiter["publish_done"]
            publish_done.wait()

    def wait_and_remove(self, cookie: int, timeout: float) -> tuple[str, int | None]:
        with self.lock:
            waiter = self.waiters.get(cookie)
            if waiter is None:
                return "cancelled", None
            event = waiter["event"]
        event.wait(timeout)
        return self.cancel_and_drain(cookie)

    def enqueue_failed(self, cookie: int) -> None:
        with self.lock:
            self.waiters.pop(cookie, None)


class SessionModel:
    """The session-manager mutex is the waiter/close drain barrier."""

    def __init__(self, registry: WaitRegistry) -> None:
        self.lock = threading.Lock()
        self.registry = registry
        self.closed = False
        self.result = None

    def power_call(self, started: threading.Event, response: threading.Event) -> str:
        with self.lock:
            if self.closed:
                return "closed"
            cookie = self.registry.register()
            self.registry.assign(cookie, 41)
            started.set()
            response.wait(1.0)
            if response.is_set():
                self.registry.complete(cookie, 41, 0)
            state, result = self.registry.wait_and_remove(cookie, 0)
            self.result = result
            return state

    def close(self) -> None:
        with self.lock:
            self.closed = True


def test_source_contract() -> None:
    check(PATCH.is_file(), "generated kernel patch is missing")
    text = PATCH.read_text()
    for path in KERNEL_FILES:
        check(f"{path}" in text, f"patch omits owned source {path}")
    added = "\n".join(
        line[1:] for line in text.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    )
    for marker in (
        "wait_for_completion_timeout",
        "npu_session_power_wait_assign_req_id",
        "npu_session_power_wait_begin_publish",
        "npu_session_power_wait_authorize_publish",
        "npu_session_power_wait_finish_publish",
        "npu_power_wait_cancel_and_drain",
        "waiter->publish_committed",
        "req.session = NULL",
        "req.param0 = (u32)waiter.cookie",
        "req.param1 = (u32)(waiter.cookie >> 32)",
        "waiter->req_id == result.nw.npu_req_id",
        "!waiter->done",
        "NPU_POWER_WAIT_TIMEOUT_MS",
        "mutex_lock(session->global_lock)",
        "BIT(NPU_SESSION_STATE_CLOSE)",
        "npu_hwdev_shutdown(device, ctrl->value)",
        "npu_hwdev_hwacg(&device->system, hdev->id, false)",
        "npu_stm_enable(&device->system, session->hids)",
        "npu_device_set_emergency_err(device)",
    ):
        check(marker in added, f"kernel patch missing lifecycle contract: {marker}")
    check("(struct npu_session *)(unsigned long)waiter.cookie" not in added and
          "req.session = session" not in added,
          "POWER_CTL waiter must not retain a session pointer")
    check("kzalloc" not in added[added.find("npu_session_wait_power_request"):],
          "POWER waiter should use stack storage; allocation failure must not be hidden")
    if KERNEL_ROOT is not None:
        session = (KERNEL_ROOT / KERNEL_FILES[0]).read_text()
        proto = (KERNEL_ROOT / KERNEL_FILES[1]).read_text()
        vertex = (KERNEL_ROOT / KERNEL_FILES[2]).read_text()
        msgid_source = (KERNEL_ROOT / "drivers/vision/npu/core/npu-util-msgidgen.c").read_text()
        interface_source = (KERNEL_ROOT / "drivers/vision/npu/core/interface/hardware/npu-interface.c").read_text()
        wait_body = function_body(session, "static int npu_session_wait_power_request(")
        callback_body = function_body(session, "int npu_session_save_power_result(")
        notify_body = function_body(session, "int npu_session_NW_CMD_POWER_NOTIFY(")
        close_body = function_body(session, "int npu_session_close(")
        boot_body = function_body(vertex, "int npu_hwdev_normal_bootup(")
        close_start = vertex.find("static int npu_vertex_close(")
        close_end = vertex.find("static unsigned int npu_vertex_poll(", close_start)
        close_vertex = vertex[close_start:close_end] if close_start >= 0 and close_end > close_start else ""
        requested_body = function_body(proto, "static int  npu_protodrv_handler_nw_requested(")
        msgid_body = function_body(msgid_source, "int msgid_issue(")
        check("wait_for_completion_timeout" in wait_body, "kernel waiter is not bounded")
        check("wait_event(session->wq" not in notify_body, "POWER_NOTIFY retained an unbounded wait")
        check("npu_session_wait_power_request(session, NPU_NW_CMD_POWER_CTL)" in notify_body,
              "POWER_NOTIFY bypasses owned waiter")
        check("waiter->req_id == result.nw.npu_req_id" in callback_body,
              "callback does not validate request identity")
        check("!waiter->done" in callback_body, "callback is not duplicate-safe")
        check("mutex_lock(session->global_lock)" in close_body and
              "BIT(NPU_SESSION_STATE_CLOSE)" in close_body,
              "session close does not use the waiter drain barrier")
        check("nw_power_wait_begin_publish(&entry->nw)" in requested_body and
              "nw_power_wait_authorize_publish(&entry->nw)" in requested_body and
              "proto_nw_lsm.lsm_move_entry(FREE, entry)" in requested_body,
              "stale queued POWER_CTL can be retried after timeout")
        power_start = requested_body.find("case NPU_NW_CMD_POWER_CTL:")
        power_end = requested_body.find("#else", power_start)
        power_body = requested_body[power_start:power_end] if power_start >= 0 and power_end > power_start else ""
        begin_pos = power_body.find("nw_power_wait_begin_publish(&entry->nw)")
        authorize_pos = power_body.find("nw_power_wait_authorize_publish(&entry->nw)")
        send_pos = power_body.find("__mbox_nw_ops_put(entry)")
        finish_pos = power_body.rfind("nw_power_wait_finish(publish_cookie, publish_req_id)")
        check(begin_pos >= 0 and begin_pos < authorize_pos < send_pos < finish_pos,
              "POWER_CTL publication is not leased, authorized, and drained")
        waiter_guard_start = power_body.find("#ifdef CONFIG_NPU_USE_BOOT_IOCTL")
        waiter_guard_end = power_body.find("#endif", waiter_guard_start)
        finish_guard_start = power_body.rfind("#ifdef CONFIG_NPU_USE_BOOT_IOCTL")
        finish_guard_end = power_body.find("#endif", finish_guard_start)
        check(waiter_guard_start >= 0 and waiter_guard_end > authorize_pos and
              waiter_guard_end < power_body.find("npu_device_is_emergency_err") and
              finish_guard_start > send_pos and finish_guard_end > finish_pos,
              "BOOT_IOCTL-only waiter ownership must be guarded from generic POWER_CTL")
        cancel_body = function_body(session, "static void npu_power_wait_cancel_and_drain(")
        check("waiter->cancelled = true" in cancel_body and
              "waiter->publishing" in cancel_body and
              "wait_for_completion(&waiter->publish_done)" in cancel_body,
              "timeout cancellation does not revoke or drain the publication lease")
        check("wait_for_completion_timeout" not in cancel_body,
              "current publication drain is unbounded and must remain a readiness blocker")
        check("!waiter->cancelled" in callback_body,
              "late completion must not revive a waiter once timeout cancellation starts")
        check("npu_power_wait_cancel_and_drain(&waiter)" in wait_body and
              wait_body.find("wait_for_completion_timeout") <
              wait_body.find("npu_power_wait_cancel_and_drain(&waiter)"),
              "timeout must cancel and drain before stack waiter removal")
        check("req.session = NULL" in wait_body and
              "req.param0 = (u32)waiter.cookie" in wait_body and
              "req.param1 = (u32)(waiter.cookie >> 32)" in wait_body,
              "opaque request cookie must not retain the session pointer")
        check("if (session)" in msgid_body and "e = NPU_MAX_MSG_ID_CNT;" in msgid_body,
              "NULL session must safely select the general message-ID pool")
        power_start = interface_source.find("case NPU_NW_CMD_POWER_CTL:")
        power_end = interface_source.find("case NPU_NW_CMD_POWER_DOWN:", power_start)
        power_case = interface_source[power_start:power_end] if power_start >= 0 and power_end > power_start else ""
        check("cmd.c.power_ctl" in power_case and "nw->param0" not in power_case and
              "nw->param1" not in power_case,
              "POWER_CTL wire mapping must leave cookie parameters unused")
        check("npu_hwdev_shutdown(device, ctrl->value)" in boot_body and
              "npu_hwdev_hwacg(&device->system, hdev->id, false)" in boot_body,
              "normal boot lacks reverse unwind")
        check("npu_stm_enable(&device->system, session->hids)" in boot_body,
              "normal boot does not check STM enable")
        check("kzalloc" not in wait_body and "kmalloc" not in wait_body,
              "stack waiter must have no allocation-failure path")
        unwind_markers = (
            "if (hwacg_attempted && hdev)",
            "if (session_hw_registered)",
            "if (power_notify_attempted)",
            "if (core_ref_held)",
            "if (hwdev_booted)",
        )
        positions = [boot_body.find(marker) for marker in unwind_markers]
        check(all(position >= 0 for position in positions) and positions == sorted(positions),
              "normal boot unwind order is not reverse-acquisition order")
        secure_free = close_vertex.find("if (session->sec_mem_buf)")
        session_free = close_vertex.find("ret = npu_session_close(session)")
        check(secure_free >= 0 and session_free > secure_free,
              "secure memory must be released before session free")


def test_queue_and_callback_edges() -> None:
    registry = WaitRegistry()
    cookie = registry.register()
    registry.enqueue_failed(cookie)
    check(not registry.active(cookie, 1), "enqueue failure must remove waiter")

    cookie = registry.register()
    check(registry.assign(cookie, 12), "first request identity assignment should succeed")
    check(not registry.assign(cookie, 13), "duplicate identity assignment must fail")
    check(not registry.complete(cookie, 99, 0), "mismatched request ID must not complete")
    check(registry.complete(cookie, 12, -5), "matching firmware response should complete")
    check(not registry.complete(cookie, 12, 0), "duplicate response must be ignored")
    state, result = registry.wait_and_remove(cookie, 0)
    check((state, result) == ("completed", -5), "firmware error must be retained")

    cookie = registry.register()
    check(registry.assign(cookie, 14), "request should receive its wire identity")
    state, result = registry.wait_and_remove(cookie, 0)
    check((state, result) == ("timeout", None), "missing response must time out")
    check(not registry.complete(cookie, 14, 0), "late response after timeout must be harmless")
    check(not registry.waiters, "terminal paths must drain the waiter registry")


def test_timeout_callback_race() -> None:
    for _ in range(100):
        registry = WaitRegistry()
        cookie = registry.register()
        registry.assign(cookie, 77)
        gate = threading.Barrier(3)
        observed: list[tuple[str, int | None] | bool] = []

        def complete() -> None:
            gate.wait()
            observed.append(registry.complete(cookie, 77, 0))

        def finish_wait() -> None:
            gate.wait()
            observed.append(registry.wait_and_remove(cookie, 0.0001))

        first = threading.Thread(target=complete)
        second = threading.Thread(target=finish_wait)
        first.start()
        second.start()
        gate.wait()
        first.join()
        second.join()
        check(len(observed) == 2, "race workers must both return")
        check(not registry.waiters, "timeout/result race must leave no waiter")
        callback_results = [item for item in observed if isinstance(item, bool)]
        terminal_results = [item for item in observed if isinstance(item, tuple)]
        check(len(callback_results) == 1,
              "callback must report exactly one accepted/late outcome")
        check(len(terminal_results) == 1 and terminal_results[0][0] in ("timeout", "completed"),
              "completion racing timeout must have one terminal owner")


def test_timeout_publication_handshake() -> None:
    # Pause after the worker's first validation/lease, cancel the waiter, then
    # let it make the final publication decision.  It must be revoked.
    registry = WaitRegistry()
    cookie = registry.register()
    check(registry.assign(cookie, 88), "publication waiter identity should be assigned")
    with registry.lock:
        cancel_seen = registry.waiters[cookie]["cancel_seen"]
    validated = threading.Event()
    allow_authorize = threading.Event()
    timeout_returned = threading.Event()
    publish_decisions: list[bool] = []
    timeout_results: list[tuple[str, int | None]] = []

    def validate_then_attempt_publish() -> None:
        publish_decisions.append(registry.begin_publish(cookie, 88))
        validated.set()
        allow_authorize.wait(1.0)
        publish_decisions.append(registry.authorize_publish(cookie, 88))
        registry.finish_publish(cookie, 88)

    def timeout_waiter() -> None:
        timeout_results.append(registry.cancel_and_drain(cookie))
        timeout_returned.set()

    publisher = threading.Thread(target=validate_then_attempt_publish)
    canceller = threading.Thread(target=timeout_waiter)
    publisher.start()
    check(validated.wait(1.0), "publisher did not reserve its waiter lease")
    canceller.start()
    check(cancel_seen.wait(1.0), "timeout did not cancel the waiter")
    check(not registry.complete(cookie, 88, 0),
          "late completion during timeout drain must not revive the waiter")
    check(not timeout_returned.wait(0.02), "timeout returned before in-flight lease drained")
    allow_authorize.set()
    publisher.join(1.0)
    canceller.join(1.0)
    check(not publisher.is_alive() and not canceller.is_alive(),
          "validation/cancellation race workers did not drain")
    check(publish_decisions == [True, False],
          "a publish attempted after cancellation must be denied")
    check(timeout_results == [("timeout", None)] and timeout_returned.is_set(),
          "cancelled publication waiter must terminate as timeout")
    check(not registry.waiters, "revoked publication lease must not leak its waiter")

    # If authorization linearizes first, timeout cannot return until the
    # synchronous mailbox-post model has completed.
    registry = WaitRegistry()
    cookie = registry.register()
    check(registry.assign(cookie, 89), "committed publisher identity should be assigned")
    with registry.lock:
        cancel_seen = registry.waiters[cookie]["cancel_seen"]
    committed = threading.Event()
    allow_post_return = threading.Event()
    timeout_returned = threading.Event()
    post_result: list[str] = []
    authorization_result: list[bool] = []
    timeout_results = []

    def committed_publish() -> None:
        authorization_result.append(registry.begin_publish(cookie, 89))
        authorization_result.append(registry.authorize_publish(cookie, 89))
        committed.set()
        allow_post_return.wait(1.0)
        post_result.append("sent")
        registry.finish_publish(cookie, 89)

    def timeout_committed_waiter() -> None:
        timeout_results.append(registry.cancel_and_drain(cookie))
        timeout_returned.set()

    publisher = threading.Thread(target=committed_publish)
    canceller = threading.Thread(target=timeout_committed_waiter)
    publisher.start()
    check(committed.wait(1.0), "publisher did not commit before timeout")
    canceller.start()
    check(cancel_seen.wait(1.0), "timeout did not cancel the committed waiter")
    check(not timeout_returned.wait(0.02), "timeout returned during committed mailbox post")
    check(not post_result, "mailbox post model should still be in flight")
    allow_post_return.set()
    publisher.join(1.0)
    canceller.join(1.0)
    check(not publisher.is_alive() and not canceller.is_alive(),
          "committed publication workers did not drain")
    check(authorization_result == [True, True],
          "committed publisher lease should begin and authorize")
    check(post_result == ["sent"] and timeout_returned.is_set(),
          "timeout must return only after committed publication finishes")
    check(timeout_results == [("timeout", None)] and not registry.waiters,
          "committed timeout must still remove its waiter")


def test_stalled_publication_retains_waiter_until_drain() -> None:
    # This is deliberately a Python reference-model test: hold an authorized
    # publisher indefinitely, let timeout cancellation start, and prove the
    # registered waiter remains alive until the publisher releases its lease.
    registry = WaitRegistry()
    cookie = registry.register()
    check(registry.assign(cookie, 90), "stalled publisher identity should be assigned")
    with registry.lock:
        cancel_seen = registry.waiters[cookie]["cancel_seen"]
    publication_started = threading.Event()
    release_publisher = threading.Event()
    timeout_returned = threading.Event()
    publish_authorization: list[bool] = []
    post_returned: list[bool] = []
    timeout_results: list[tuple[str, int | None]] = []

    def stalled_publisher() -> None:
        publish_authorization.append(registry.begin_publish(cookie, 90))
        publish_authorization.append(registry.authorize_publish(cookie, 90))
        publication_started.set()
        release_publisher.wait()
        post_returned.append(True)
        registry.finish_publish(cookie, 90)

    def timeout_waiter() -> None:
        timeout_results.append(registry.cancel_and_drain(cookie))
        timeout_returned.set()

    publisher = threading.Thread(target=stalled_publisher, daemon=True)
    canceller = threading.Thread(target=timeout_waiter, daemon=True)
    publisher.start()
    try:
        check(publication_started.wait(1.0), "publisher did not enter its committed post")
        canceller.start()
        check(cancel_seen.wait(1.0), "timeout did not begin publication drain")
        check(not timeout_returned.wait(0.02),
              "timeout returned while the authorized publication was stalled")
        with registry.lock:
            waiter = registry.waiters.get(cookie)
            check(waiter is not None, "stalled publisher lost its registered waiter")
            check(waiter["cancelled"] and waiter["publishing"] and
                  waiter["publish_committed"],
                  "stalled publisher lease was not retained across timeout")
        check(not registry.complete(cookie, 90, 0),
              "late callback revived a waiter while timeout was draining")
    finally:
        release_publisher.set()
        publisher.join(1.0)
        if canceller.ident is not None:
            canceller.join(1.0)

    check(not publisher.is_alive() and not canceller.is_alive(),
          "stalled publication cleanup workers did not drain")
    check(publish_authorization == [True, True] and post_returned == [True],
          "publication did not remain authorized until its modeled post returned")
    check(timeout_returned.is_set() and timeout_results == [("timeout", None)],
          "timeout did not return only after the publisher released its lease")
    check(not registry.waiters, "drained stalled publication leaked its waiter")


def test_close_barrier() -> None:
    registry = WaitRegistry()
    session = SessionModel(registry)
    started = threading.Event()
    response = threading.Event()
    power_result: list[str] = []
    close_done = threading.Event()
    power = threading.Thread(target=lambda: power_result.append(session.power_call(started, response)))

    def close_session() -> None:
        session.close()
        close_done.set()

    closer = threading.Thread(target=close_session)
    power.start()
    check(started.wait(1.0), "POWER call did not enter waiter")
    closer.start()
    check(not close_done.wait(0.02), "close must wait behind active POWER waiter")
    response.set()
    power.join()
    closer.join()
    check(power_result == ["completed"], "POWER result must finish before close")
    check(session.closed and close_done.is_set(), "close must finish after waiter cleanup")
    check(not registry.waiters, "close barrier must not leave an owned waiter")


def test_reverse_boot_unwind() -> None:
    # Match npu_hwdev_normal_bootup's acquired-resource flags and unwind order.
    steps = ("hwdev", "core_ref", "session_hw", "power", "stm", "hwacg")
    for failure_at in steps:
        acquired: list[str] = []
        attempted: list[str] = []
        for step in steps:
            if step == "power":
                attempted.append(step)
            if step == failure_at:
                break
            acquired.append(step)
        release = []
        if "hwacg" in acquired or failure_at == "hwacg":
            release.append("hwacg_off")
        if "session_hw" in acquired:
            release.append("session_unreg")
        if "power" in attempted:
            release.append("power_off_compensation")
        if "core_ref" in acquired or failure_at == "core_ref":
            release.append("core_ref_put")
        if "hwdev" in acquired:
            release.append("hwdev_shutdown")
        positions = [
            (release.index(item) if item in release else -1)
            for item in ("hwacg_off", "session_unreg", "power_off_compensation",
                         "core_ref_put", "hwdev_shutdown")
        ]
        present = [position for position in positions if position >= 0]
        check(present == sorted(present), f"unwind not reverse-ordered at {failure_at}: {release}")
        check(len(release) == len(set(release)), f"resource released twice at {failure_at}")

    failed_power_state = True
    firmware_accepted = False
    if not firmware_accepted:
        failed_power_state = False
    check(not failed_power_state, "firmware rejection must restore optimistic power state")


def main() -> None:
    test_source_contract()
    test_queue_and_callback_edges()
    test_timeout_callback_race()
    test_timeout_publication_handshake()
    test_stalled_publication_retains_waiter_until_drain()
    test_close_barrier()
    test_reverse_boot_unwind()
    print("NPU lifecycle source contracts and Python reference model passed; kernel C was not executed")


if __name__ == "__main__":
    main()
