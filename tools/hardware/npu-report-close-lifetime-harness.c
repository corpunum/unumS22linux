/*
 * Template for test-npu-report-close-lifetime.py. Pinned production C bodies
 * are injected at the markers. pthread locks, mailbox memory and IRQ/workqueue
 * calls below are controlled host shims, not Linux-kernel execution.
 */
#include <errno.h>
#include <limits.h>
#include <pthread.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdatomic.h>

typedef uint8_t u8;
typedef uint32_t u32;
typedef pthread_mutex_t spinlock_t;
typedef pthread_mutex_t mutex_t;
typedef struct { atomic_int refs; } refcount_t;
typedef struct { pthread_mutex_t lock; pthread_cond_t cond; } wait_queue_head_t;

enum refcount_saturation_type {
	REFCOUNT_ADD_NOT_ZERO_OVF,
	REFCOUNT_ADD_OVF,
	REFCOUNT_ADD_UAF,
	REFCOUNT_SUB_UAF,
	REFCOUNT_DEC_LEAK,
};

#define REFCOUNT_INIT(value) { ATOMIC_VAR_INIT(value) }
#define REFCOUNT_MAX INT_MAX
#define REFCOUNT_SATURATED (INT_MIN / 2)
#define __must_check
#define atomic_fetch_sub_release(value, target) \
	atomic_fetch_sub_explicit((target), (value), memory_order_release)
#define smp_acquire__after_ctrl_dep() \
	atomic_thread_fence(memory_order_acquire)
#define EXPORT_SYMBOL(symbol)
#define REFCOUNT_WARN(message) mock_refcount_warn(message)
#define DEFINE_SPINLOCK(name) pthread_mutex_t name = PTHREAD_MUTEX_INITIALIZER
#define DEFINE_MUTEX(name) pthread_mutex_t name = PTHREAD_MUTEX_INITIALIZER
#define DECLARE_WAIT_QUEUE_HEAD(name) \
	wait_queue_head_t name = { PTHREAD_MUTEX_INITIALIZER, PTHREAD_COND_INITIALIZER }
static int mock_escape_saturated_owner_wait(void);
#define wait_event(queue, condition) do { \
	pthread_mutex_lock(&(queue).lock); \
	if (!(condition)) mock_note_owner_wait(); \
	while (!(condition)) { \
		if (mock_escape_saturated_owner_wait()) break; \
		pthread_cond_wait(&(queue).cond, &(queue).lock); \
	} \
	pthread_mutex_unlock(&(queue).lock); \
} while (0)
static void mock_wake_up_all(wait_queue_head_t *queue);
#define wake_up_all(queue) mock_wake_up_all((queue))
#define spin_lock_irqsave(lock, flags) \
	mock_spin_lock_irqsave((lock), &(flags))
#define spin_unlock_irqrestore(lock, flags) \
	mock_spin_unlock_irqrestore((lock), (flags))
#define mutex_lock(lock) mock_mutex_lock((lock))
#define mutex_unlock(lock) mock_mutex_unlock((lock))
#define mutex_init(lock) mock_mutex_init((lock))
#define container_of(pointer, type, member) \
	((type *)((char *)(pointer) - offsetof(type, member)))
#define likely(value) (value)
#define unlikely(value) (value)
#define TRUE 1
#define FALSE 0
#define BUFSIZE 128
#define LENGTHOFEVIDENCE 128
#define GFP_ATOMIC 0
#define NPU_LOG_ERR 3
#define CONFIG_NPU_MAILBOX_VERSION 9
#define IS_ENABLED(option) 0
#define WQ_FREEZABLE 1
#define WQ_HIGHPRI 2
#define __WQ_LEGACY 4
#define __WQ_ORDERED 8
#define IRQF_TRIGGER_HIGH 16
#define NPU_DEVICE_ERR_STATE_EMERGENCY 2
#define MAILBOX_F2HCTRL_REPORT 2
#define MAILBOX_F2HCTRL_RESPONSE 1
#define MAILBOX_F2HCTRL_NRESPONSE 3
#define MAILBOX_H2FCTRL_LPRIORITY 0
#define MAILBOX_H2FCTRL_MPRIORITY 1
#define MAILBOX_H2FCTRL_HPRIORITY 2
#define ECTRL_LOW 0
#define ECTRL_MEDIUM 1
#define ECTRL_HIGH 2
#define ECTRL_ACK 3
#define ECTRL_NACK 4
#define ECTRL_REPORT 5
#define LINE_TO_SGMT(length, position) ((position) & ((length) - 1))
#define NPU_MBOX_BASE(pointer) (pointer)
#define NPU_MAILBOX_GET_CTRL(pointer, offset) \
	((void)(pointer), (void)(offset), g_mailbox_ring)
#define memcpy_fromio(destination, source, length) \
	memcpy((destination), (source), (length))
