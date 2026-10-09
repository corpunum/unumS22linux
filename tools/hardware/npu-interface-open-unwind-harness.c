/*
 * Template for test-npu-interface-open-unwind.py. Actual pinned functions are
 * injected from npu-interface.c; surrounding IRQ, mailbox, mutex, and
 * workqueue operations are deterministic host-only shims.
 */
#define _POSIX_C_SOURCE 200809L
#include <errno.h>
#include <pthread.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define IRQF_TRIGGER_HIGH 1UL
#define WQ_FREEZABLE 1U
#define WQ_HIGHPRI 2U
#define __WQ_LEGACY 4U
#define __WQ_ORDERED 8U
#define NPU_DEVICE_ERR_STATE_EMERGENCY 0
#define likely(value) (value)
#define BUG_ON(condition) do { \
	if (condition) { \
		fprintf(stderr, "unexpected BUG_ON at %s:%d\n", __FILE__, __LINE__); \
		exit(3); \
	} \
} while (0)
#define container_of(pointer, type, member) \
	((type *)((char *)(pointer) - offsetof(type, member)))
static void mock_log(const char *format, ...)
{
	(void)format;
}
#define probe_err(...) ((void)0)
#define probe_info(...) ((void)0)
#define npu_err(...) ((void)0)
#define npu_info(...) ((void)0)
#define npu_dbg(...) mock_log(__VA_ARGS__)

struct workqueue_struct {
	unsigned int id;
	int live;
	int flushes;
	int destroys;
	unsigned int flush_order;
	const char *name;
};

struct work_struct {
	int pending;
};

typedef struct {
	pthread_mutex_t native;
} spinlock_t;

typedef struct {
	pthread_mutex_t native;
	int initialized;
} mutex_t;

#define DEFINE_SPINLOCK(name) \
	spinlock_t name = { PTHREAD_MUTEX_INITIALIZER }
#define DEFINE_MUTEX(name) \
	mutex_t name = { PTHREAD_MUTEX_INITIALIZER, 1 }

static __thread int g_irq_enabled = 1;
static __thread int g_report_queue_lock_depth;
static pthread_mutex_t g_observer_lock = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t g_observer_cond = PTHREAD_COND_INITIALIZER;
static int g_close_thread_started;
static int g_close_queue_lock_attempted;
static pthread_t g_close_thread_id;
static void mock_spin_lock_irqsave(spinlock_t *lock, unsigned long *flags)
{
	*flags = (unsigned long)g_irq_enabled;
	g_irq_enabled = 0;
	pthread_mutex_lock(&g_observer_lock);
	if (g_close_thread_started &&
	    pthread_equal(pthread_self(), g_close_thread_id)) {
		g_close_queue_lock_attempted = 1;
		pthread_cond_broadcast(&g_observer_cond);
	}
	pthread_mutex_unlock(&g_observer_lock);
	if (pthread_mutex_lock(&lock->native) != 0) {
		fprintf(stderr, "spin lock shim failed\n");
		exit(3);
	}
	++g_report_queue_lock_depth;
}

static void mock_spin_unlock_irqrestore(spinlock_t *lock, unsigned long flags)
{
	--g_report_queue_lock_depth;
	g_irq_enabled = (int)flags;
	if (pthread_mutex_unlock(&lock->native) != 0) {
		fprintf(stderr, "spin unlock shim failed\n");
		exit(3);
	}
}

#define spin_lock_irqsave(lock, flags) \
	mock_spin_lock_irqsave((lock), &(flags))
#define spin_unlock_irqrestore(lock, flags) \
	mock_spin_unlock_irqrestore((lock), (flags))

static void mutex_init(mutex_t *lock)
{
	if (!lock->initialized) {
		if (pthread_mutex_init(&lock->native, NULL) != 0) {
			fprintf(stderr, "mutex init shim failed\n");
			exit(3);
		}
		lock->initialized = 1;
	}
}

