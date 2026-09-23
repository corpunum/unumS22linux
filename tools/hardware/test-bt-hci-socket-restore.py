#!/usr/bin/env python3
"""Validate the HCI socket lifecycle repair against pinned kernel source.

This is source-application and contract regression coverage, not a kernel build
or runtime socket test. --base-source applies the candidate patch in a private
temporary tree before checking the resulting source.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def body(text: str, marker: str) -> str:
    start = text.index(marker)
    brace = text.index("{", start)
    depth = 0
    for pos in range(brace, len(text)):
        if text[pos] == "{":
            depth += 1
        elif text[pos] == "}":
            depth -= 1
            if depth == 0:
                return text[start : pos + 1]
    raise AssertionError(f"unterminated body: {marker}")


def validate_contract(text: str) -> None:
    assert "#if 0" not in text, "disabled HCI socket lifecycle code remains"
    assert "static DEFINE_IDA(sock_cookie_ida);" in text
    assert "static bool hci_sock_gen_cookie(" in text
    assert "static void hci_sock_free_cookie(" in text
    assert "send_monitor_control_replay(" in text

    create = body(text, "static int hci_sock_create(")
    for expected in (
        "sock->type != SOCK_RAW",
        "return -ESOCKTNOSUPPORT",
        "sock->ops = &hci_sock_ops",
        "bt_sock_alloc(net, sock, &hci_sk_proto, protocol, GFP_ATOMIC,",
        "if (!sk)",
        "return -ENOMEM",
        "sock->state = SS_UNCONNECTED",
        "sk->sk_destruct = hci_sock_destruct",
        "bt_sock_link(&hci_sk_list, sk)",
        "return 0",
    ):
        assert expected in create, expected
    assert create.index("if (!sk)") < create.index("return -ENOMEM")
    assert create.index("return -ENOMEM") < create.index("bt_sock_link(&hci_sk_list, sk)")
    assert create.index("bt_sock_link(&hci_sk_list, sk)") < create.index("return 0")

    release = body(text, "static int hci_sock_release(")
    for expected in (
        "hci_sock_free_cookie(sk)", "bt_sock_unlink(&hci_sk_list, sk)",
        "sock_orphan(sk)", "sock_put(sk)",
    ):
        assert expected in release, expected

    ioctl = body(text, "static int hci_sock_ioctl(")
    assert "if (!capable(CAP_NET_ADMIN))" in ioctl
    bound_ioctl = body(text, "static int hci_sock_bound_ioctl(")
    assert bound_ioctl.count("capable(CAP_NET_ADMIN)") >= 3

    bind = body(text, "static int hci_sock_bind(")
    for expected in (
        "case HCI_CHANNEL_USER:",
        "case HCI_CHANNEL_MONITOR:",
        "case HCI_CHANNEL_LOGGING:",
        "if (!capable(CAP_NET_ADMIN))",
        "if (!capable(CAP_NET_RAW))",
    ):
        assert expected in bind, expected

    ops = body(text, "static const struct proto_ops hci_sock_ops")
    for name in (
        "hci_sock_release", "hci_sock_bind", "hci_sock_getname",
        "hci_sock_sendmsg", "hci_sock_recvmsg", "hci_sock_ioctl",
        "hci_sock_setsockopt", "hci_sock_getsockopt",
    ):
        assert name in ops, name

    for name in ("hci_sock_ioctl", "hci_sock_bind", "hci_sock_getname"):
        fn = body(text, "static int " + name + "(")
        assert fn.count("return 0;") == 0, f"stale stub return in {name}"


def negative_contract_checks(text: str) -> None:
    release = body(text, "static int hci_sock_release(")
    broken_release = text.replace(
        release, release.replace("hci_sock_free_cookie(sk);", "/* removed cleanup */", 1), 1
    )
    try:
        validate_contract(broken_release)
    except AssertionError:
        pass
    else:
        raise AssertionError("contract accepted missing release/cookie cleanup")

    ioctl = body(text, "static int hci_sock_ioctl(")
    broken_ioctl = text.replace(
        ioctl, ioctl.replace("!capable(CAP_NET_ADMIN)", "!capable(CAP_NET_RAW)"), 1
    )
    try:
        validate_contract(broken_ioctl)
    except AssertionError:
        pass
    else:
        raise AssertionError("contract accepted weakened ioctl privilege check")

    create = body(text, "static int hci_sock_create(")
    broken_create = text.replace(
        create, create.replace("sock->type != SOCK_RAW", "sock->type == SOCK_RAW", 1), 1
    )
    try:
        validate_contract(broken_create)
    except AssertionError:
        pass
    else:
        raise AssertionError("contract accepted incorrect socket-type gate")


def patched_candidate(base: Path, patch: Path) -> str:
    with tempfile.TemporaryDirectory(prefix="s22-hci-candidate-") as temp:
        temp_root = Path(temp)
        target = temp_root / "net/bluetooth/hci_sock.c"
        target.parent.mkdir(parents=True)
        shutil.copyfile(base, target)
        subprocess.run(["git", "apply", "--check", str(patch)],
                       cwd=temp_root, check=True)
        subprocess.run(["git", "apply", str(patch)], cwd=temp_root, check=True)
        return target.read_text()


def main() -> None:
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--source", type=Path,
                         help="already-patched hci_sock.c source")
    source.add_argument("--base-source", type=Path,
                        help="pinned original hci_sock.c to patch in a temporary tree")
    parser.add_argument("--patch", type=Path,
                        help="candidate patch; required with --base-source")
    args = parser.parse_args()
    if args.source:
        if not args.source.is_file():
            parser.error(f"source file does not exist: {args.source}")
        if args.patch:
            parser.error("--patch is only valid with --base-source")
        text = args.source.read_text()
        label = str(args.source)
    else:
        if not args.base_source.is_file():
            parser.error(f"base source file does not exist: {args.base_source}")
        if not args.patch:
            parser.error("--patch is required with --base-source")
        if not args.patch.is_file():
            parser.error(f"patch file does not exist: {args.patch}")
        text = patched_candidate(args.base_source, args.patch.resolve())
        label = f"{args.patch} applied to {args.base_source} in a temporary tree"

    validate_contract(text)
    negative_contract_checks(text)
    print(f"HCI lifecycle contract and negative checks passed: {label}")
    print("Scope: host source validation only; no kernel build or runtime socket test")


if __name__ == "__main__":
    main()