#define BUG_ON(condition) do { \
	if (condition) { \
		fprintf(stderr, "unexpected extracted BUG_ON at %s:%d\n", __FILE__, __LINE__); \
	exit(3); \
	} \
} while (0)
static void mock_suppressed_log(const char *format, ...)
{
	(void)format;
}

#define npu_err(...) do { mock_suppressed_log(__VA_ARGS__); ++g_suppressed_logs; } while (0)
#define npu_warn(...) do { mock_suppressed_log(__VA_ARGS__); ++g_suppressed_logs; } while (0)
#define npu_info(...) do { mock_suppressed_log(__VA_ARGS__); ++g_suppressed_logs; } while (0)
#define npu_dbg(...) do { mock_suppressed_log(__VA_ARGS__); ++g_suppressed_logs; } while (0)
#define npu_dump(...) do { mock_suppressed_log(__VA_ARGS__); ++g_suppressed_logs; } while (0)
#define probe_err(...) do { mock_suppressed_log(__VA_ARGS__); ++g_suppressed_logs; } while (0)
#define probe_info(...) do { mock_suppressed_log(__VA_ARGS__); ++g_suppressed_logs; } while (0)
#define pr_err(...) do { mock_suppressed_log(__VA_ARGS__); ++g_suppressed_logs; } while (0)

struct mailbox_ctrl {
	u32 wptr;
	u32 rptr;
	u32 sgmt_len;
	u32 sgmt_ofs;
};

struct mailbox_hdr {
	struct mailbox_ctrl f2hctrl[8];
	struct mailbox_ctrl h2fctrl[8];
};

struct mailbox_sfr { u32 unused; };
struct work_struct { int pending; };
struct workqueue_struct { int live; int id; };
struct device { void *of_node; };
struct platform_device { struct device dev; };

struct npu_system {
	struct platform_device *pdev;
	int *irq;
	int irq_num;
	struct mailbox_hdr *mbox_hdr;
};

struct npu_device {
	struct npu_system system;
	unsigned long err_state;
};

struct npu_interface_state {
	volatile struct mailbox_sfr *sfr;
	volatile struct mailbox_sfr *sfr2;
	struct mailbox_hdr *mbox_hdr;
	void *addr;
	mutex_t lock;
};

struct report_store {
	char *st_buf;
	size_t st_size;
	size_t wr_pos;
	size_t rp_pos;
	size_t last_dump_line_cnt;
};

struct npu_log_ops {
	void (*fw_rprt_gather)(void);
	int (*npu_check_unposted_mbox)(int channel);
};

struct npu_log_state { struct npu_log_ops *log_ops; };

static struct workqueue_struct *wq;
static struct work_struct work_report;
static struct npu_interface_state interface;
static struct npu_device g_device;
static struct platform_device g_platform;
static struct mailbox_hdr g_mailbox;
static int g_irqs[] = { 10, 11, 12 };
static char g_mailbox_ring[256];
static char g_report_storage[4096];
static struct report_store fw_report;
static struct npu_log_ops g_log_ops;
static struct npu_log_state npu_log = { &g_log_ops };
static void (*mailbox_isr_list[16])(void);
static mutex_t *g_interface_lock;
static pthread_mutex_t *g_report_owner_lock;
static pthread_mutex_t *g_report_wq_lock;
static int (*g_interface_header_is_null)(void);
static _Thread_local int g_in_interrupt;
static _Thread_local int g_fw_report_lock_depth;
static _Thread_local int g_owner_spin_depth;
static _Thread_local int g_wq_spin_depth;

enum gate_kind { GATE_NONE, GATE_INTERFACE_MUTEX, GATE_MEMDUMP };
static pthread_mutex_t g_observer_lock = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t g_observer_cond = PTHREAD_COND_INITIALIZER;
static enum gate_kind g_gate_kind;
static int g_gate_enabled;
static int g_gate_entered;
static int g_gate_release;
static int g_close_started;
static int g_close_done;
static int g_close_result;
static int g_owner_wait_entered;
static int g_owner_wait_stalled;
static int g_direct_stale_escape;
static int g_profiler_stale_escape;
static int g_mailbox_init_result;
static int g_mailbox_init_calls;
static int g_mailbox_deinit_calls;
static int g_mailbox_deinit_before_reader_exit;
static int g_request_calls;
static int g_free_calls;
static int g_affinity_calls;
static int g_clear_affinity_calls;
static int g_queue_calls;
static int g_flush_calls;
static int g_destroy_calls;
static int g_kzalloc_calls;
static int g_interface_mutex_init_calls;
static int g_interface_mutex_reinit_attempts;
static int g_interface_mutex_acquires;
static int g_reader_returned;
static int g_reader_result;
static int g_suppressed_logs;
static int g_store_calls;
static int g_refcount_warning_calls;
static atomic_int g_order_errors;
static atomic_int g_wait_lock_errors;
static spinlock_t fw_report_lock = PTHREAD_MUTEX_INITIALIZER;