static void mutex_lock(mutex_t *lock)
{
	if (pthread_mutex_lock(&lock->native) != 0) {
		fprintf(stderr, "mutex lock shim failed\n");
		exit(3);
	}
}

static void mutex_unlock(mutex_t *lock)
{
	if (pthread_mutex_unlock(&lock->native) != 0) {
		fprintf(stderr, "mutex unlock shim failed\n");
		exit(3);
	}
}

struct mailbox_ctrl {
	unsigned int wptr;
	unsigned int rptr;
};

struct mailbox_header {
	struct mailbox_ctrl f2hctrl[4];
};

struct mailbox_sfr {
	unsigned int value;
};

struct device {
	void *of_node;
};

struct platform_device {
	struct device dev;
};

struct npu_system {
	struct platform_device *pdev;
	int *irq;
	int irq_num;
	struct mailbox_header *mbox_hdr;
	int fw_cold_boot;
};

struct npu_device {
	struct npu_system system;
	unsigned long err_state;
};

struct npu_interface_state {
	volatile struct mailbox_sfr *sfr;
	volatile struct mailbox_sfr *sfr2;
	struct mailbox_header *mbox_hdr;
	void *addr;
	mutex_t lock;
};

static struct workqueue_struct *wq;
static struct work_struct work_report;
static struct npu_interface_state interface;
static struct npu_device g_device;
static struct platform_device g_platform;
static struct mailbox_header g_mailbox;
static int g_irqs[] = { 10, 11, 12 };
static void (*mailbox_isr_list[8])(void);

#define QUEUE_CAPACITY 8
#define IRQ_CAPACITY 32
static struct workqueue_struct g_queues[QUEUE_CAPACITY];
static unsigned int g_queue_count;
static unsigned int g_alloc_calls;
static unsigned int g_fail_alloc_at;
static unsigned int g_request_calls;
static unsigned int g_fail_request_at;
static unsigned int g_affinity_calls;
static unsigned int g_fail_affinity_at;
static unsigned int g_affinity_clear_calls;
static unsigned int g_free_calls;
static unsigned int g_invalid_affinity_clear;
static unsigned int g_invalid_irq_free;
static unsigned int g_queue_calls;
static unsigned int g_null_queue_calls;
static unsigned int g_queue_after_destroy;
static unsigned int g_flush_calls;
static unsigned int g_destroy_calls;
static unsigned int g_cancel_calls;
static unsigned int g_queue_without_report_lock;
static unsigned int g_flush_under_report_lock;
static unsigned int g_destroy_under_report_lock;
static unsigned int g_irq_release_under_report_lock;
static int g_block_next_flush;
static int g_flush_block_entered;
static int g_release_blocked_flush;
static int g_open_thread_result;
static unsigned int g_order_counter;
static unsigned int g_last_irq_free_order;
static unsigned int g_mailbox_init_calls;
static unsigned int g_mailbox_deinit_calls;
static int g_mailbox_init_result;
static int g_irq_live[IRQ_CAPACITY];
static int g_affinity_live[IRQ_CAPACITY];
static int g_block_next_queue;
static int g_queue_block_entered;
static int g_release_blocked_queue;

static int irq_index(int irq)
{
	if (irq < 0 || irq >= IRQ_CAPACITY) {
		fprintf(stderr, "unexpected irq id %d\n", irq);
		exit(3);
	}
	return irq;
}

static struct workqueue_struct *alloc_workqueue(const char *name,
		unsigned int flags, int max_active)
{
	struct workqueue_struct *queue;
	(void)flags;
	(void)max_active;
	++g_alloc_calls;
	if (g_fail_alloc_at == g_alloc_calls)
		return NULL;
	if (g_queue_count >= QUEUE_CAPACITY) {
		fprintf(stderr, "workqueue shim capacity exceeded\n");
		exit(3);
	}
	queue = &g_queues[g_queue_count];
	memset(queue, 0, sizeof(*queue));
	queue->id = ++g_queue_count;
	queue->live = 1;
	queue->name = name;
	return queue;
}

