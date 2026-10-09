#!/usr/bin/env python3
"""Exercise exact pinned NPU publication ownership C before and after patch 9.

The C translation units contain SHA-verified target-source functions. Linux
locks, IRQ/MMIO scheduling, and LSM list storage are bounded host shims; this
does not build a kernel or establish device behavior.
"""
from __future__ import annotations

import hashlib
import importlib.util
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
HARNESS = ROOT / "tools/hardware/npu-publication-ownership-harness.c"
PATCH = ROOT / "tools/hardware/npu-publication-ownership.patch"
PATCH_SHA256 = "2e2e2de8a28c5b408bfa0661535c358070130340318f1d0eaa0efe38edc11078"
SIX_SOURCE_TREE_ENV = "S22_NPU_PUBLICATION_SIX_SOURCE_TREE"
SIX_SOURCE_COMMIT = "e5af0ba1cefc959094d03e1a136b8e33ff938b2a"

LIVE_TEST = ROOT / "tools/hardware/test-npu-publication-liveness-c.py"
WALK_TEST = ROOT / "tools/hardware/test-npu-mailbox-debug-walk-bounds.py"
FULL_TEST = ROOT / "tools/hardware/test-npu-full-lifecycle-profile.py"

MAILBOX_C = "drivers/vision/npu/core/npu-if-protodrv-mbox2.c"
PROTO_C = "drivers/vision/npu/core/npu-protodrv.c"
SESSION_C = "drivers/vision/npu/core/npu-session.c"
MSGID_C = "drivers/vision/npu/core/npu-util-msgidgen.c"
MSGID_H = "drivers/vision/npu/core/npu-util-msgidgen.h"
INTERFACE_C = "drivers/vision/npu/core/interface/hardware/npu-interface.c"
INTERFACE_H = "drivers/vision/npu/core/interface/hardware/npu-interface.h"
MAILBOX_IPC_C = "drivers/vision/npu/core/interface/hardware/mailbox_ipc.c"
MAILBOX_IPC_H = "drivers/vision/npu/core/interface/hardware/mailbox_ipc.h"
MAILBOX_MSG_H = "drivers/vision/npu/core/interface/hardware/mailbox_msg_v10.h"
NPU_COMMON_H = "drivers/vision/npu/core/include/npu-common.h"
NPU_CONFIG_H = "drivers/vision/npu/core/include/npu-config.h"
NPU_ERRNO_H = "drivers/vision/npu/core/include/npu-errno.h"
PROTO_H = "drivers/vision/npu/core/npu-protodrv.h"
HWDEV_H = "drivers/vision/npu/core/npu-hw-device.h"
AUTO_SLEEP_C = "drivers/vision/npu/core/npu-util-autosleepthr.c"
AUTO_SLEEP_H = "drivers/vision/npu/core/npu-util-autosleepthr.h"
SYSTEM_C = "drivers/vision/npu/core/npu-system.c"

ADDITIONAL_SOURCE_SHA256 = {
    INTERFACE_H: "2728e767f6efdec52f7b3a971e00781394108496a725511f26d7460e800b6b3d",
    NPU_COMMON_H: "f605f3f28b28f6ee16a82ba6abd5cffb6c8cfce6323063f7da3eb051d505b9e3",
    NPU_CONFIG_H: "0aea42ae72a0083ab57c918b5b90bad4d87f1e4c213552c14d728bf427e5073f",
    NPU_ERRNO_H: "d3397096e15124ec4f777c8b69f23f14464281f61b6610b453eb1a7fa31edbfa",
    PROTO_H: "d9c99a5a786ae323e55b92b8fd930fdeb02860ce03b572dfa28ce23842675ce9",
    MAILBOX_IPC_H: "c5dbb552dbbc8221ef1600c0d9dd21a7a071dec84b523dce63b56adf6d64cf31",
    AUTO_SLEEP_C: "08a874db08d4eaeb368239995c974394c31a3611fc1eae383a08739511499419",
    AUTO_SLEEP_H: "8d4e916eb9165f0c6af0c8cbd5a05c3b4bab76d11533b6a1f2d47688a79c0dee",
    SYSTEM_C: "96eaa6bf1511f3e6414e3e376d62592229454a2ea1687d760b7bb8e5952b1a05",
}

MAX_FILE_BYTES = 512 * 1024