static void mock_refcount_warn(const char *message)
{
	(void)message;
	++g_refcount_warning_calls;
}

static int atomic_ref_read(const refcount_t *refs)
{
	return atomic_load_explicit(&refs->refs, memory_order_acquire);
}

static void mock_wake_up_all(wait_queue_head_t *queue)
{
	pthread_mutex_lock(&queue->lock);
	pthread_cond_broadcast(&queue->cond);
	pthread_mutex_unlock(&queue->lock);
}

static unsigned int refcount_read(const refcount_t *refs)
{
	return (unsigned int)atomic_ref_read(refs);
}

static void refcount_set(refcount_t *refs, int value)
{
	atomic_store_explicit(&refs->refs, value, memory_order_release);
}

static bool refcount_inc_not_zero(refcount_t *refs)
{
	int old = atomic_ref_read(refs);
	for (;;) {
		int next;
		if (!old)
			return false;
		if (old < 0 || old >= REFCOUNT_MAX)
			next = REFCOUNT_SATURATED;
		else
			next = old + 1;
		if (atomic_compare_exchange_weak_explicit(&refs->refs, &old, next,
			memory_order_acq_rel, memory_order_acquire))
			return true;
	}
}
/* PINNED_REFCOUNT_HELPERS */

static void mock_note_owner_wait(void)
{
	pthread_mutex_lock(&g_observer_lock);
	if (g_owner_spin_depth || g_wq_spin_depth || g_fw_report_lock_depth ||
		(g_interface_lock && pthread_mutex_trylock(g_interface_lock) != 0))
		atomic_fetch_add(&g_wait_lock_errors, 1);
	else if (g_interface_lock)
		pthread_mutex_unlock(g_interface_lock);
	g_owner_wait_entered = 1;
	pthread_cond_broadcast(&g_observer_cond);
	pthread_mutex_unlock(&g_observer_lock);
}

static void mock_spin_lock_irqsave(spinlock_t *lock, unsigned long *flags)
{
	*flags = 0;
	if (lock == g_report_owner_lock && g_fw_report_lock_depth)
		atomic_fetch_add(&g_order_errors, 1);
	pthread_mutex_lock(lock);
	if (lock == &fw_report_lock)
		++g_fw_report_lock_depth;
	if (lock == g_report_owner_lock)
		++g_owner_spin_depth;
	if (lock == g_report_wq_lock)
		++g_wq_spin_depth;
}

static void mock_spin_unlock_irqrestore(spinlock_t *lock, unsigned long flags)
{
	(void)flags;
	if (lock == &fw_report_lock && g_fw_report_lock_depth)
		--g_fw_report_lock_depth;
	if (lock == g_report_owner_lock && g_owner_spin_depth)
		--g_owner_spin_depth;
	if (lock == g_report_wq_lock && g_wq_spin_depth)
		--g_wq_spin_depth;
	pthread_mutex_unlock(lock);
}

static void mock_mutex_init(mutex_t *lock)
{
	if (lock == g_interface_lock && g_interface_mutex_init_calls)
		++g_interface_mutex_reinit_attempts;
	if (pthread_mutex_init(lock, NULL) != 0) {
		fprintf(stderr, "host mutex initialization failed\n");
		exit(3);
	}
	if (lock == g_interface_lock)
		++g_interface_mutex_init_calls;
}

static void mock_gate(enum gate_kind kind)
{
	pthread_mutex_lock(&g_observer_lock);
	if (g_gate_enabled && g_gate_kind == kind) {
		g_gate_enabled = 0;
		g_gate_entered = 1;
		pthread_cond_broadcast(&g_observer_cond);
		while (!g_gate_release)
			pthread_cond_wait(&g_observer_cond, &g_observer_lock);
	}
	pthread_mutex_unlock(&g_observer_lock);
}

static void mock_mutex_lock(mutex_t *lock)
{
	if (lock == g_interface_lock) {
		++g_interface_mutex_acquires;
		mock_gate(GATE_INTERFACE_MUTEX);
#ifdef EXPECT_BASELINE
		if (g_interface_header_is_null && g_interface_header_is_null()) {
			pthread_mutex_lock(&g_observer_lock);
			g_direct_stale_escape = 1;
			pthread_mutex_unlock(&g_observer_lock);
			pthread_exit((void *)(uintptr_t)1);
		}
#endif
	}
	pthread_mutex_lock(lock);
}

static void mock_mutex_unlock(mutex_t *lock)
{
	pthread_mutex_unlock(lock);
}

static void mock_irq_handler(void) { }

static struct workqueue_struct g_queues[16];
static unsigned int g_queue_count;