static int queue_work(struct workqueue_struct *queue, struct work_struct *work)
{
	pthread_mutex_lock(&g_observer_lock);
	if (!g_report_queue_lock_depth)
		++g_queue_without_report_lock;
	if (!queue) {
		++g_null_queue_calls;
		pthread_mutex_unlock(&g_observer_lock);
		return 0;
	}
	if (!queue->live) {
		++g_queue_after_destroy;
		pthread_mutex_unlock(&g_observer_lock);
		return 0;
	}
	if (g_block_next_queue) {
		g_block_next_queue = 0;
		g_queue_block_entered = 1;
		pthread_cond_broadcast(&g_observer_cond);
		while (!g_release_blocked_queue)
			pthread_cond_wait(&g_observer_cond, &g_observer_lock);
	}
	++g_queue_calls;
	work->pending = 1;
	pthread_mutex_unlock(&g_observer_lock);
	return 1;
}

static void flush_workqueue(struct workqueue_struct *queue)
{
	pthread_mutex_lock(&g_observer_lock);
	if (g_report_queue_lock_depth)
		++g_flush_under_report_lock;
	if (g_block_next_flush) {
		g_block_next_flush = 0;
		g_flush_block_entered = 1;
		pthread_cond_broadcast(&g_observer_cond);
		while (!g_release_blocked_flush)
			pthread_cond_wait(&g_observer_cond, &g_observer_lock);
	}
	if (!queue || !queue->live) {
		++g_queue_after_destroy;
	} else {
		++g_flush_calls;
		++queue->flushes;
		queue->flush_order = ++g_order_counter;
		work_report.pending = 0;
	}
	pthread_mutex_unlock(&g_observer_lock);
}

static void destroy_workqueue(struct workqueue_struct *queue)
{
	pthread_mutex_lock(&g_observer_lock);
	if (g_report_queue_lock_depth)
		++g_destroy_under_report_lock;
	if (!queue || !queue->live) {
		++g_queue_after_destroy;
	} else {
		queue->live = 0;
		++queue->destroys;
		++g_destroy_calls;
	}
	pthread_mutex_unlock(&g_observer_lock);
}

static void init_work(struct work_struct *work)
{
	work->pending = 0;
}

#define INIT_WORK(work, callback) do { \
	(void)(callback); init_work((work)); \
} while (0)

static int work_pending(struct work_struct *work)
{
	int pending;
	pthread_mutex_lock(&g_observer_lock);
	pending = work->pending;
	pthread_mutex_unlock(&g_observer_lock);
	return pending;
}

static int cancel_work_sync(struct work_struct *work)
{
	pthread_mutex_lock(&g_observer_lock);
	++g_cancel_calls;
	work->pending = 0;
	pthread_mutex_unlock(&g_observer_lock);
	return 1;
}

static int devm_request_irq(struct device *dev, int irq, void (*handler)(void),
		unsigned long flags, const char *name, void *data)
{
	int index = irq_index(irq);
	(void)dev;
	(void)handler;
	(void)flags;
	(void)name;
	(void)data;
	++g_request_calls;
	if (g_fail_request_at == g_request_calls)
		return -EIO;
	if (g_irq_live[index]) {
		fprintf(stderr, "duplicate IRQ request in shim\n");
		exit(3);
	}
	g_irq_live[index] = 1;
	return 0;
}

static int irq_set_affinity_hint(int irq, const void *mask)
{
	int index = irq_index(irq);
	if (mask) {
		++g_affinity_calls;
		if (g_fail_affinity_at == g_affinity_calls)
			return -EIO;
		if (!g_irq_live[index] || g_affinity_live[index]) {
			fprintf(stderr, "invalid affinity set in shim\n");
			exit(3);
		}
		g_affinity_live[index] = 1;
	} else {
		if (g_report_queue_lock_depth)
			++g_irq_release_under_report_lock;
		++g_affinity_clear_calls;
		if (!g_affinity_live[index])
			++g_invalid_affinity_clear;
		g_affinity_live[index] = 0;
	}
	return 0;
}

