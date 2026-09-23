#!/usr/bin/env python3
"""Check restored Bluetooth HCI socket lifecycle and capability contracts."""
from __future__ import annotations

import argparse
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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True,
                        help="hci_sock.c from the kernel checkout after applying the candidate patch")
    args = parser.parse_args()
    if not args.source.is_file():
        parser.error(f"source file does not exist: {args.source}")
    text = args.source.read_text()

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
    for expected in ("hci_sock_free_cookie(sk)", "bt_sock_unlink(&hci_sk_list, sk)",
                     "sock_orphan(sk)", "sock_put(sk)"):
        assert expected in release, expected

    ioctl = body(text, "static int hci_sock_ioctl(")
    assert "capable(CAP_NET_ADMIN)" in ioctl
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
    for name in ("hci_sock_release", "hci_sock_bind", "hci_sock_getname",
                 "hci_sock_sendmsg", "hci_sock_recvmsg", "hci_sock_ioctl",
                 "hci_sock_setsockopt", "hci_sock_getsockopt"):
        assert name in ops, name

    for name in ("hci_sock_ioctl", "hci_sock_bind", "hci_sock_getname"):
        fn = body(text, "static int " + name + "(")
        assert fn.count("return 0;") == 0, f"stale stub return in {name}"

    print(f"HCI socket lifecycle/security contract passed: {args.source}")


if __name__ == "__main__":
    main()