static struct workqueue_struct *alloc_workqueue(const char *name,
		unsigned int flags, int max_active)
{
	struct workqueue_struct *queue;
	(void)name;
	(void)flags;
	(void)max_active;
	if (g_queue_count >= sizeof(g_queues) / sizeof(g_queues[0]))
		return NULL;
	queue = &g_queues[g_queue_count];
	memset(queue, 0, sizeof(*queue));
	queue->live = 1;
	queue->id = (int)++g_queue_count;
	return queue;
}

static int queue_work(struct workqueue_struct *queue, struct work_struct *work)
{
	if (!queue || !queue->live)
		return 0;
	++g_queue_calls;
	work->pending = 1;
	return 1;
}

static int work_pending(struct work_struct *work) { return work->pending; }

static int cancel_work_sync(struct work_struct *work)
{
	work->pending = 0;
	return 1;
}

static void flush_workqueue(struct workqueue_struct *queue)
{
	if (queue && queue->live)
		++g_flush_calls;
	if (queue)
		work_report.pending = 0;
}

static void destroy_workqueue(struct workqueue_struct *queue)
{
	if (queue && queue->live) {
		queue->live = 0;
		++g_destroy_calls;
	}
}

static int devm_request_irq(struct device *dev, int irq,
		void (*handler)(void), unsigned long flags, const char *name, void *data)
{
	(void)dev;
	(void)irq;
	(void)handler;
	(void)flags;
	(void)name;
	(void)data;
	++g_request_calls;
	return 0;
}

static void devm_free_irq(struct device *dev, int irq, void *data)
{
	(void)dev;
	(void)irq;
	(void)data;
	++g_free_calls;
}