static void devm_free_irq(struct device *dev, int irq, void *data)
{
	int index = irq_index(irq);
	(void)dev;
	(void)data;
	if (g_report_queue_lock_depth)
		++g_irq_release_under_report_lock;
	++g_free_calls;
	g_last_irq_free_order = ++g_order_counter;
	if (!g_irq_live[index])
		++g_invalid_irq_free;
	g_irq_live[index] = 0;
}

static int of_property_read_u32(void *node, const char *name, unsigned int *value)
{
	(void)node;
	(void)name;
	(void)value;
	return -ENOENT;
}

static const void *cpumask_of(unsigned int cpu)
{
	(void)cpu;
	return (const void *)(uintptr_t)1;
}

static int mailbox_init(struct mailbox_header *header, struct npu_system *system)
{
	(void)header;
	(void)system;
	++g_mailbox_init_calls;
	return g_mailbox_init_result;
}

static void mailbox_deinit(struct mailbox_header *header, struct npu_system *system)
{
	(void)header;
	(void)system;
	++g_mailbox_deinit_calls;
}

static void dbg_print_interface(void) { }

static void set_bit(unsigned int bit, unsigned long *word)
{
	*word |= 1UL << bit;
}

static void __rprt_manager(struct work_struct *work)
{
	(void)work;
}

/* ACTUAL_REPORT_STATE */
/* ACTUAL_PROBE_OPEN_CLOSE */
/* ACTUAL_REPORT_MANAGER */

static void reset_case(void)
{
	unsigned int i;
	pthread_mutex_lock(&g_observer_lock);
	memset(g_queues, 0, sizeof(g_queues));
	memset(g_irq_live, 0, sizeof(g_irq_live));
	memset(g_affinity_live, 0, sizeof(g_affinity_live));
	interface.sfr = NULL;
	interface.sfr2 = NULL;
	interface.mbox_hdr = NULL;
	interface.addr = NULL;
	memset(&g_mailbox, 0, sizeof(g_mailbox));
	memset(&g_device, 0, sizeof(g_device));
	memset(&g_platform, 0, sizeof(g_platform));
	g_queue_count = 0;
	g_alloc_calls = 0;
	g_fail_alloc_at = 0;
	g_request_calls = 0;
	g_fail_request_at = 0;
	g_affinity_calls = 0;
	g_fail_affinity_at = 0;
	g_affinity_clear_calls = 0;
	g_free_calls = 0;
	g_invalid_affinity_clear = 0;
	g_invalid_irq_free = 0;
	g_queue_calls = 0;
	g_null_queue_calls = 0;
	g_queue_after_destroy = 0;
	g_flush_calls = 0;
	g_destroy_calls = 0;
	g_cancel_calls = 0;
	g_mailbox_init_calls = 0;
	g_mailbox_deinit_calls = 0;
	g_mailbox_init_result = 0;
	g_block_next_queue = 0;
	g_queue_block_entered = 0;
	g_release_blocked_queue = 0;
	g_close_thread_started = 0;
	g_close_queue_lock_attempted = 0;
	g_block_next_flush = 0;
	g_flush_block_entered = 0;
	g_release_blocked_flush = 0;
	g_open_thread_result = 0;
	g_queue_without_report_lock = 0;
	g_flush_under_report_lock = 0;
	g_destroy_under_report_lock = 0;
	g_irq_release_under_report_lock = 0;
	g_order_counter = 0;
	g_last_irq_free_order = 0;
	wq = NULL;
	work_report.pending = 0;
#ifndef EXPECT_BASELINE
	report_irq_count = 0;
	report_affinity_count = 0;
	report_interface_probed = false;
	report_interface_open = false;
#endif
	g_device.system.pdev = &g_platform;
	g_device.system.irq = g_irqs;
	g_device.system.irq_num = 3;
	g_device.system.mbox_hdr = &g_mailbox;
	g_device.system.fw_cold_boot = 1;
	for (i = 0; i < 3; ++i)
		g_irqs[i] = 10 + (int)i;
	pthread_mutex_unlock(&g_observer_lock);
}

