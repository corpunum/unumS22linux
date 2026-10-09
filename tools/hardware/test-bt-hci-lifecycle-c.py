#!/usr/bin/env python3
"""Execute pinned HCI lifecycle C with explicitly modelled host dependencies.

No Bluetooth socket, controller, device, SSH, or kernel API is used. The test
reproduces the disabled baseline create, then executes repaired lifecycle C.
The actual IDA free guard is executed too: sentinel releases are harmless in
this pinned implementation. Kernel locks/XArray/SKB/core registration are shims.
"""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
BASE = "4e5c5ad7d950e4de0688b5663965f2075654b2ad"
SOURCE = "net/bluetooth/hci_sock.c"
SOURCE_SHA = "672c58217ea7becf77f5eed0d77d6f5c28a516b8843dfe23243900774b972316"
RESTORE_SHA = "b342b92d262767c3dd6ed71f1037e7c63eca253efde23f285144baf2b2c44d08"
SOURCE_CAP = 128 * 1024
URL = f"https://raw.githubusercontent.com/LineageOS/android_kernel_samsung_s5e9925/{BASE}/{SOURCE}"
IDA_C = "lib/idr.c"
IDA_SHA = "a5f5799202aebdcfad0d266c241a18da3f43a04cf04bdd63f37c7536087cc4b8"
IDA_H = "include/linux/idr.h"
IDA_H_SHA = "485060f431354181579bbf8b9e92f276834192f94c604c69a3688dbf12c2bad9"


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise RuntimeError(reason)


def pinned_source(tree: Path | None, path: str = SOURCE, expected: str = SOURCE_SHA) -> bytes:
    url = f"https://raw.githubusercontent.com/LineageOS/android_kernel_samsung_s5e9925/{BASE}/{path}"
    if tree is not None:
        require(tree.is_dir(), "configured kernel tree unavailable; no network fallback")
        result = subprocess.run(["git", "show", f"{BASE}:{path}"], cwd=tree,
                                capture_output=True, timeout=10, check=True)
        data = result.stdout
    else:
        with urllib.request.urlopen(url, timeout=5) as response:
            require(response.geturl() == url, "unexpected source redirect")
            length = response.headers.get("Content-Length")
            if length is not None:
                require(bool(re.fullmatch(r"[0-9]+", length)), "invalid source length")
                require(int(length) <= SOURCE_CAP, "declared source exceeds cap")
            data = response.read(SOURCE_CAP + 1)
    require(len(data) <= SOURCE_CAP, "actual source exceeds cap")
    require(hashlib.sha256(data).hexdigest() == expected, f"pinned source mismatch: {path}")
    return data


def body(source: str, marker: str) -> str:
    start = source.index(marker)
    opening = source.index("{", start)
    depth = 0
    for end in range(opening, len(source)):
        if source[end] == "{":
            depth += 1
        elif source[end] == "}":
            depth -= 1
            if depth == 0:
                return source[start:end + 1]
    raise RuntimeError(f"unterminated pinned function: {marker}")