static int irq_set_affinity_hint(int irq, const void *mask)
{
	(void)irq;
	if (mask)
		++g_affinity_calls;
	else
		++g_clear_affinity_calls;
	return 0;
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

static int mailbox_init(volatile struct mailbox_hdr *header, struct npu_system *system)
{
	(void)header;
	(void)system;
	++g_mailbox_init_calls;
	return g_mailbox_init_result;
}

static void mailbox_deinit(volatile struct mailbox_hdr *header, struct npu_system *system)
{
	(void)header;
	(void)system;
	pthread_mutex_lock(&g_observer_lock);
	++g_mailbox_deinit_calls;
	if (g_gate_entered && !g_reader_returned)
		++g_mailbox_deinit_before_reader_exit;
	pthread_cond_broadcast(&g_observer_cond);
	pthread_mutex_unlock(&g_observer_lock);
}

static void dbg_print_interface(void) { }

static void set_bit(unsigned int bit, unsigned long *word)
{
	*word |= 1UL << bit;
}

static void init_work(struct work_struct *work) { work->pending = 0; }
#define INIT_WORK(work, callback) do { (void)(callback); init_work(work); } while (0)
static void __rprt_manager(struct work_struct *work) { (void)work; }

static int in_interrupt(void) { return g_in_interrupt; }

static void *kzalloc(size_t size, int flags)
{
	(void)flags;
	++g_kzalloc_calls;
	return calloc(1, size);
}

static void kfree(void *pointer) { free(pointer); }

static int npu_debug_memdump32_by_memcpy(u32 *source, u32 *destination)
{
	(void)source;
	(void)destination;
	mock_gate(GATE_MEMDUMP);
#ifdef EXPECT_BASELINE
	if (g_interface_header_is_null && g_interface_header_is_null()) {
		pthread_mutex_lock(&g_observer_lock);
		g_profiler_stale_escape = 1;
		pthread_mutex_unlock(&g_observer_lock);
	}
#endif
	return 0;
}

static void mbx_ipc_print(void *base, volatile struct mailbox_ctrl *ctrl, int level)
{
	(void)base;
	(void)ctrl;
	(void)level;
}

static int npu_fw_report_store(char *data, int size)
{
	(void)data;
	(void)size;
	++g_store_calls;
	return 0;
}

static void npu_log_memlog_sync_to_file(void) { }

static void reset_mailbox_ring(void)
{
	memset(&g_mailbox, 0, sizeof(g_mailbox));
	memset(g_mailbox_ring, 0, sizeof(g_mailbox_ring));
	g_mailbox.f2hctrl[MAILBOX_F2HCTRL_REPORT].sgmt_len = sizeof(g_mailbox_ring);
	g_mailbox.f2hctrl[MAILBOX_F2HCTRL_REPORT].wptr = 1;
	g_mailbox.f2hctrl[MAILBOX_F2HCTRL_REPORT].rptr = 0;
	g_mailbox.h2fctrl[MAILBOX_H2FCTRL_LPRIORITY].sgmt_len = sizeof(g_mailbox_ring);
	g_mailbox.h2fctrl[MAILBOX_H2FCTRL_LPRIORITY].wptr = LENGTHOFEVIDENCE;
	g_mailbox.h2fctrl[MAILBOX_H2FCTRL_MPRIORITY].sgmt_len = sizeof(g_mailbox_ring);
	g_mailbox.h2fctrl[MAILBOX_H2FCTRL_MPRIORITY].wptr = LENGTHOFEVIDENCE;
	g_mailbox.h2fctrl[MAILBOX_H2FCTRL_HPRIORITY].sgmt_len = sizeof(g_mailbox_ring);
	g_mailbox.h2fctrl[MAILBOX_H2FCTRL_HPRIORITY].wptr = LENGTHOFEVIDENCE;
	g_mailbox.f2hctrl[MAILBOX_F2HCTRL_RESPONSE].sgmt_len = sizeof(g_mailbox_ring);
	g_mailbox.f2hctrl[MAILBOX_F2HCTRL_NRESPONSE].sgmt_len = sizeof(g_mailbox_ring);
	g_mailbox.f2hctrl[MAILBOX_F2HCTRL_REPORT].wptr = 0;
	g_mailbox.f2hctrl[MAILBOX_F2HCTRL_REPORT].rptr = 0;
}

static int interface_header_is_null(void)
{
	return interface.mbox_hdr == NULL;
}

/* ACTUAL_REPORT_STATE */
static int mock_escape_saturated_owner_wait(void)
{
#ifdef EXPECT_BAD_REFCOUNT
	if (atomic_ref_read(&report_owner_refs) == REFCOUNT_SATURATED) {
		pthread_mutex_lock(&g_observer_lock);
		g_owner_wait_stalled = 1;
		pthread_cond_broadcast(&g_observer_cond);
		pthread_mutex_unlock(&g_observer_lock);
		return 1;
	}
#endif
	return 0;
}

/* ACTUAL_PROBE_OPEN_CLOSE */
/* ACTUAL_REPORT_READERS */
/* ACTUAL_LOG_CALLERS */

static void expect(int condition, const char *message)
{
	if (!condition) {
		fprintf(stderr, "FAIL: %s\n", message);
		exit(2);
	}
}

static void observer_reset_race(enum gate_kind kind)
{
	pthread_mutex_lock(&g_observer_lock);
	g_gate_kind = kind;
	g_gate_enabled = 1;
	g_gate_entered = 0;
	g_gate_release = 0;
	g_close_started = 0;
	g_close_done = 0;
	g_owner_wait_entered = 0;
	g_reader_returned = 0;
	g_reader_result = -999;
	g_direct_stale_escape = 0;
	g_profiler_stale_escape = 0;
	g_mailbox_deinit_before_reader_exit = 0;
	pthread_mutex_unlock(&g_observer_lock);
}

static void wait_for_flag(int *flag, const char *message)
{
	pthread_mutex_lock(&g_observer_lock);
	while (!*flag)
		pthread_cond_wait(&g_observer_cond, &g_observer_lock);
	pthread_mutex_unlock(&g_observer_lock);
	(void)message;
}

static void release_gate(void)
{
	pthread_mutex_lock(&g_observer_lock);
	g_gate_release = 1;
	pthread_cond_broadcast(&g_observer_cond);
	pthread_mutex_unlock(&g_observer_lock);
}

enum reader_kind { READER_KERNEL_NOTE, READER_FW_NOTE, READER_DEBUG, READER_PROFILER };
static enum reader_kind g_reader_kind;

static void *reader_thread(void *unused)
{
	(void)unused;
	switch (g_reader_kind) {
	case READER_KERNEL_NOTE:
		g_reader_result = fw_will_note_to_kernel(0);
		break;
	case READER_FW_NOTE:
		g_reader_result = fw_will_note(0);
		break;
	case READER_DEBUG:
		dbg_print_error();
		g_reader_result = 0;
		break;
	case READER_PROFILER:
		g_in_interrupt = 1;
		g_reader_result = npu_check_unposted_mbox(ECTRL_REPORT);
		g_in_interrupt = 0;
		break;
	}
	pthread_mutex_lock(&g_observer_lock);
	g_reader_returned = 1;
	pthread_cond_broadcast(&g_observer_cond);
	pthread_mutex_unlock(&g_observer_lock);
	return NULL;
}

static void *close_thread(void *unused)
{
	(void)unused;
	pthread_mutex_lock(&g_observer_lock);
	g_close_started = 1;
	pthread_cond_broadcast(&g_observer_cond);
	pthread_mutex_unlock(&g_observer_lock);
	g_close_result = npu_interface_close(&g_device.system);
	pthread_mutex_lock(&g_observer_lock);
	g_close_done = 1;
	pthread_cond_broadcast(&g_observer_cond);
	pthread_mutex_unlock(&g_observer_lock);
	return NULL;
}

static int call_probe(void)
{
	return npu_interface_probe(&g_platform.dev,
		(void *)(uintptr_t)0x1000, (void *)(uintptr_t)0x2000);
}

static int call_open(void) { return npu_interface_open(&g_device.system); }
static int call_close(void) { return npu_interface_close(&g_device.system); }

static void prepare_system(void)
{
	unsigned int i;
	(void)g_owner_wait_stalled;
	g_device.system.pdev = &g_platform;
	g_device.system.irq = g_irqs;
	g_device.system.irq_num = 3;
	g_device.system.mbox_hdr = &g_mailbox;
	for (i = 0; i < sizeof(mailbox_isr_list) / sizeof(mailbox_isr_list[0]); ++i)
		mailbox_isr_list[i] = mock_irq_handler;
	g_interface_lock = &interface.lock;
	g_interface_header_is_null = interface_header_is_null;
	g_log_ops.fw_rprt_gather = fw_rprt_gather;
	g_log_ops.npu_check_unposted_mbox = npu_check_unposted_mbox;
	fw_report.st_buf = g_report_storage;
	fw_report.st_size = sizeof(g_report_storage);
	fw_report.wr_pos = 0;
	fw_report.rp_pos = 0;
	fw_report.last_dump_line_cnt = 0;
	reset_mailbox_ring();
}

static void test_preprobe_readers_refuse_without_mutex(void)
{
	int locks_before = g_interface_mutex_acquires;
	int allocations_before = g_kzalloc_calls;
	fw_rprt_gather();
	dbg_print_error();
	expect(npu_check_unposted_mbox(ECTRL_REPORT) == -1,
		"pre-probe profiler returns without a mailbox owner");
	expect(g_interface_mutex_acquires == locks_before &&
		g_kzalloc_calls == allocations_before,
		"pre-probe report readers do not touch an uninitialized interface mutex");
	puts("PASS: pre-probe report readers refuse without touching uninitialized mutex");
}

static void start_reader_and_close(enum reader_kind kind, enum gate_kind gate,
		int expect_owner_wait)
{
	pthread_t reader;
	pthread_t closer;
	int deinit_before = g_mailbox_deinit_calls;
	int result;
	g_reader_kind = kind;
	observer_reset_race(gate);
	result = pthread_create(&reader, NULL, reader_thread, NULL);
	expect(result == 0, "create controlled report reader thread");
	wait_for_flag(&g_gate_entered, "reader reaches controlled source barrier");
	result = pthread_create(&closer, NULL, close_thread, NULL);
	expect(result == 0, "create concurrent close thread");
	wait_for_flag(&g_close_started, "close thread starts");
#ifndef EXPECT_BASELINE
	if (expect_owner_wait) {
		wait_for_flag(&g_owner_wait_entered,
			"close reaches wait after owner publication was detached");
		expect(g_mailbox_deinit_calls == deinit_before,
			"mailbox deinit must wait for the pinned direct reader");
		expect(interface.mbox_hdr == &g_mailbox,
			"close keeps the mutable interface pointer until pinned reader drain");
		expect(report_owner == NULL && refcount_read(&report_owner_refs) == 1,
			"detach drops only the publication reference while the active reader stays pinned");
		expect(atomic_load(&g_wait_lock_errors) == 0,
			"close waits without report/queue/owner/interface locks held");
		/* A reader arriving after detach must refuse before interface.lock/MMIO. */
		{
			int lock_count = g_interface_mutex_acquires;
			int allocations = g_kzalloc_calls;
			fw_rprt_gather();
			dbg_print_error();
			expect(npu_check_unposted_mbox(ECTRL_REPORT) == -1,
				"post-detach profiler reader refuses publication");
			expect(g_interface_mutex_acquires == lock_count &&
				g_kzalloc_calls == allocations,
				"post-detach readers do not touch interface mutex or allocate buffers");
		}
	} else {
		wait_for_flag(&g_close_done, "baseline close completes before reader resumes");
	}
#else
	(void)expect_owner_wait;
	wait_for_flag(&g_close_done, "baseline close completes before reader resumes");
	expect(g_mailbox_deinit_calls == deinit_before + 1,
		"baseline close deinitializes mailbox while direct reader remains paused");
#endif
	release_gate();
	expect(pthread_join(reader, NULL) == 0,
		"reader returns after controlled barrier release");
	expect(pthread_join(closer, NULL) == 0,
		"close returns after actual owner drain");
}

#ifdef EXPECT_BASELINE
static void test_baseline_direct_reader_escapes_close(void)
{
	expect(call_open() == 0, "baseline open for direct reader race");
	start_reader_and_close(READER_KERNEL_NOTE, GATE_INTERFACE_MUTEX, 0);
	expect(g_direct_stale_escape && g_mailbox_deinit_before_reader_exit == 1,
		"baseline direct reader crossed the pre-lock check; close deinitialized first");
	printf("REPRO: baseline direct reader passed pre-lock check; close deinitialized/cleared before stale dereference\n");
}

static void test_baseline_profiler_reader_escapes_close(void)
{
	expect(call_open() == 0, "baseline reopen for profiler race");
	start_reader_and_close(READER_PROFILER, GATE_MEMDUMP, 0);
	expect(g_profiler_stale_escape && g_mailbox_deinit_before_reader_exit == 1,
		"baseline interrupt-context profiler reader outlived mailbox deinit");
	printf("REPRO: baseline profiler direct reader escaped close after pre-lock header check\n");
}

int main(void)
{
	prepare_system();
	test_preprobe_readers_refuse_without_mutex();
	expect(call_probe() == 0, "baseline NPU13 probe succeeds");
	test_baseline_direct_reader_escapes_close();
	test_baseline_profiler_reader_escapes_close();
	return 0;
}
#elif defined(EXPECT_BAD_REFCOUNT)
int main(void)
{
	volatile struct mailbox_hdr *owner;
	prepare_system();
	g_report_owner_lock = &report_owner_lock;
	g_report_wq_lock = &report_wq_lock;
	expect(call_probe() == 0,
		"pinned refcount negative control initializes interface mutex");
	expect(npu_report_owner_publish(&g_mailbox) == 0,
		"pre-fix refcount negative control publishes sole owner reference");
	expect(refcount_read(&report_owner_refs) == 1,
		"pre-fix no-reader close begins with publication reference only");
	owner = npu_report_owner_detach();
	expect(owner == &g_mailbox && report_owner == NULL,
		"pre-fix detach withdraws owner before entering its wait");
	expect(atomic_ref_read(&report_owner_refs) == REFCOUNT_SATURATED &&
		g_refcount_warning_calls == 1 && g_owner_wait_stalled,
		"pinned refcount_dec at one saturates and leaves wait condition unsatisfied");
	puts("REPRO: pre-fix refcount_dec at one saturates; wait condition remains unsatisfied");
	return 0;
}
#else
static void test_preopen_openerror_and_probe_guard(void)
{
	int before_lock_count;
	int before_alloc_count;
	expect(call_probe() == -EBUSY && g_interface_mutex_init_calls == 1 &&
		g_interface_mutex_reinit_attempts == 0,
		"serialized reprobe is refused before interface.lock reinitialization");
	before_lock_count = g_interface_mutex_acquires;
	before_alloc_count = g_kzalloc_calls;
	fw_rprt_gather();
	dbg_print_error();
	expect(npu_check_unposted_mbox(ECTRL_REPORT) == -1,
		"profiler refuses before mailbox publication");
	expect(g_interface_mutex_acquires == before_lock_count &&
		g_kzalloc_calls == before_alloc_count,
		"before-open readers do not touch uninitialized mailbox state");
	expect(call_close() == 0 && call_close() == 0,
		"close-before-open and repeat close are safe");
	expect(g_mailbox_deinit_calls == 0,
		"close-before-open does not deinitialize unpublished mailbox");
	g_mailbox_init_result = -ETIMEDOUT;
	expect(call_open() == -ETIMEDOUT,
		"mailbox open error is returned through actual NPU13 unwind");
	before_lock_count = g_interface_mutex_acquires;
	before_alloc_count = g_kzalloc_calls;
	fw_rprt_gather();
	dbg_print_error();
	expect(npu_check_unposted_mbox(ECTRL_REPORT) == -1,
		"failed-open reader refuses unpublished mailbox owner");
	expect(g_interface_mutex_acquires == before_lock_count &&
		g_kzalloc_calls == before_alloc_count,
		"open-error readers do not access mailbox or interface mutex");
	expect(report_owner == NULL && refcount_read(&report_owner_refs) == 0,
		"failed mailbox initialization publishes no report owner reference");
	g_mailbox_init_result = 0;
	expect(call_open() == 0,
		"subsequent open recovers after mailbox initialization failure");
	expect(call_open() == -EBUSY,
		"active duplicate open is rejected without replacing the report owner");
	puts("PASS: before-open/open-error readers refuse unpublished owner; probe/reprobe guarded");
}

static void test_direct_and_debug_reader_races(void)
{
	start_reader_and_close(READER_KERNEL_NOTE, GATE_INTERFACE_MUTEX, 1);
	expect(g_close_result == 0 && g_reader_result == 0 &&
		g_mailbox_deinit_before_reader_exit == 0 &&
		!g_direct_stale_escape,
		"kernel note direct caller finishes against its pinned local owner");
	expect(report_owner == NULL && refcount_read(&report_owner_refs) == 0 &&
		interface.mbox_hdr == NULL,
		"close deinitializes and clears only after the direct reader drops its pin");
	expect(call_open() == 0, "report interface reopens after direct caller close");
	start_reader_and_close(READER_FW_NOTE, GATE_INTERFACE_MUTEX, 1);
	expect(g_close_result == 0 && g_reader_result == 0 &&
		g_mailbox_deinit_before_reader_exit == 0,
		"actual fw_will_note direct caller progresses while close waits for gather");
	expect(call_open() == 0, "report interface reopens after fw_will_note close");
	start_reader_and_close(READER_DEBUG, GATE_INTERFACE_MUTEX, 1);
	expect(g_close_result == 0 && !g_direct_stale_escape &&
		g_mailbox_deinit_before_reader_exit == 0,
		"debug report reader retains mailbox through interface mutex body");
	puts("PASS: direct caller close waits for pinned reader; post-detach callers refuse");
}

static void test_interrupt_profiler_and_reopen_progress(void)
{
	unsigned int locks_before = (unsigned int)g_interface_mutex_acquires;
	int deinit_before_no_reader;
	expect(call_open() == 0, "report interface reopens before interrupt profiler race");
	start_reader_and_close(READER_PROFILER, GATE_MEMDUMP, 1);
	expect(g_close_result == 0 && g_reader_result == 1 &&
		g_mailbox_deinit_before_reader_exit == 0 &&
		(unsigned int)g_interface_mutex_acquires == locks_before,
		"interrupt profiler retains owner without adding a sleeping interface mutex");
	expect(call_open() == 0,
		"caller and close make progress through another report-owner reopen");
	expect(report_owner == &g_mailbox && refcount_read(&report_owner_refs) == 1,
		"no-reader close begins with only the publication sentinel");
	deinit_before_no_reader = g_mailbox_deinit_calls;
	expect(call_close() == 0,
		"no-reader close drops the publication reference and returns");
	expect(report_owner == NULL && refcount_read(&report_owner_refs) == 0 &&
		g_mailbox_deinit_calls == deinit_before_no_reader + 1,
		"no-reader close drains the sole publication reference before deinit");
	expect(call_close() == 0 &&
		g_mailbox_deinit_calls == deinit_before_no_reader + 1,
		"repeat close is idempotent after a no-reader teardown");
	puts("PASS: no-reader close drops the publication sentinel with pinned refcount_dec_and_test");
	expect(report_owner == NULL && refcount_read(&report_owner_refs) == 0 &&
		g_interface_mutex_reinit_attempts == 0,
		"final teardown leaves no owner pin and never reinitializes interface.lock");
	puts("PASS: debug and interrupt-context profiler races drain; reopen/repeat-close progress");
}

static void test_refcount_saturation_fails_closed(void)
{
	volatile struct mailbox_hdr *owner;
	int wakeups_before = g_mailbox_deinit_calls;
	{
		unsigned long flags;
		spin_lock_irqsave(&report_owner_lock, flags);
	report_owner = &g_mailbox;
	refcount_set(&report_owner_refs, REFCOUNT_MAX);
		spin_unlock_irqrestore(&report_owner_lock, flags);
	}
	owner = npu_report_owner_get();
	expect(owner == &g_mailbox && refcount_read(&report_owner_refs) != 0,
		"saturated report refcount cannot wrap into a false free condition");
	npu_report_owner_put();
	expect(refcount_read(&report_owner_refs) != 0,
		"put on saturated reader counter remains fail-closed");
	{
		unsigned long flags;
		spin_lock_irqsave(&report_owner_lock, flags);
		report_owner = NULL;
		refcount_set(&report_owner_refs, 0);
		spin_unlock_irqrestore(&report_owner_lock, flags);
	}
	refcount_set(&report_owner_refs, 0);
	npu_report_owner_put();
	expect(refcount_read(&report_owner_refs) != 0 &&
		g_mailbox_deinit_calls == wakeups_before,
		"underflow saturates rather than allowing mailbox deinit");
	puts("PASS: report-owner counter overflow/underflow fail closed rather than wrap to free");
}

int main(void)
{
	prepare_system();
	g_report_owner_lock = &report_owner_lock;
	g_report_wq_lock = &report_wq_lock;
	test_preprobe_readers_refuse_without_mutex();
	expect(call_probe() == 0, "patched NPU13 probe succeeds");
	test_preopen_openerror_and_probe_guard();
	test_direct_and_debug_reader_races();
	test_interrupt_profiler_and_reopen_progress();
	test_refcount_saturation_fails_closed();
	expect(atomic_load(&g_order_errors) == 0,
		"direct note callers do not acquire owner lock under fw_report_lock");
	expect(atomic_load(&g_wait_lock_errors) == 0,
		"close never waits while holding owner/report/queue/interface reader locks");
	expect(g_mailbox_init_calls >= 6 && g_mailbox_deinit_calls == 5,
		"open/reopen and close/deinit lifecycle counts remain balanced");
	return 0;
}
#endif