static void expect(int condition, const char *message)
{
	if (!condition) {
		fprintf(stderr, "FAIL: %s\n", message);
		exit(2);
	}
}

static void expect_no_irq_leaks(const char *message)
{
	int i;
	for (i = 10; i < 13; ++i) {
		expect(!g_irq_live[i], message);
		expect(!g_affinity_live[i], message);
	}
}

static int call_probe(void)
{
	return npu_interface_probe(&g_platform.dev,
		(void *)(uintptr_t)0x1000, (void *)(uintptr_t)0x2000);
}

static int call_open(void)
{
	return npu_interface_open(&g_device.system);
}

static int call_close(void)
{
	return npu_interface_close(&g_device.system);
}

#ifdef EXPECT_BASELINE
static void test_baseline_probe_wq_failure_false_success(void)
{
	reset_case();
	g_fail_alloc_at = 1;
	expect(call_probe() == 0,
		"baseline probe reports success after initial WQ allocation failure");
	expect(wq == NULL, "baseline probe leaves global report queue NULL");
	expect(call_close() == 0, "baseline close after probe false success returns");
	expect(g_null_queue_calls == 1,
		"baseline close queues through NULL when initial probe WQ allocation failed");
	printf("REPRO: baseline probe WQ allocation failure returns success and close submits to NULL\n");
}

static void test_baseline_open_wq_failure_false_success(void)
{
	int ret;
	reset_case();
	expect(call_probe() == 0, "baseline probe should succeed");
	g_fail_alloc_at = 2;
	ret = call_open();
	expect(ret == 0, "baseline failed active WQ allocation is falsely reported as success");
	expect(g_irq_live[10] && g_irq_live[11] && g_irq_live[12],
		"baseline false success retains all acquired IRQs");
	expect(g_affinity_live[10] && g_affinity_live[11] && g_affinity_live[12],
		"baseline false success retains affinity hints");
	expect(wq == NULL && g_queues[0].live,
		"baseline overwrites probe queue pointer and leaves old queue live");
	expect(call_close() == 0, "baseline close after false success returns");
	expect(g_null_queue_calls == 1,
		"baseline close attempts queue_work through a NULL active queue");
	expect(g_queues[0].live,
		"baseline close cannot destroy the orphaned probe queue");
	printf("REPRO: baseline active-WQ allocation failure returns success, leaks IRQ state and orphaned queue\n");
}

static void test_baseline_partial_request_overfree(void)
{
	int ret;
	reset_case();
	expect(call_probe() == 0, "baseline probe should succeed");
	g_fail_request_at = 2;
	ret = call_open();
	expect(ret == -EIO, "baseline second request failure is returned");
	expect(g_free_calls == 3 && g_invalid_irq_free == 2,
		"baseline frees unrequested IRQ slots after partial request failure");
	expect(g_affinity_clear_calls == 3 && g_invalid_affinity_clear == 3,
		"baseline clears affinity for IRQs that never acquired hints");
	expect(g_queues[0].live,
		"baseline request error leaves probe queue installed");
	printf("REPRO: baseline partial IRQ acquisition frees and clears every slot, including unowned entries\n");
}
#else
static void test_probe_allocation_failure_and_repeated_close(void)
{
	reset_case();
	g_fail_alloc_at = 1;
	expect(call_probe() == -ENOMEM, "probe WQ allocation failure must fail probe");
	expect(!report_interface_probed && wq == NULL,
		"failed probe publishes neither readiness nor a queue");
	expect(call_close() == 0 && call_close() == 0,
		"close-before-open and repeated close are safe after failed probe");
	expect(g_null_queue_calls == 0 && g_free_calls == 0,
		"empty close never queues through NULL or frees unowned IRQs");
	printf("PASS: probe allocation failure plus close-before-open/repeated close\n");
}