def check(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    check(spec is not None and spec.loader is not None,
          f"cannot load pinned-source helper {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


LIVE = load_module(LIVE_TEST, "s22_npu_publication_ownership_liveness")
WALK = load_module(WALK_TEST, "s22_npu_publication_ownership_walk")
FULL = load_module(FULL_TEST, "s22_npu_publication_ownership_full")
RECON = FULL.RECON
STACK = RECON.STACK


def sha256(label: str, data: bytes, expected: str) -> None:
    actual = hashlib.sha256(data).hexdigest()
    check(actual == expected,
          f"SHA-256 mismatch for {label}: expected {expected}, got {actual}")


def braced_declaration(source: str, marker: str) -> str:
    start = source.find(marker)
    while start >= 0:
        brace = source.find("{", start)
        semicolon = source.find(";", start)
        if semicolon >= 0 and (brace < 0 or semicolon < brace):
            start = source.find(marker, semicolon + 1)
            continue
        check(brace >= 0, f"pinned declaration has no body: {marker}")
        depth = 0
        end = brace
        state = "code"
        while end < len(source):
            char = source[end]
            following = source[end + 1] if end + 1 < len(source) else ""
            if state == "code":
                if char == "/" and following == "*":
                    state = "block-comment"
                    end += 2
                    continue
                if char == "/" and following == "/":
                    state = "line-comment"
                    end += 2
                    continue
                if char == '"':
                    state = "string"
                elif char == "'":
                    state = "character"
                elif char == "{":
                    depth += 1
                elif char == "}":
                    depth -= 1
                    if depth == 0:
                        finish = end + 1
                        if source[finish:finish + 1] == ";":
                            finish += 1
                        return source[start:finish]
            elif state == "block-comment" and char == "*" and following == "/":
                state = "code"
                end += 2
                continue
            elif state == "line-comment" and char == "\n":
                state = "code"
            elif state in ("string", "character"):
                if char == "\\":
                    end += 2
                    continue
                if (state == "string" and char == '"') or (
                        state == "character" and char == "'"):
                    state = "code"
            end += 1
        raise RuntimeError(
            f"pinned declaration is incomplete: {marker} (depth={depth}, "
            f"state={state}, scanned={end}, source_bytes={len(source)})")
    raise RuntimeError(f"pinned declaration is missing: {marker}")


def bounded_declaration(source: str, marker: str, next_marker: str,
                        expected_tail: str) -> str:
    """Slice an exact function with mutually exclusive preprocessor braces."""
    start = source.find(marker)
    end = source.find(next_marker, start + len(marker)) if start >= 0 else -1
    check(start >= 0 and end > start and
          source.find(marker, start + len(marker)) < 0 and
          source.find(next_marker, end + len(next_marker)) < 0,
          f"pinned function boundary changed: {marker} / {next_marker}")
    declaration = source[start:end].rstrip()
    check(declaration.endswith(expected_tail),
          f"pinned function no longer ends at expected boundary: {marker}")
    return declaration


def typedef_enum(source: str, member_marker: str, end_marker: str) -> str:
    member = source.find(member_marker)
    end = source.find(end_marker, member + len(member_marker)) if member >= 0 else -1
    start = source.rfind("typedef enum {", 0, member) if member >= 0 else -1
    check(start >= 0 and end > member,
          f"pinned enum boundary changed: {member_marker} / {end_marker}")
    return source[start:end + len(end_marker)]


def load_sources() -> tuple[dict[str, bytes], str, int]:
    sources, _source_root, source_identity, total = LIVE.load_exact_sources()
    walk_sources, walk_identity = WALK.load_sources()
    for relative, data in walk_sources.items():
        check(len(data) <= MAX_FILE_BYTES,
              f"walk source exceeds cap: {relative}")
        if relative in sources:
            check(sources[relative] == data,
                  f"overlapping pinned source fixtures differ: {relative}")
        else:
            sources[relative] = data

    added = 0
    for relative, expected in ADDITIONAL_SOURCE_SHA256.items():
        data = STACK.load_extra_fixture(relative, expected)
        check(len(data) <= MAX_FILE_BYTES,
              f"additional source exceeds per-file cap: {relative}")
        if relative in sources:
            check(sources[relative] == data,
                  f"overlapping pinned source fixtures differ: {relative}")
        else:
            sources[relative] = data
            added += len(data)

    total_bytes = sum(len(data) for data in sources.values())
    aggregate_cap = len(sources) * MAX_FILE_BYTES
    check(total_bytes <= aggregate_cap,
          f"pinned source union exceeds explicit aggregate cap: {total_bytes}")
    return sources, f"{source_identity}; {walk_identity}", total_bytes


def write_fixture(root: Path, sources: dict[str, bytes]) -> None:
    for relative, data in sources.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        target.chmod(0o755)


def apply_checked(root: Path, patch: Path, label: str) -> None:
    FULL.apply_plain_patch(root, patch, label)


def snapshot(root: Path, sources: dict[str, bytes]) -> dict[str, bytes]:
    return {relative: (root / relative).read_bytes() for relative in sources}


def check_optional_six_patch_tree(paths: dict[str, bytes]) -> None:
    configured = os.environ.get(SIX_SOURCE_TREE_ENV)
    if not configured:
        print("SIX_PATCH_SOURCE_TREE not supplied; public pinned composition remains authoritative")
        return

    source_root = Path(configured).expanduser().resolve()
    head = subprocess.run(
        ["git", "-C", str(source_root), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=False, timeout=10,
    )
    check(head.returncode == 0 and head.stdout.strip() == SIX_SOURCE_COMMIT,
          f"explicit six-patch tree is not pinned at {SIX_SOURCE_COMMIT}: {source_root}")
    status = subprocess.run(
        ["git", "-C", str(source_root), "status", "--porcelain"],
        capture_output=True, text=True, check=False, timeout=10,
    )
    check(status.returncode == 0 and not status.stdout.strip(),
          f"explicit six-patch tree is not clean: {source_root}")
    for relative, expected in paths.items():
        local = source_root / relative
        check(local.is_file(), f"explicit six-patch fixture is missing: {relative}")
        check(local.read_bytes() == expected,
              f"six-patch composition differs from actual source tree: {relative}")
    print(f"PASS six-patch composition byte-matches clean {source_root}@{SIX_SOURCE_COMMIT}")


def verify_source_path(sources: dict[str, bytes]) -> None:
    interface = sources[INTERFACE_C].decode("utf-8")
    proto = sources[PROTO_C].decode("utf-8")
    mailbox = sources[MAILBOX_C].decode("utf-8")
    mailbox_ipc = sources[MAILBOX_IPC_C].decode("utf-8")
    headers = sources[INTERFACE_H].decode("utf-8")
    defconfig = sources[WALK.DEFCONFIG].decode("utf-8")

    manager = braced_declaration(interface, "int nw_req_manager(")
    set_cmd = braced_declaration(interface, "static int npu_set_cmd(")
    send = bounded_declaration(
        interface, "static int __send_interrupt(",
        "static irqreturn_t mailbox_isr0(", "return ret;\n}")
    put = braced_declaration(mailbox_ipc, "int mbx_ipc_put(")
    mailbox_put = braced_declaration(mailbox, "int npu_nw_mbox_ops_put(")
    registration = ".nw_post_request = nw_req_manager"
    check(registration in interface,
          "target hardware ops no longer register the exact nw_req_manager callback")
    check("CONFIG_NPU_USE_BOOT_IOCTL=y" in defconfig and
          "CONFIG_NPU_MAILBOX_VERSION=9" in defconfig and
          "CONFIG_NPU_COMMAND_VERSION=10" in defconfig,
          "pinned target defconfig no longer selects the BOOT_IOCTL/mailbox-v9/v10 path")
    check("#define\tMAILBOX_CLEAR_CHECK_TIMEOUT\t10000" in headers,
          "pinned hardware interrupt-wait bound changed")
    check("mbx_ipc_put(" in set_cmd and "__send_interrupt(" in set_cmd and
          set_cmd.find("mbx_ipc_put(") < set_cmd.find("__send_interrupt("),
          "npu_set_cmd no longer commits mailbox data before interrupt wait")
    check("ctrl->wptr = cmd_end_wptr;" in put and
          put.find("__copy_command_to_line(") < put.find("ctrl->wptr = cmd_end_wptr;"),
          "pinned mbx_ipc_put no longer publishes its committed wptr after ring writes")
    check(put.find("ret = -ERESOURCE;") < put.find("__copy_message_to_line(") and
          put.count("ret = -ERESOURCE;") == 2,
          "ring-full errors are no longer proven pre-commit in pinned mbx_ipc_put")
    producer_errors = re.findall(r"ret\s*=\s*(-[A-Z_]+);", put)
    check(producer_errors == ["-EPARAM", "-EALIGN", "-EINVAL",
                              "-ERESOURCE", "-ERESOURCE"] and
          "-EWOULDBLOCK" not in put and
          all(put.find(f"ret = {error};") < put.find("__copy_message_to_line(")
              for error in producer_errors),
          "pinned producer error provenance changed before wptr commit")
    check("case COMMAND_POWER_CTL:" in send and
          "while (timeout && interface.sfr->grp[1].ms & 0x1)" in send and
          "ret = -EWOULDBLOCK;" in send and "dbg_dump_mbox();" in send,
          "pinned POWER_CTL interrupt wait no longer returns EWOULDBLOCK after timeout dump")
    load_wait_start = send.find("case COMMAND_LOAD:")
    process_wait_start = send.find("case COMMAND_PROCESS:")
    power_wait = send[load_wait_start:process_wait_start]
    check(load_wait_start >= 0 and process_wait_start > load_wait_start and
          power_wait.count("ret = -EWOULDBLOCK;") == 1 and
          power_wait.find("dbg_dump_mbox();") <
          power_wait.find("ret = -EWOULDBLOCK;") <
          power_wait.find("return ret;"),
          "exact POWER_CTL interrupt-wait branch has another EWOULDBLOCK return path")
    power_start = manager.find("case NPU_NW_CMD_POWER_CTL:")
    power_end = manager.find("\n#else", power_start)
    check(power_start >= 0 and power_end > power_start,
          "pinned nw_req_manager POWER_CTL branch boundary changed")
    power_case = manager[power_start:power_end]
    check("ret = npu_set_cmd(&msg, &cmd, NPU_MBOX_REQUEST_LOW);" in manager and
          "if (ret)\n\t\tgoto nw_req_err;" in manager,
          "hardware POWER_CTL manager no longer propagates the post callback return")
    check(power_case.count("return") == 0,
          "POWER_CTL command branch unexpectedly returns before ring publication")
    check(mailbox_put.count("nw_post_request(msgid, &src->nw)") == 1,
          "npu_nw_mbox_ops_put callback site changed")
    check("-EWOULDBLOCK" in mailbox_put or "ret <= 0" in mailbox_put,
          "mailbox adapter failure path is missing")

    # Linux aliases EWOULDBLOCK to EAGAIN on this build host. Callback identity,
    # not errno spelling alone, is therefore required for the retention case.
    check(getattr(__import__("errno"), "EWOULDBLOCK") ==
          getattr(__import__("errno"), "EAGAIN"),
          "host errno alias assumption changed; review the bounded test setup")

    # The exact protocol task handles inbound responses before requested work,
    # but only in its one sequential AST do_task call; no callback runs the
    # result adapter directly.
    task = braced_declaration(proto, "static int proto_drv_do_task(")
    processing = task.find("npu_protodrv_handler_nw_processing();")
    requested = task.find("npu_protodrv_handler_nw_requested();")
    check(processing >= 0 and requested > processing and
          task.count("npu_protodrv_handler_nw_processing();") == 1 and
          task.count("npu_protodrv_handler_nw_requested();") == 1,
          "single AST pass no longer processes inbound replies before requested publication")

    # Callback source has one POWER_CTL route in this target source. No other
    # EWOULDBLOCK producer is accepted by the patch unless the exact hardware
    # manager function pointer is registered.
    check(".nw_post_request = nw_req_manager" in interface,
          "exact callback provenance has changed")
    print("PASS source audit: ring commit precedes exact POWER_CTL callback EWOULDBLOCK; ERESOURCE is pre-commit")


def verify_composed_callback_provenance(base: dict[str, bytes],
                                       composed: dict[str, bytes]) -> None:
    base_session = base[SESSION_C].decode("utf-8")
    session = composed[SESSION_C].decode("utf-8")
    proto = composed[PROTO_C].decode("utf-8")
    mailbox = composed[MAILBOX_C].decode("utf-8")
    registration_files = [
        relative for relative, data in composed.items()
        if re.search(rb"(?m)^\s*\.nw_post_request\s*=", data)
    ]
    check("int npu_session_save_result(" in base_session and
          "npu_session_save_power_result" not in base_session,
          "pinned pre-stack callback source changed unexpectedly")
    check("req.notify_func = npu_session_save_power_result;" in session,
          "composed POWER_CTL waiter no longer registers its exact result callback")
    callback = braced_declaration(
        session, "int npu_session_save_power_result(")
    check("!waiter->cancelled" in callback and
          "complete(&waiter->completion);" in callback,
          "composed late-result callback no longer ignores canceled waiters")
    check(registration_files == [INTERFACE_C] and
          composed[INTERFACE_C].decode("utf-8").count(
              ".nw_post_request = nw_req_manager") == 1 and
          mailbox.count("->nw_post_request(msgid, &src->nw)") == 1,
          "composed source has an unexpected nw_post_request callback or callsite")
    check(proto.count(
              "target->nw.notify_func == npu_session_save_power_result") == 1 and
          proto.count(
              "entry->nw.notify_func == npu_session_save_power_result") == 1,
          "composed protocol no longer brackets this exact waiter at admission and publication")
    print("PASS composed callback provenance: base save_result differs; POWER_CTL waiter is save_power_result; one nw_post_request registration/callsite")


def exact_power_ctl_manager(interface: str) -> str:
    manager = braced_declaration(interface, "int nw_req_manager(")
    start = manager.find("case NPU_NW_CMD_POWER_CTL:")
    end = manager.find("\n#else", start) if start >= 0 else -1
    tail_start = manager.find("\n\tmsg.mid = msgid;", end) if end >= 0 else -1
    check(start >= 0 and end > start and tail_start > end,
          "pinned hardware manager POWER_CTL-only extraction boundaries changed")
    case = manager[start:end]
    tail = manager[tail_start:manager.rfind("}")]
    check("cmd.c.power_ctl.magic = 0xdeadbeef;" in case and
          "msg.command = COMMAND_POWER_CTL;" in case,
          "exact POWER_CTL command constructor changed")
    return (
        "int nw_req_manager(int msgid, struct npu_nw *nw)\n{\n"
        "\tint ret = 0;\n"
        "\tstruct command cmd = {};\n"
        "\tstruct message msg = {};\n"
        "\tswitch (nw->cmd) {\n"
        + case + "\n\tdefault:\n\t\tbreak;\n\t}\n" + tail + "\n}"
    )


def exact_stucked_transition(proto: str) -> str:
    marker = "if (unlikely(is_stucked_req_nw(entry))) {"
    start = proto.find(marker)
    stucked = proto.find("proto_nw_lsm.lsm_move_entry(STUCKED, entry);", start)
    free = proto.find("proto_nw_lsm.lsm_move_entry(FREE, entry);", stucked)
    end = proto.find("}", free) if free >= 0 else -1
    check(start >= 0 and stucked > start and free > stucked and end > free,
          "pinned NW completion STUCKED/FREE transition boundaries changed")
    block = proto[start:end + 1]
    check("proto_nw_lsm.lsm_move_entry(STUCKED, entry);" in block and
          "proto_nw_lsm.lsm_move_entry(FREE, entry);" in block and
          "set_emergency_err_from_req_nw(entry);" in block,
          "pinned NW completion no longer preserves the STUCKED entry")
    return (
        "static void actual_stucked_transition(struct proto_req_nw *entry)\n{\n"
        + block + "\n}\n"
    )


def make_translation_unit(mailbox: bytes, proto: bytes, msgid: bytes,
                          interface: bytes, ipc: bytes, session: bytes,
                          msgid_header: bytes,
                          nw_header: bytes, error_header: bytes,
                          proto_header: bytes, config_header: bytes,
                          hwdev_header: bytes, message_header: bytes,
                          *, expected_patch: bool) -> str:
    template = HARNESS.read_text(encoding="utf-8")
    anchor = "/* Exact pinned production C is emitted here by the Python test. */"
    check(template.count(anchor) == 1,
          "publication ownership C harness insertion anchor changed")
    prelude, tests = template.split(anchor, 1)
    abi_anchor = "/* Pinned ABI declarations are injected before harness structs. */"
    check(prelude.count(abi_anchor) == 1,
          "publication ownership ABI insertion anchor changed")

    mailbox_source = mailbox.decode("utf-8")
    proto_source = proto.decode("utf-8")
    msgid_source = msgid.decode("utf-8")
    interface_source = interface.decode("utf-8")
    ipc_source = ipc.decode("utf-8")
    session_source = session.decode("utf-8")
    nw_header_text = nw_header.decode("utf-8")
    error_header_text = error_header.decode("utf-8")
    proto_header_text = proto_header.decode("utf-8")
    config_header_text = config_header.decode("utf-8")
    hwdev_header_text = hwdev_header.decode("utf-8")
    message_header_text = message_header.decode("utf-8")
    msgid_header_text = msgid_header.decode("utf-8")

    nw_enum = typedef_enum(nw_header_text, "NPU_NW_CMD_BASE", "} nw_cmd_e;")
    errno_enum = typedef_enum(error_header_text, "NPU_ERR_NO_ERROR",
                              "} npu_driver_err_codes;")
    proto_enum = typedef_enum(proto_header_text, "PROTO_DRV_REQ_TYPE_FRAME",
                              "} proto_drv_req_type_e;")
    message_types_start = message_header_text.find("struct cmd_load_payload {")
    message_types_end_marker = "\n};\n\n#endif /* MAILBOX_MSG_H_ */"
    message_types_end = message_header_text.find(
        message_types_end_marker, message_types_start)
    check(message_types_start >= 0 and message_types_end > message_types_start,
          "pinned mailbox-v10 command/message declarations changed boundaries")
    message_types = message_header_text[
        message_types_start:message_types_end + len("\n};")]
    msgid_pool_type = braced_declaration(msgid_header_text, "struct msgid_pool {")
    max_id_match = re.search(r"^#define\s+NPU_MAX_MSG_ID_CNT\s+(\d+)",
                             config_header_text, re.MULTILINE)
    magic_match = re.search(r"^#define\s+MSGID_POOL_MAGIC\s+(0x[0-9A-Fa-f]+)",
                            msgid_header_text, re.MULTILINE)
    message_magic_match = re.search(r"^#define\s+MESSAGE_MAGIC\s+(0x[0-9A-Fa-f]+)",
                                    message_header_text, re.MULTILINE)
    npu_hwdev_match = re.search(r"\bNPU_HWDEV_ID_NPU\s*=\s*(0x[0-9A-Fa-f]+|\d+)",
                                hwdev_header_text)
    dsp_hwdev_match = re.search(r"\bNPU_HWDEV_ID_DSP\s*=\s*(0x[0-9A-Fa-f]+|\d+)",
                                hwdev_header_text)
    check(max_id_match is not None and magic_match is not None and
          message_magic_match is not None and npu_hwdev_match is not None and
          dsp_hwdev_match is not None,
          "pinned allocator/mailbox/hardware constants changed shape")

    # Keep target enums and all wire/message-ID declarations from the pinned
    # headers, before the harness structs that embed those types.
    abi_types = "\n\n".join((nw_enum, errno_enum, proto_enum,
                               message_types, msgid_pool_type))
    prelude = prelude.replace(abi_anchor, abi_types, 1)
    for key, value in (
        ("NPU_MAX_MSG_ID_CNT_VALUE", max_id_match.group(1)),
        ("NPU_HWDEV_ID_NPU_VALUE", npu_hwdev_match.group(1)),
        ("NPU_HWDEV_ID_DSP_VALUE", dsp_hwdev_match.group(1)),
        ("MSGID_POOL_MAGIC_VALUE", magic_match.group(1)),
        ("MESSAGE_MAGIC_VALUE", message_magic_match.group(1)),
    ):
        check(prelude.count(key) == 1, f"missing unique constant slot {key}")
        prelude = prelude.replace(key, value, 1)

    power_case = LIVE.exact_power_ctl_case(proto_source)
    case_wrapper = (
        "\nstatic int run_actual_power_ctl_case(struct proto_req_nw *entry)\n{\n"
        "\tint proc_handle_cnt = 0;\n"
        "\tint compl_handle_cnt = 0;\n"
        "\tswitch (entry->nw.cmd) {\n" + power_case +
        "\n\tdefault:\n\t\tbreak;\n\t}\n"
        "\treturn proc_handle_cnt + compl_handle_cnt;\n}\n"
    )
    power_result_callback = braced_declaration(
        session_source, "int npu_session_save_power_result(")

    # Extract exact producer/interrupt/manager functions. Keep the target
    # mailbox ring and LSM-list allocation as explicit deterministic shims.
    ipc_helpers = "\n\n".join(
        braced_declaration(ipc_source, marker) for marker in (
            "static inline u32 __get_readable_size(",
            "static inline u32 __get_writable_size(",
            "static inline u32 __copy_message_to_line(",
            "static inline u32 __copy_command_to_line(",
            "int mbx_ipc_put(",
        )
    )
    msgid_functions = "\n\n".join(
        braced_declaration(msgid_source, marker) for marker in (
            "void msgid_pool_init(",
            "int msgid_issue(struct msgid_pool *handle, struct npu_session *session)",
            "int msgid_issue_save_ref(",
            "static inline int __msgid_claim(",
            "static inline void __validate_handle_msgid(",
            "void msgid_claim(",
            "void *msgid_claim_get_ref(",
        )
    )
    mailbox_functions = "\n\n".join(
        braced_declaration(mailbox_source, marker) for marker in (
            "int npu_nw_mbox_ops_get(",
            "int npu_nw_mbox_ops_put(",
        )
    )
    proto_functions = "\n\n".join((
        LIVE.exact_proto_helper_block(proto_source),
        braced_declaration(proto_source, "static int nw_mgmt_op_get_request("),
        braced_declaration(proto_source, "static int nw_mbox_ops_get("),
        braced_declaration(proto_source, "static int nw_mbox_ops_put("),
        braced_declaration(proto_source, "static int __mbox_nw_ops_put("),
        braced_declaration(proto_source, "static int  npu_protodrv_handler_nw_processing("),
        braced_declaration(proto_source, "static int is_nw_stucked_result_code("),
        braced_declaration(proto_source, "static int is_stucked_req_nw("),
    ))
    interface_functions = "\n\n".join((
        bounded_declaration(
            interface_source, "static int __send_interrupt(",
            "static irqreturn_t mailbox_isr0(", "return ret;\n}"),
        braced_declaration(interface_source, "static int npu_set_cmd("),
        exact_power_ctl_manager(interface_source),
    ))
    stucked_transition = exact_stucked_transition(proto_source)

    production = "\n\n".join((ipc_helpers, msgid_functions,
                              mailbox_functions, proto_functions,
                              interface_functions, case_wrapper,
                              stucked_transition, power_result_callback))
    production += "\n"
    production += "\n"
    unit = prelude + production + tests
    unit = unit.replace("EXPECTED_FIRST_RESULT_VALUE",
                        "1" if expected_patch else "0")
    unit = unit.replace("EXPECT_OWNERSHIP_PATCH_VALUE",
                        "1" if expected_patch else "0")
    unresolved = ("NPU_MAX_MSG_ID_CNT_VALUE", "NPU_HWDEV_ID_NPU_VALUE",
                  "NPU_HWDEV_ID_DSP_VALUE", "MSGID_POOL_MAGIC_VALUE",
                  "MESSAGE_MAGIC_VALUE", "EXPECTED_FIRST_RESULT_VALUE",
                  "EXPECT_OWNERSHIP_PATCH_VALUE")
    check(not any(name in unit for name in unresolved),
          "host C unit retains an unresolved pinned-value placeholder")
    return unit


def compile_and_run(compiler: str, directory: Path, name: str, unit: str,
                    optimization: str) -> str:
    c_path = directory / f"{name}.c"
    executable = directory / name
    c_path.write_text(unit, encoding="utf-8")
    flags = shlex.split(os.environ.get("CFLAGS", ""))
    command = [compiler, "-std=gnu11", "-Wall", "-Wextra", "-Werror",
               "-Wno-unused-parameter", "-Wno-unused-but-set-variable",
               optimization, *flags, str(c_path), "-o", str(executable)]
    built = subprocess.run(command, capture_output=True, text=True,
                           check=False, timeout=30)
    check(built.returncode == 0,
          f"{name} compile failed at {optimization}:\n{built.stderr}")
    ran = subprocess.run([str(executable)], capture_output=True, text=True,
                         check=False, timeout=10)
    check(ran.returncode == 0,
          f"{name} failed at {optimization}:\n{ran.stdout}\n{ran.stderr}")
    return ran.stdout


def verify_stucked_and_close_lifetimes(sources: dict[str, bytes]) -> None:
    proto = sources[PROTO_C].decode("utf-8")
    autosleep = sources[AUTO_SLEEP_C].decode("utf-8")
    interface = sources[INTERFACE_C].decode("utf-8")
    ipc = sources[MAILBOX_IPC_C].decode("utf-8")
    close = braced_declaration(proto, "int proto_drv_close(")
    open_body = braced_declaration(proto, "int proto_drv_open(")
    terminate = braced_declaration(autosleep, "int auto_sleep_thread_terminate(")
    init = braced_declaration(ipc, "int mailbox_init(")
    is_stucked = braced_declaration(proto, "static int is_nw_stucked_result_code(")
    timeout = braced_declaration(proto, "static int proto_drv_timedout_handling(")
    completed = braced_declaration(proto, "static int npu_protodrv_handler_nw_completed(")

    stop_pos = close.find("auto_sleep_thread_terminate(&npu_proto_drv.ast)")
    destroy_pos = close.find("proto_nw_lsm.lsm_destroy()")
    check(stop_pos >= 0 and destroy_pos > stop_pos and
          "ret = kthread_stop(thrctx->thread_ref);" in terminate,
          "protodrv close no longer joins AST before destroying NW LSM storage")
    check("msgid_pool_init(&npu_proto_drv.msgid_pool);" not in close and
          "msgid_claim(" not in close,
          "protodrv close unexpectedly gained message-ID invalidation semantics")
    check("msgid_pool_init(&npu_proto_drv.msgid_pool);" in open_body and
          open_body.find("msgid_pool_init(&npu_proto_drv.msgid_pool);") <
          open_body.find("NPU_PROTODRV_LSM_ENTRY_INIT(proto_nw_lsm"),
          "protodrv reopen no longer reinitializes msgids before NW entries")
    check("ctrl[i].wptr = ctrl[i].rptr = 0;" in init,
          "mailbox open no longer resets the response-ring logical pointers")
    check("NPU_ERR_QUEUE_TIMEOUT" in is_stucked and
          "NPU_PROTODRV_TIMEOUT_MAP[state][PROTO_DRV_REQ_TYPE_NW].err_code" in timeout and
          "is_stucked_req_nw(entry)" in completed and
          "proto_nw_lsm.lsm_move_entry(STUCKED, entry);" in completed,
          "existing NW timeout-to-STUCKED retention path changed")

    # No interface mutex serializes this publisher with mailbox result reads.
    # The single AST worker serializes its own processing/requested phases, and
    # proto_drv_close joins that worker, but we do not claim a firmware/IRQ
    # quiescence fence across close/reopen from these host checks.
    for marker in ("int nw_req_manager(", "static int npu_set_cmd(",
                   "static int __send_interrupt("):
        if marker == "static int __send_interrupt(":
            body = bounded_declaration(
                interface, marker, "static irqreturn_t mailbox_isr0(",
                "return ret;\n}")
        else:
            body = braced_declaration(interface, marker)
        check("interface.lock" not in body,
              f"unexpected interface lock appeared in exact publisher {marker}")
    print("PASS source lifetime audit: AST join precedes LSM destroy; queue timeout moves retained NW entry to STUCKED")
    print("LIMIT source shows close resets only on reopen; no independent firmware/IRQ quiescence proof")


def patch_effect(before: bytes, after: bytes) -> None:
    before_text = before.decode("utf-8")
    after_text = after.decode("utf-8")
    before_put = braced_declaration(before_text, "int npu_nw_mbox_ops_put(")
    after_put = braced_declaration(after_text, "int npu_nw_mbox_ops_put(")
    cleanup = (
        '\t\tnpu_utrace("nw_post_request failed. Reclaiming msgid(%d)\\n", '
        '&src->nw, msgid);\n\t\tmsgid_claim(pool, msgid);'
    )
    replacement = (
        "#ifdef CONFIG_NPU_USE_BOOT_IOCTL\n"
        "\t\tif (ret == -EWOULDBLOCK &&\n"
        "\t\t    src->nw.cmd == NPU_NW_CMD_POWER_CTL &&\n"
        "\t\t    src->nw.notify_func == npu_session_save_power_result &&\n"
        "\t\t    protodrv_mbox.npu_if_protodrv_mbox_ops->nw_post_request == nw_req_manager) {\n"
        "\t\t\t/* npu_set_cmd has already advanced the mailbox ring before this\n"
        "\t\t\t * interrupt wait can fail. Keep the ID for a possible late reply.\n"
        "\t\t\t */\n"
        "\t\t\tnpu_uwarn(\"POWER_CTL publication uncertain; retaining msgid(%d)\\n\",\n"
        "\t\t\t\t&src->nw, msgid);\n"
        "\t\t\t/* Keep the normal worker transition, but suppress this retry. */\n"
        "\t\t\tret = 1;\n"
        "\t\t} else\n"
        "#endif\n"
        "\t\t\tmsgid_claim(pool, msgid);"
    )
    check(before_put.count(cleanup) == 1 and
          before_put.replace(cleanup, replacement, 1) == after_put,
          "candidate patch differs from the exact callback-ownership cleanup branch")
    expected = before_text.replace(
        "extern struct npu_proto_drv *protodr;",
        "extern struct npu_proto_drv *protodr;\n"
        "#ifdef CONFIG_NPU_USE_BOOT_IOCTL\n"
        "extern int npu_session_save_power_result(struct npu_session *session,\n"
        "\t\tstruct nw_result result);\n"
        "#endif", 1)
    check(expected.replace(before_put, after_put, 1) == after_text,
          "candidate patch modified code outside npu_nw_mbox_ops_put")
    print("PASS candidate effect: exact POWER_CTL waiter+hardware callback EWOULDBLOCK retains ID and returns in-flight ownership")


def main() -> int:
    check(HARNESS.is_file(), "host C harness template is missing")
    sources, source_identity, source_bytes = load_sources()
    check_stuffed = sources.get(NPU_CONFIG_H)
    check(check_stuffed is not None, "exact target allocator configuration is missing")

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
    candidate = FULL.patch_bytes(PATCH, PATCH.name, PATCH_SHA256)
    check(len(candidate) <= MAX_FILE_BYTES,
          "candidate publication patch exceeds bounded source cap")

    print(f"SOURCE_FIXTURE {source_identity}")
    print(f"SOURCE_UNION_BYTES {source_bytes}")
    print("PATCH_ORDER " + " -> ".join(
        [*(path.name for path in patch_paths), FULL.PROFILE_PATCH.name,
         LIVE.ERROR.ERROR_PATCH.name, LIVE.MAILBOX_PATCH.name,
         WALK.PATCH_PATH.name, PATCH.name]))

    with tempfile.TemporaryDirectory(prefix="s22-npu-publication-ownership-") as temp_name:
        temp = Path(temp_name)
        source_root = temp / "composed"
        source_root.mkdir()
        write_fixture(source_root, sources)
        base_snapshot = snapshot(source_root, sources)

        RECON.apply_patch_series(source_root, patch_paths)
        apply_checked(source_root, FULL.PROFILE_PATCH, "fifth shutdown lifecycle profile")
        apply_checked(source_root, LIVE.ERROR.ERROR_PATCH,
                      "sixth shutdown error propagation")
        six_sources = snapshot(source_root, sources)
        check_optional_six_patch_tree(six_sources)

        apply_checked(source_root, LIVE.MAILBOX_PATCH,
                      "seventh missing callback message-ID reclaim")
        apply_checked(source_root, WALK.PATCH_PATH,
                      "eighth mailbox debug walk bound")
        eight_sources = snapshot(source_root, sources)
        verify_source_path(eight_sources)
        verify_composed_callback_provenance(base_snapshot, eight_sources)

        apply_checked(source_root, PATCH, "ninth publication ownership fix")
        final_sources = snapshot(source_root, sources)
        changed = {relative for relative in sources
                   if eight_sources[relative] != final_sources[relative]}
        check(changed == {MAILBOX_C},
              f"ninth patch changed unexpected source paths: {sorted(changed)!r}")
        patch_effect(eight_sources[MAILBOX_C], final_sources[MAILBOX_C])

        verify_stucked_and_close_lifetimes(eight_sources)
        check(sources[INTERFACE_C] == base_snapshot[INTERFACE_C] and
              sources[MAILBOX_IPC_C] == base_snapshot[MAILBOX_IPC_C],
              "unexpected baseline source mutation in interface/ring fixture")

        baseline_unit = make_translation_unit(
            eight_sources[MAILBOX_C], eight_sources[PROTO_C],
            eight_sources[MSGID_C], eight_sources[INTERFACE_C],
            eight_sources[MAILBOX_IPC_C], eight_sources[SESSION_C], sources[MSGID_H],
            sources[NPU_COMMON_H], sources[NPU_ERRNO_H], sources[PROTO_H],
            sources[NPU_CONFIG_H], sources[HWDEV_H], sources[MAILBOX_MSG_H],
            expected_patch=False)
        patched_unit = make_translation_unit(
            final_sources[MAILBOX_C], final_sources[PROTO_C],
            final_sources[MSGID_C], final_sources[INTERFACE_C],
            final_sources[MAILBOX_IPC_C], eight_sources[SESSION_C], sources[MSGID_H],
            sources[NPU_COMMON_H], sources[NPU_ERRNO_H], sources[PROTO_H],
            sources[NPU_CONFIG_H], sources[HWDEV_H], sources[MAILBOX_MSG_H],
            expected_patch=True)

        for optimization in ("-O0", "-O2"):
            baseline = compile_and_run(compiler, temp,
                                       f"baseline-{optimization[2:]}",
                                       baseline_unit, optimization)
            print(baseline, end="")
            patched = compile_and_run(compiler, temp,
                                      f"patched-{optimization[2:]}",
                                      patched_unit, optimization)
            print(patched, end="")
            for marker in (
                "BASELINE REPRO actual C: late POWER_CTL response is attributed to a different entry",
                "BASELINE REPRO actual C: pre-transition response loses its recycled message-ID owner",
            ):
                check(marker in baseline,
                      f"baseline extracted C omitted negative marker {marker!r} at {optimization}")
            for marker in (
                "PASS patched actual C: ambiguous commit retains original owner through late response",
                "PASS actual C: response ready at ring commit is consumed after serialized PROCESSING transition",
                "PASS actual C: -ERESOURCE remains a pre-commit retry and recycles only its ID",
                "PASS actual adapter: errno-only failure from non-hardware callback is reclaimed, not retained",
                "PASS actual source C: canceled waiter ignores late callback; STUCKED entry survives reply",
                "PASS actual C: unresolved ownership is capped at 64 IDs; exhaustion refuses another ring commit",
            ):
                check(marker in patched,
                      f"patched extracted C omitted pass marker {marker!r} at {optimization}")

        print("PASS exact eight-patch baseline plus candidate patch; only mbox2 adapter changed")
        print("LIMIT actual extracted target C and pinned source ordering with host MMIO/LSM shims; no kernel build or device acceptance")

    return 0


if __name__ == "__main__":
    sys.exit(main())
