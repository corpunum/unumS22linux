/*
 * Host execution harness for the exact added npu_hwdev_bootup() text extracted
 * from npu-session-lifecycle-fix.patch by test-npu-session-lifecycle.py.
 *
 * The reference-count helpers below mirror npu_hw_ref_get()/put() from the
 * pinned npu-hw-device.h, including increment-before-first-callback behavior.
 * This exercises C control flow and rollback ordering with injected callback
 * failures; it does not execute Linux or prove device/runtime behavior.
 */
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef uint32_t __u32;

struct npu_device;
struct npu_hw_device;
typedef int (*ref_callback)(struct npu_device *, struct npu_hw_device *);

enum ref_kind { REF_BOOT = 1, REF_INIT = 2 };
enum event_op { EV_FIRST = 1, EV_PUT = 2, EV_FINAL = 3 };

struct npu_hw_refcount {
	int refcount;
	struct npu_hw_device *hdev;
	ref_callback first;
	ref_callback final;
	enum ref_kind kind;
};

struct npu_hw_device {
	const char *name;
	__u32 id;
	struct npu_hw_refcount boot_cnt;
	struct npu_hw_refcount init_cnt;
};

struct npu_device {
	bool emergency;
};

struct event {
	enum event_op op;
	enum ref_kind kind;
	__u32 id;
};

static struct npu_hw_device devices[3];
static struct npu_hw_device *g_hwdev_list[3];
static int g_hwdev_num = 3;
static struct npu_device device;
static struct event events[64];
static size_t event_count;
static __u32 fail_first_id;
static enum ref_kind fail_first_kind;
static int fail_first_error;
static __u32 fail_final_id;
static enum ref_kind fail_final_kind;
static int fail_final_error;

static void record(enum event_op op, enum ref_kind kind, __u32 id)
{
	if (event_count >= sizeof(events) / sizeof(events[0])) {
		fprintf(stderr, "event log overflow\n");
		exit(1);
	}
	events[event_count++] = (struct event){ op, kind, id };
}

static int first_callback(struct npu_hw_device *hdev, enum ref_kind kind)
{
	record(EV_FIRST, kind, hdev->id);
	if (hdev->id == fail_first_id && kind == fail_first_kind)
		return fail_first_error;
	return 0;
}

static int final_callback(struct npu_hw_device *hdev, enum ref_kind kind)
{
	record(EV_FINAL, kind, hdev->id);
	if (hdev->id == fail_final_id && kind == fail_final_kind)
		return fail_final_error;
	return 0;
}

static int boot_first(struct npu_device *unused, struct npu_hw_device *hdev)
{
	(void)unused;
	return first_callback(hdev, REF_BOOT);
}

static int boot_final(struct npu_device *unused, struct npu_hw_device *hdev)
{
	(void)unused;
	return final_callback(hdev, REF_BOOT);
}

static int init_first(struct npu_device *unused, struct npu_hw_device *hdev)
{
	(void)unused;
	return first_callback(hdev, REF_INIT);
}

static int init_final(struct npu_device *unused, struct npu_hw_device *hdev)
{
	(void)unused;
	return final_callback(hdev, REF_INIT);
}

/* Pinned npu-hw-device.h semantics: increment/decrement precedes callback. */
static int npu_hw_ref_get(struct npu_device *dev, struct npu_hw_refcount *ref)
{
	if (!ref->first)
		return 0;
	return (++ref->refcount == 1) ? ref->first(dev, ref->hdev) : 0;
}

static int npu_hw_ref_put(struct npu_device *dev, struct npu_hw_refcount *ref)
{
	record(EV_PUT, ref->kind, ref->hdev->id);
	if (!ref->final)
		return 0;
	return (--ref->refcount == 0) ? ref->final(dev, ref->hdev) : 0;
}

static void npu_device_set_emergency_err(struct npu_device *dev)
{
	dev->emergency = true;
}

#define BUG_ON(condition) do { if (condition) abort(); } while (0)
#define npu_info(...) ((void)0)
#define npu_err(...) ((void)0)

/* Exact production function is emitted by the Python test. */
#include "npu-hwdev-bootup-extracted.inc"

static void fail(const char *message)
{
	fprintf(stderr, "npu-ownership-harness: %s\n", message);
	exit(1);
}

static void expect(bool condition, const char *message)
{
	if (!condition)
		fail(message);
}

static void reset_case(void)
{
	memset(devices, 0, sizeof(devices));
	memset(events, 0, sizeof(events));
	event_count = 0;
	memset(&device, 0, sizeof(device));
	fail_first_id = 0;
	fail_first_kind = 0;
	fail_first_error = 0;
	fail_final_id = 0;
	fail_final_kind = 0;
	fail_final_error = 0;
	for (int i = 0; i < 3; i++) {
		devices[i].id = (__u32)(1U << i);
		devices[i].name = i == 0 ? "one" : i == 1 ? "two" : "four";
		devices[i].boot_cnt.hdev = &devices[i];
		devices[i].boot_cnt.first = boot_first;
		devices[i].boot_cnt.final = boot_final;
		devices[i].boot_cnt.kind = REF_BOOT;
		devices[i].init_cnt.hdev = &devices[i];
		devices[i].init_cnt.first = init_first;
		devices[i].init_cnt.final = init_final;
		devices[i].init_cnt.kind = REF_INIT;
		g_hwdev_list[i] = &devices[i];
	}
}