SHIMS = r'''
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#define BT_DBG(...) do {} while (0)
#define BT_ERR(...) do {} while (0)
#define BT_INFO(...) do {} while (0)
#define __init
#define BUILD_BUG_ON(x) _Static_assert(!(x), "ABI shim size")
#define GFP_ATOMIC 0
#define GFP_KERNEL 0
#define SOCK_RAW 3
#define SS_UNCONNECTED 1
#define BTPROTO_HCI 1
#define HCI_CHANNEL_RAW 0
#define HCI_CHANNEL_USER 1
#define HCI_CHANNEL_MONITOR 2
#define HCI_CHANNEL_CONTROL 3
#define HCI_USER_CHANNEL 4
#define HCI_SOCK_TRUSTED 1
struct net { int unused; };
struct sockaddr { char bytes[16]; };
struct sockaddr_hci { char bytes[8]; };
struct sock { void (*sk_destruct)(struct sock *); int sk_receive_queue, sk_write_queue; };
struct hci_dev { int promisc, closed, flag_cleared, index_added, refs; };
struct hci_pinfo { struct sock sk; struct hci_dev *hdev; unsigned short channel; uint32_t cookie; char comm[16]; };
struct socket { struct sock *sk; int type, state; void *ops; };
struct sk_buff { int unused; };
struct proto { int unused; };
static struct proto hci_sk_proto;
static int hci_sock_ops, hci_sock_family_ops, hci_sk_list, monitor_promisc;
struct ida { int xa; };
static struct ida sock_cookie_ida;
struct xa_state { unsigned int index; };
#define XA_STATE(name, xa, idx) struct xa_state name={.index=(idx)}
#define IDA_CHUNK_SIZE 128
#define IDA_BITMAP_LONGS (IDA_CHUNK_SIZE / sizeof(long))
#define IDA_BITMAP_BITS (IDA_BITMAP_LONGS * sizeof(long) * 8)
struct ida_bitmap { unsigned long bitmap[IDA_BITMAP_LONGS]; };
#define BITS_PER_XA_VALUE (sizeof(unsigned long)*8-1)
#define XA_FREE_MARK 1
static struct ida_bitmap *allocated_bitmap;
static int ida_warns, xarray_locks, xarray_frees;
void ida_free(struct ida *ida,unsigned int id);
#define xas_lock_irqsave(xas, flags) do { (flags)=0; xarray_locks++; } while(0)
#define xas_unlock_irqrestore(xas, flags) do { (void)(flags); xarray_locks--; } while(0)
static void *xas_load(struct xa_state *xas) { return allocated_bitmap; }
static bool xa_is_value(void *bitmap) { return false; }
static unsigned long xa_to_value(void *bitmap) { return 0; }
static void *xa_mk_value(unsigned long value) { return NULL; }
static void xas_store(struct xa_state *xas,void *bitmap) { allocated_bitmap=bitmap; xarray_frees++; }
static void xas_set_mark(struct xa_state *xas,int mark) {}
static bool test_bit(unsigned int bit,unsigned long *bitmap) { return (bitmap[bit/(sizeof(long)*8)]>>(bit%(sizeof(long)*8)))&1UL; }
static void __clear_bit(unsigned int bit,unsigned long *bitmap) { bitmap[bit/(sizeof(long)*8)] &= ~(1UL<<(bit%(sizeof(long)*8))); }
static bool bitmap_empty(unsigned long *bitmap,unsigned long bits) { for(unsigned int i=0;i<IDA_BITMAP_LONGS;i++) if(bitmap[i]) return false; return true; }
#define kfree(pointer) free(pointer)
#define WARN(...) do { ida_warns++; } while(0)
static struct net init_net;
#define hci_pi(sk) ((struct hci_pinfo *)(sk))
static int current;
static int allocations, links, unlinks, socket_puts, purges, locks, orphans, packets, task_comm;
static int alloc_fail, cookie_result, cookie_allocs, cookie_frees, invalid_cookie_frees;
static int proto_rc, socket_rc, proc_rc, registrations, socket_regs, proc_regs;
static int unregisters, socket_unregs, proc_unregs, order[16], norder;
static void check(bool ok, const char *why) { if (!ok) { fprintf(stderr,"FAIL %s\n",why); exit(1); } }
static void reset(void) {
 allocations=links=unlinks=socket_puts=purges=locks=orphans=packets=task_comm=0;
 alloc_fail=cookie_allocs=cookie_frees=invalid_cookie_frees=0; cookie_result=17;
 if(allocated_bitmap) free(allocated_bitmap);
 allocated_bitmap=NULL;
 ida_warns=xarray_locks=xarray_frees=0;
 proto_rc=socket_rc=proc_rc=registrations=socket_regs=proc_regs=0;
 unregisters=socket_unregs=proc_unregs=norder=0; monitor_promisc=0;
}
static void event(int e) { check(norder<16,"event capacity"); order[norder++]=e; }
static struct sock *bt_sock_alloc(struct net *n,struct socket *s,struct proto *p,int protocol,int gfp,int kern) {
 allocations++; if (alloc_fail) return NULL;
 struct hci_pinfo *pi=calloc(1,sizeof(*pi)); check(pi!=NULL,"host allocation"); s->sk=&pi->sk; return s->sk;
}
static void bt_sock_link(int *list,struct sock *s) { links++; }
static void bt_sock_unlink(int *list,struct sock *s) { unlinks++; }
static void lock_sock(struct sock *s) { locks++; }
static void release_sock(struct sock *s) { check(locks==1,"release lock balance"); locks--; }
static void sock_orphan(struct sock *s) { orphans++; }
static void skb_queue_purge(int *q) { purges++; }
static void sock_put(struct sock *s) { socket_puts++; if(s->sk_destruct) s->sk_destruct(s); free(s); }
static void atomic_dec(int *n) { (*n)--; }
static struct sk_buff *create_monitor_ctrl_close(struct sock *s) { static struct sk_buff skb; return &skb; }
static void hci_send_to_channel(int channel,struct sk_buff *skb,int trust,void *p) { packets++; }
static void kfree_skb(struct sk_buff *skb) {}
static void hci_dev_do_close(struct hci_dev *d) { d->closed++; }
static void hci_dev_clear_flag(struct hci_dev *d,int flag) { d->flag_cleared++; }
static void mgmt_index_added(struct hci_dev *d) { d->index_added++; }
static void hci_dev_put(struct hci_dev *d) { d->refs--; }
static int ida_simple_get(struct ida *ida,int start,int end,int gfp) {
 cookie_allocs++;
 if(cookie_result>=0) { allocated_bitmap=calloc(1,sizeof(*allocated_bitmap)); check(allocated_bitmap!=NULL,"host IDA bitmap"); allocated_bitmap->bitmap[0]|=1UL<<17; }
 return cookie_result;
}
static void ida_simple_remove(struct ida *ida,unsigned int id) { cookie_frees++; if(id==UINT32_MAX) invalid_cookie_frees++; ida_free(ida,id); }
static void get_task_comm(char *dest,int task) { task_comm++; memcpy(dest,"host",5); }
static int proto_register(struct proto *p,int flag) { registrations++; event(1); return proto_rc; }
static void proto_unregister(struct proto *p) { unregisters++; event(6); }
static int bt_sock_register(int protocol,int *family) { socket_regs++; event(2); return socket_rc; }
static void bt_sock_unregister(int protocol) { socket_unregs++; event(5); }
static int bt_procfs_init(struct net *n,const char *name,int *list,void *p) { proc_regs++; event(3); return proc_rc; }
static void bt_procfs_cleanup(struct net *n,const char *name) { proc_unregs++; event(4); }
'''