static void test_active_queue_allocation_failure(void)
{
	reset_case();
	expect(call_probe() == 0, "probe succeeds");
	g_fail_alloc_at = 2;
	expect(call_open() == -ENOMEM,
		"active report WQ allocation failure returns -ENOMEM");
	expect(g_request_calls == 3 && g_free_calls == 3 &&
		g_invalid_irq_free == 0,
		"allocation failure frees exactly acquired IRQs");
	expect(g_affinity_calls == 3 && g_affinity_clear_calls == 3 &&
		g_invalid_affinity_clear == 0,
		"allocation failure clears exactly acquired affinity hints");
	expect(!g_queues[0].live && wq == NULL,
		"failed open detaches and destroys the probe queue");
	expect(g_queues[0].flush_order > g_last_irq_free_order,
		"failed open synchronizes IRQ producers before draining the published probe queue");
	expect(!interface.mbox_hdr && call_close() == 0 && call_close() == 0,
		"failed open leaves repeatable close-safe interface state");
	printf("PASS: active queue allocation failure returns -ENOMEM with exact cleanup\n");
}

static void test_partial_irq_and_affinity_failures(void)
{
	reset_case();
	expect(call_probe() == 0, "probe succeeds for request failure case");
	g_fail_request_at = 2;
	expect(call_open() == -EIO, "second request failure propagates");
	expect(g_free_calls == 1 && g_invalid_irq_free == 0,
		"request failure frees only the one successfully requested IRQ");
	expect(g_affinity_clear_calls == 0 && g_invalid_affinity_clear == 0,
		"request failure clears no unacquired affinity hint");
	expect(!g_queues[0].live, "request failure drains and destroys probe queue");
	expect(g_queues[0].flush_order > g_last_irq_free_order,
		"request failure synchronizes IRQs before draining the published queue");

	reset_case();
	expect(call_probe() == 0, "probe succeeds for affinity failure case");
	g_fail_affinity_at = 2;
	expect(call_open() == -EIO, "second affinity failure propagates");
	expect(g_free_calls == 3 && g_invalid_irq_free == 0,
		"affinity failure frees all three successfully requested IRQs");
	expect(g_affinity_clear_calls == 1 && g_invalid_affinity_clear == 0,
		"affinity failure clears only the first installed hint");
	expect_no_irq_leaks("partial setup failure left IRQ or affinity ownership live");
	expect(!g_queues[0].live, "affinity failure drains and destroys probe queue");
	expect(g_queues[0].flush_order > g_last_irq_free_order,
		"affinity failure synchronizes IRQs before draining the published queue");
	printf("PASS: partial request and affinity failures release only acquired resources\n");
}

