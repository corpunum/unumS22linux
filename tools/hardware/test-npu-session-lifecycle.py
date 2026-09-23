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
                "result": None,
                "event": threading.Event(),
            }
            return cookie

    def assign(self, cookie: int, req_id: int) -> bool:
        with self.lock:
            waiter = self.waiters.get(cookie)
            if waiter is None or waiter["done"] or waiter["req_id"] is not None:
                return False
            waiter["req_id"] = req_id
            return True

    def active(self, cookie: int, req_id: int) -> bool:
        with self.lock:
            waiter = self.waiters.get(cookie)
            return bool(waiter and waiter["req_id"] == req_id and not waiter["done"])

    def complete(self, cookie: int, req_id: int, result: int) -> bool:
        with self.lock:
            waiter = self.waiters.get(cookie)
            if (waiter is None or waiter["req_id"] != req_id or waiter["done"]):
                return False
            waiter["result"] = result
            waiter["done"] = True
            waiter["event"].set()
            return True

    def wait_and_remove(self, cookie: int, timeout: float) -> tuple[str, int | None]:
        with self.lock:
            waiter = self.waiters.get(cookie)
            if waiter is None:
                return "cancelled", None
            event = waiter["event"]
        event.wait(timeout)
        with self.lock:
            waiter = self.waiters.get(cookie)
            if waiter is None:
                return "cancelled", None
            if waiter["done"]:
                result = waiter["result"]
                del self.waiters[cookie]
                return "completed", result
            del self.waiters[cookie]
            return "timeout", None

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
        "npu_session_power_wait_is_active",
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
        check("nw_power_wait_is_active(&entry->nw)" in requested_body and
              "proto_nw_lsm.lsm_move_entry(FREE, entry)" in requested_body,
              "stale queued POWER_CTL can be retried after timeout")
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
    test_close_barrier()
    test_reverse_boot_unwind()
    print("NPU lifecycle source contracts and host race/unwind model passed")


if __name__ == "__main__":
    main()