CASES = r'''
int main(void) {
 check(sizeof(int)==4,"32-bit signed cookie model");
 reset(); struct socket stub={.type=SOCK_RAW};
 check(baseline_create(&init_net,&stub,1,0)==0 && stub.sk==NULL && allocations==0,"baseline false create success reproduced");
 check(baseline_init()==0 && registrations==1 && socket_regs==1 && proc_regs==1,"baseline init registration preserved; create still stubbed");
 reset(); struct hci_pinfo pi={0}; cookie_result=-ENOMEM;
 check(hci_sock_gen_cookie(&pi.sk) && pi.cookie==UINT32_MAX,"cookie allocation failure sentinel");
 hci_sock_free_cookie(&pi.sk);
 check(cookie_frees==1 && invalid_cookie_frees==1 && ida_warns==0 && xarray_locks==0,"actual IDA rejects sentinel before XArray; not a cookie bug");
 reset(); memset(&pi,0,sizeof(pi)); cookie_result=-ENOSPC;
 check(hci_sock_gen_cookie(&pi.sk),"failed cookie attempt recorded");
 hci_sock_free_cookie(&pi.sk);
 check(cookie_frees==1 && ida_warns==0 && xarray_frees==0 && pi.cookie==UINT32_MAX,"failed allocation causes no actual free");
 check(!hci_sock_gen_cookie(&pi.sk) && cookie_allocs==1,"failed cookie not automatically retried");
 reset(); memset(&pi,0,sizeof(pi));
 check(hci_sock_gen_cookie(&pi.sk) && pi.cookie==17,"valid cookie allocation");
 check(!hci_sock_gen_cookie(&pi.sk) && cookie_allocs==1,"valid cookie not duplicated");
 hci_sock_free_cookie(&pi.sk); hci_sock_free_cookie(&pi.sk);
 check(cookie_frees==2 && invalid_cookie_frees==1 && ida_warns==0 && xarray_frees==1 && allocated_bitmap==NULL && pi.cookie==UINT32_MAX,"valid ID freed once; repeated sentinel call harmless");
 reset(); memset(&pi,0,sizeof(pi)); hci_sock_free_cookie(&pi.sk);
 check(cookie_frees==0,"zero cookie not freed");
 reset(); struct socket bad={.type=2};
 check(hci_sock_create(&init_net,&bad,1,0)==-ESOCKTNOSUPPORT && allocations==0 && bad.ops==NULL,"unsupported type denied before allocation");
 reset(); struct socket nomem={.type=SOCK_RAW}; alloc_fail=1;
 check(hci_sock_create(&init_net,&nomem,1,0)==-ENOMEM && links==0 && nomem.sk==NULL,"allocation failure no publication");
 reset(); struct socket empty={0}; check(hci_sock_release(&empty)==0 && locks==0 && socket_puts==0,"empty release");
 for(int i=0;i<200;i++) {
  reset(); struct socket s={.type=SOCK_RAW};
  check(hci_sock_create(&init_net,&s,1,0)==0 && s.sk && links==1 && s.state==SS_UNCONNECTED && s.ops==&hci_sock_ops,"create initialized published socket");
  check(hci_sock_release(&s)==0 && unlinks==1 && socket_puts==1 && purges==2 && locks==0 && orphans==1,"raw create close cleanup");
 }
 reset(); struct socket user={.type=SOCK_RAW}; struct hci_dev dev={.promisc=1,.refs=1};
 check(hci_sock_create(&init_net,&user,1,0)==0,"user create"); hci_pi(user.sk)->hdev=&dev; hci_pi(user.sk)->channel=HCI_CHANNEL_USER;
 check(hci_sock_gen_cookie(user.sk),"user cookie"); hci_sock_release(&user);
 check(dev.promisc==0 && dev.refs==0 && dev.closed==1 && dev.flag_cleared==1 && dev.index_added==1 && cookie_frees==1,"user release dependencies");
 reset(); struct socket monitor={.type=SOCK_RAW}; check(hci_sock_create(&init_net,&monitor,1,0)==0,"monitor create");
 hci_pi(monitor.sk)->channel=HCI_CHANNEL_MONITOR; monitor_promisc=1; hci_sock_release(&monitor);
 check(monitor_promisc==0 && cookie_frees==0 && packets==0 && socket_puts==1,"monitor cleanup");
 reset(); proto_rc=-ENOMEM;
 check(hci_sock_init()==-ENOMEM && registrations==1 && socket_regs==0 && unregisters==0,"proto failure");
 reset(); socket_rc=-EIO;
 check(hci_sock_init()==-EIO && unregisters==1 && socket_unregs==0 && proc_regs==0 && norder==3 && order[2]==6,"socket registration failure unwind");
 reset(); proc_rc=-EIO;
 check(hci_sock_init()==-EIO && unregisters==1 && socket_unregs==1 && norder==5 && order[3]==5 && order[4]==6,"proc failure reverse unwind");
 reset(); check(hci_sock_init()==0 && norder==3 && order[0]==1 && order[1]==2 && order[2]==3,"registration success");
 hci_sock_cleanup(); check(norder==6 && order[3]==4 && order[4]==5 && order[5]==6,"cleanup reverse ordering");
 puts("HCI_C_PASS: baseline create failure, actual IDA sentinel guard, lifecycle and error unwind; host shims only");
 return 0;
}
'''