static void test_mailbox_failure_and_successful_lifecycle(void)
{
	reset_case();
	expect(call_probe() == 0, "probe succeeds for mailbox failure case");
	g_mailbox_init_result = -ETIMEDOUT;
	expect(call_open() == -ETIMEDOUT, "mailbox initialization error propagates");
	expect(g_destroy_calls == 2 && g_flush_calls == 2,
		"mailbox failure destroys unpublished active and detached probe queues");
	expect(g_queues[1].flush_order < g_last_irq_free_order &&
		g_queues[0].flush_order > g_last_irq_free_order,
		"mailbox failure drains unpublished queue, synchronizes IRQs, then drains published queue");
	expect(g_free_calls == 3 && g_affinity_clear_calls == 3,
		"mailbox failure releases all successfully acquired resources");
	expect(!report_interface_open && wq == NULL,
		"mailbox failure publishes no open interface or queue");

	reset_case();
	expect(call_probe() == 0, "probe succeeds for successful lifecycle");
	expect(call_open() == 0, "successful open returns success");
	expect(report_interface_open && report_irq_count == 3 &&
		report_affinity_count == 3,
		"successful open commits exact ownership counts");
	expect(g_destroy_calls == 1 && !g_queues[0].live && g_queues[1].live,
		"successful open drains probe queue before active queue publication");
	expect(g_queue_calls == 1,
		"successful queue swap schedules one ring catch-up");
	expect(call_open() == -EBUSY,
		"second open is rejected without overwriting committed ownership");
	expect(g_request_calls == 3 && g_alloc_calls == 2,
		"rejected open performs no new IRQ or queue acquisitions");
	expect(call_close() == 0, "successful close returns success");
	expect(!report_interface_open && wq == NULL,
		"close detaches active queue and clears open state");
	expect(g_free_calls == 3 && g_affinity_clear_calls == 3,
		"close releases the exact committed IRQ and affinity counts");
	expect(g_destroy_calls == 2 && g_queue_after_destroy == 0,
		"close drains/destroys active queue with no post-destroy queueing");
	expect(g_queues[1].flush_order > g_last_irq_free_order,
		"close frees/synchronizes IRQ producers before draining active queue");
	expect(call_close() == 0 && g_free_calls == 3 && g_destroy_calls == 2,
		"repeated close is idempotent for resource ownership");
	printf("PASS: mailbox failure cleanup, successful publication, duplicate open, and repeated close\n");
}

static void test_close_before_open_with_pending_report(void)
{
	reset_case();
	expect(call_probe() == 0, "probe succeeds before close-before-open");
	fw_rprt_manager();
	expect(g_queue_calls == 1 && work_report.pending,
		"probe-only report producer queues actual worker path");
	expect(call_close() == 0, "close-before-open with pending report succeeds");
	expect(!interface.mbox_hdr && !wq && g_destroy_calls == 1,
		"close-before-open safely drains queue without a mailbox header");
	expect(g_queue_after_destroy == 0 && g_invalid_irq_free == 0,
		"close-before-open does not queue after destroy or free IRQs");
	expect(call_close() == 0 && g_destroy_calls == 1,
		"repeated empty close remains safe");
	printf("PASS: close-before-open guards NULL mailbox diagnostics with queued work\n");
}

static void *producer_thread(void *unused)
{
	(void)unused;
	fw_rprt_manager();
	return NULL;
}

static void *open_thread(void *unused)
{
	(void)unused;
	g_open_thread_result = call_open();
	return NULL;
}

static void *close_thread(void *unused)
{
	(void)unused;
	pthread_mutex_lock(&g_observer_lock);
	g_close_thread_id = pthread_self();
	g_close_thread_started = 1;
	pthread_cond_broadcast(&g_observer_cond);
	pthread_mutex_unlock(&g_observer_lock);
	if (call_close() != 0)
		abort();
	return NULL;
}