static void expect_event(size_t at, enum event_op op, enum ref_kind kind,
		 __u32 id)
{
	if (at >= event_count || events[at].op != op || events[at].kind != kind ||
	    events[at].id != id) {
		fprintf(stderr, "event %zu mismatch (count=%zu)\n", at, event_count);
		fail("unexpected ref callback or unwind order");
	}
}

static void test_boot_callback_failure_balances_only_owned_refs(void)
{
	int ret;

	reset_case();
	/* A different owner already holds id 1; this call only owns its increment. */
	devices[0].boot_cnt.refcount = 1;
	fail_first_id = 4;
	fail_first_kind = REF_BOOT;
	fail_first_error = -5;
	ret = npu_hwdev_bootup(&device, 1U | 4U);
	expect(ret == -5, "boot callback failure was not returned unchanged");
	expect(events[0].op == EV_FIRST && events[0].kind == REF_BOOT &&
	       events[0].id == 4, "failed boot first callback was not recorded");
	expect_event(1, EV_PUT, REF_BOOT, 4);
	expect_event(2, EV_FINAL, REF_BOOT, 4);
	expect_event(3, EV_PUT, REF_BOOT, 1);
	expect(event_count == 4, "boot error unwind had an unexpected extra release");
	expect(devices[0].boot_cnt.refcount == 1,
	       "unwind dropped another owner's pre-existing boot reference");
	expect(devices[2].boot_cnt.refcount == 0 &&
	       devices[1].boot_cnt.refcount == 0,
	       "boot failure leaked a selected reference or touched unselected device");
	expect(!device.emergency, "successful rollback spuriously marked emergency");
	puts("PASS actual C bootup: failed first callback balances just this call's refs");
}

static void test_init_failure_unwinds_partial_init_then_boot_in_reverse(void)
{
	int ret;

	reset_case();
	fail_first_id = 4;
	fail_first_kind = REF_INIT;
	fail_first_error = -28;
	fail_final_id = 4;
	fail_final_kind = REF_INIT;
	fail_final_error = -5;
	ret = npu_hwdev_bootup(&device, 1U | 4U);
	expect(ret == -28, "cleanup error replaced the original init failure");
	expect_event(0, EV_FIRST, REF_BOOT, 1);
	expect_event(1, EV_FIRST, REF_BOOT, 4);
	expect_event(2, EV_FIRST, REF_INIT, 1);
	expect_event(3, EV_FIRST, REF_INIT, 4);
	expect_event(4, EV_PUT, REF_INIT, 4);
	expect_event(5, EV_FINAL, REF_INIT, 4);
	expect_event(6, EV_PUT, REF_INIT, 1);
	expect_event(7, EV_FINAL, REF_INIT, 1);
	expect_event(8, EV_PUT, REF_BOOT, 4);
	expect_event(9, EV_FINAL, REF_BOOT, 4);
	expect_event(10, EV_PUT, REF_BOOT, 1);
	expect_event(11, EV_FINAL, REF_BOOT, 1);
	expect(event_count == 12,
	       "partial init unwind did not attempt every acquired reference exactly once");
	expect(devices[0].boot_cnt.refcount == 0 &&
	       devices[2].boot_cnt.refcount == 0 &&
	       devices[0].init_cnt.refcount == 0 &&
	       devices[2].init_cnt.refcount == 0,
	       "init failure left a selected boot/init count unbalanced");
	expect(devices[1].boot_cnt.refcount == 0 &&
	       devices[1].init_cnt.refcount == 0,
	       "init failure touched an unselected hdev");
	expect(device.emergency,
	       "failed final callback was not reported through emergency state");
	puts("PASS actual C bootup: partial-init rollback is reverse ordered and preserves primary error");
}

static void test_success_honors_exact_hids(void)
{
	int ret;

	reset_case();
	ret = npu_hwdev_bootup(&device, 1U | 4U);
	expect(ret == 0, "successful selected boot/init unexpectedly failed");
	expect(event_count == 4, "success called an unexpected callback");
	expect_event(0, EV_FIRST, REF_BOOT, 1);
	expect_event(1, EV_FIRST, REF_BOOT, 4);
	expect_event(2, EV_FIRST, REF_INIT, 1);
	expect_event(3, EV_FIRST, REF_INIT, 4);
	expect(devices[0].boot_cnt.refcount == 1 &&
	       devices[0].init_cnt.refcount == 1 &&
	       devices[2].boot_cnt.refcount == 1 &&
	       devices[2].init_cnt.refcount == 1,
	       "successful selected references are not retained exactly once");
	expect(devices[1].boot_cnt.refcount == 0 &&
	       devices[1].init_cnt.refcount == 0,
	       "success touched an hdev outside the requested id mask");
	puts("PASS actual C bootup: success acquires only exact requested hids");
}

int main(void)
{
	test_boot_callback_failure_balances_only_owned_refs();
	test_init_failure_unwinds_partial_init_then_boot_in_reverse();
	test_success_honors_exact_hids();
	puts("LIMIT: extracted C and callback shims do not validate Linux runtime or hardware behavior");
	return 0;
}