def compile_fixture(base: str, repaired: str, idr: str) -> str:
    markers = (
        "static void hci_sock_destruct(", "static bool hci_sock_gen_cookie(",
        "static void hci_sock_free_cookie(", "static int hci_sock_release(",
        "static int hci_sock_create(", "int __init hci_sock_init(",
        "void hci_sock_cleanup(",
    )
    functions = [body(repaired, marker) for marker in markers]
    originals = [
        body(base, "static int hci_sock_create(").replace("hci_sock_create", "baseline_create", 1),
        body(base, "int __init hci_sock_init(").replace("hci_sock_init", "baseline_init", 1),
    ]
    # The kernel's socket release invokes the same actual destructor through
    # the modelled last reference; no source body is rewritten beyond names.
    return SHIMS + "\n" + body(idr, "void ida_free(") + "\n" + "\n\n".join(functions + originals) + "\n" + CASES


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-tree", type=Path)
    args = parser.parse_args()
    source = pinned_source(args.source_tree)
    idr = pinned_source(args.source_tree, IDA_C, IDA_SHA).decode()
    idr_header = pinned_source(args.source_tree, IDA_H, IDA_H_SHA).decode()
    require("#define ida_simple_remove(ida, id)\tida_free(ida, id)" in idr_header,
            "pinned IDA removal macro changed")
    require(bool(re.search(r"^#define IDA_CHUNK_SIZE\s+128\b", idr_header, re.M)),
            "pinned IDA bitmap geometry changed")
    restore = ROOT / "tools/hardware/bt-hci-socket-restore.patch"
    require(hashlib.sha256(restore.read_bytes()).hexdigest() == RESTORE_SHA, "restoration patch changed")
    cc = shutil.which("cc")
    require(cc is not None, "host C compiler unavailable")
    with tempfile.TemporaryDirectory(prefix="s22-hci-c-") as tmp:
        directory = Path(tmp)
        target = directory / SOURCE
        target.parent.mkdir(parents=True)
        target.write_bytes(source)
        subprocess.run(["git", "apply", "--check", str(restore)], cwd=directory, check=True, timeout=10)
        subprocess.run(["git", "apply", str(restore)], cwd=directory, check=True, timeout=10)
        code = compile_fixture(source.decode(), target.read_text(), idr)
        fixture = directory / "hci.c"
        fixture.write_text(code)
        for opt in (0, 2):
            binary = directory / f"hci-O{opt}"
            subprocess.run([cc, "-std=c11", "-Wall", "-Wextra", "-Werror",
                            "-Wno-unused-parameter", "-Wno-unused-variable",
                            f"-O{opt}", str(fixture), "-o", str(binary)], check=True, timeout=30)
            subprocess.run([str(binary)], check=True, timeout=10)
        print(f"Pinned HCI source bytes={len(source)} sha256={SOURCE_SHA}; both C optimization levels passed")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except urllib.error.HTTPError as error:
        if error.code == 429 or error.code >= 500:
            print("SOURCE_FIXTURE_UNAVAILABLE: HTTP environmental failure")
            raise SystemExit(77)
        raise
    except (urllib.error.URLError, TimeoutError):
        print("SOURCE_FIXTURE_UNAVAILABLE: network environmental failure")
        raise SystemExit(77)