static void test_producer_close_interleaving(void)
{
	pthread_t producer;
	pthread_t closer;
	unsigned int queues_before_close;
	reset_case();
	expect(call_probe() == 0 && call_open() == 0,
		"setup for report-producer/close interleaving");
	pthread_mutex_lock(&g_observer_lock);
	g_block_next_queue = 1;
	pthread_mutex_unlock(&g_observer_lock);
	expect(pthread_create(&producer, NULL, producer_thread, NULL) == 0,
		"create report producer thread");
	pthread_mutex_lock(&g_observer_lock);
	while (!g_queue_block_entered)
		pthread_cond_wait(&g_observer_cond, &g_observer_lock);
	pthread_mutex_unlock(&g_observer_lock);
	expect(pthread_create(&closer, NULL, close_thread, NULL) == 0,
		"create close thread while producer is inside queue_work");
	pthread_mutex_lock(&g_observer_lock);
	while (!g_close_thread_started)
		pthread_cond_wait(&g_observer_cond, &g_observer_lock);
	while (!g_close_queue_lock_attempted)
		pthread_cond_wait(&g_observer_cond, &g_observer_lock);
	g_release_blocked_queue = 1;
	pthread_cond_broadcast(&g_observer_cond);
	pthread_mutex_unlock(&g_observer_lock);
	expect(pthread_join(producer, NULL) == 0 &&
		pthread_join(closer, NULL) == 0,
		"join controlled producer and close threads");
	queues_before_close = g_queue_calls;
	fw_rprt_manager();
	expect(g_queue_calls == queues_before_close,
		"post-close producer sees detached queue and schedules nothing");
	expect(g_queue_after_destroy == 0 && g_null_queue_calls == 0,
		"controlled overlap never submits to destroyed or NULL queue");
	expect(g_queue_without_report_lock == 0,
		"every patched queue_work call is under the publication lock");
	expect(g_flush_under_report_lock == 0 && g_destroy_under_report_lock == 0 &&
		g_irq_release_under_report_lock == 0,
		"flush, destroy, and IRQ release occur outside the publication spinlock");
	expect(g_destroy_calls == 2 && !report_interface_open && !wq,
		"close detaches and destroys active queue after synchronized producer");
	expect_no_irq_leaks("controlled close left IRQ ownership live");
	printf("PASS: producer holding queue-publication lock overlaps close without use-after-destroy\n");
}

static void test_explicit_producer_during_queue_swap(void)
{
	pthread_t opener;
	unsigned int queues_before_gap;
	reset_case();
	expect(call_probe() == 0, "probe succeeds before active queue swap");
	pthread_mutex_lock(&g_observer_lock);
	g_block_next_flush = 1;
	pthread_mutex_unlock(&g_observer_lock);
	expect(pthread_create(&opener, NULL, open_thread, NULL) == 0,
		"create opener for controlled queue-swap gap");
	pthread_mutex_lock(&g_observer_lock);
	while (!g_flush_block_entered)
		pthread_cond_wait(&g_observer_cond, &g_observer_lock);
	queues_before_gap = g_queue_calls;
	pthread_mutex_unlock(&g_observer_lock);
	fw_rprt_manager();
	expect(g_queue_calls == queues_before_gap,
		"explicit producer during detached gap does not use old queue");
	pthread_mutex_lock(&g_observer_lock);
	g_release_blocked_flush = 1;
	pthread_cond_broadcast(&g_observer_cond);
	pthread_mutex_unlock(&g_observer_lock);
	expect(pthread_join(opener, NULL) == 0 && g_open_thread_result == 0,
		"open publishes active queue after draining probe queue");
	expect(g_queue_calls == queues_before_gap + 1,
		"successful queue swap explicitly schedules one post-install catch-up");
	expect(!g_queues[0].live && g_queues[1].live &&
		g_queue_after_destroy == 0,
		"queue-swap catch-up targets only the new live queue");
	expect(g_flush_under_report_lock == 0 && g_destroy_under_report_lock == 0,
		"queue swap drains outside publication spinlock");
	expect(call_close() == 0, "close after controlled queue swap succeeds");
	printf("PASS: explicit producer in queue-swap gap is followed by one post-install catch-up\n");
}
#endif

int main(void)
{
#ifdef EXPECT_BASELINE
	test_baseline_probe_wq_failure_false_success();
	test_baseline_open_wq_failure_false_success();
	test_baseline_partial_request_overfree();
#else
	test_probe_allocation_failure_and_repeated_close();
	test_active_queue_allocation_failure();
	test_partial_irq_and_affinity_failures();
	test_mailbox_failure_and_successful_lifecycle();
	test_close_before_open_with_pending_report();
	test_producer_close_interleaving();
	test_explicit_producer_during_queue_swap();
#endif
	return 0;
}
