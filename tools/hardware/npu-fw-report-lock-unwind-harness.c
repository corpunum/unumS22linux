/*
 * Template for test-npu-fw-report-lock-unwind.py.
 * Pinned Linux C bodies are injected at the markers below; all surrounding
 * synchronization and MMIO shims are host-only and deliberately observable.
 */
#include <errno.h>
#include <setjmp.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef uint32_t u32;
typedef struct { int counter; } atomic_t;

#define NPU_MAX_MSG_ID_CNT 64
#define MSGID_POOL_MAGIC 0x18D718D7U
#define PROTO_DRV_REQ_TYPE_NW 2
#define FW_LOGSIZE 0
#define PAGE_SIZE 4096
#define BUFSIZE 1024
#define MAILBOX_F2HCTRL_REPORT 2
#define MAILBOX_F2HCTRL_COUNT 3
#define MAILBOX_F2HCTRL_LOW 0
#define MAILBOX_F2HCTRL_MEDIUM 1
#define MAILBOX_F2HCTRL_HIGH 2
#define MAILBOX_F2HCTRL_ACK 3
#define MAILBOX_F2HCTRL_NACK 4
#define NPU_STORE_LOG_FLUSH_INTERVAL_MS 1
#define CONFIG_NPU_MAILBOX_VERSION 9
#define IS_ENABLED(option) 0
#define TRUE 1
#define FALSE 0
#define ECTRL_LOW 0
#define ECTRL_MEDIUM 1
#define ECTRL_HIGH 2
#define ECTRL_ACK 3
#define ECTRL_NACK 4
#define ECTRL_REPORT 5
#define likely(value) (value)
#define unlikely(value) (value)

#define npu_dbg(...) do { ++g_suppressed_log_calls; } while (0)
#define npu_warn(...) do { ++g_suppressed_log_calls; } while (0)
#define npu_err(...) do { ++g_suppressed_log_calls; } while (0)
#define npu_info(...) do { ++g_suppressed_log_calls; } while (0)
#define npu_dump(...) do { ++g_suppressed_log_calls; } while (0)
#define probe_err(...) do { ++g_suppressed_log_calls; } while (0)

typedef struct { int held; } spinlock_t;
typedef struct { int held; } mutex_t;

static spinlock_t fw_report_lock;
static spinlock_t fw_profile_lock;
static int g_irq_enabled = 1;
static int g_lock_errors;
static int g_suppressed_log_calls;
static int g_mutex_errors;
static int g_mutex_lock_calls;
static int g_mutex_unlock_calls;
static int g_wakeup_calls;
static int g_flush_sleep_calls;
static int g_actual_gather_calls;
static int g_actual_report_store_calls;
static int g_unposted_check_calls;
static int g_bug_calls;
static int g_bug_armed;
static int g_failures;
static jmp_buf g_bug_env;

static void mock_spin_lock_irqsave(spinlock_t *lock, unsigned long *flags)
{
	*flags = (unsigned long)g_irq_enabled;
	if (lock->held)
		++g_lock_errors;
	else
		lock->held = 1;
	g_irq_enabled = 0;
}

static void mock_spin_unlock_irqrestore(spinlock_t *lock, unsigned long flags)
{
	if (!lock->held)
		++g_lock_errors;
	else
		lock->held = 0;
	g_irq_enabled = (int)flags;
}

#define spin_lock_irqsave(lock, flags) \
	mock_spin_lock_irqsave((lock), &(flags))
#define spin_unlock_irqrestore(lock, flags) \
	mock_spin_unlock_irqrestore((lock), (flags))

static void mock_mutex_lock(mutex_t *lock)
{
	++g_mutex_lock_calls;
	if (lock->held)
		++g_mutex_errors;
	else
		lock->held = 1;
}

static void mock_mutex_unlock(mutex_t *lock)
{
	++g_mutex_unlock_calls;
	if (!lock->held)
		++g_mutex_errors;
	else
		lock->held = 0;
}

#define mutex_lock(lock) mock_mutex_lock(lock)
#define mutex_unlock(lock) mock_mutex_unlock(lock)
#define wake_up_all(waitq) do { (void)(waitq); ++g_wakeup_calls; } while (0)
#define msleep(milliseconds) do { \
	(void)(milliseconds); ++g_flush_sleep_calls; \
} while (0)
#define memcpy_fromio(destination, source, length) \
	memcpy((destination), (source), (length))
#define LINE_TO_SGMT(length, position) ((position) & ((length) - 1))
#define NPU_MBOX_BASE(pointer) (pointer)
#define NPU_MAILBOX_GET_CTRL(pointer, offset) \
	((void)(pointer), (void)(offset), g_report_ring)
#define BUG_ON(condition) do { \
	if (condition) { \
		++g_bug_calls; \
		if (g_bug_armed) longjmp(g_bug_env, 1); \
		fprintf(stderr, "unexpected pinned BUG_ON at %s:%d\n", \
			__FILE__, __LINE__); \
		exit(3); \
	} \
} while (0)
#define WARN_ON(condition) ((condition) ? (++g_suppressed_log_calls, 1) : 0)

struct npu_store_log {
	char *st_buf;
	size_t st_size;
	size_t wr_pos;
	size_t rp_pos;
	size_t line_cnt;
	size_t last_dump_line_cnt;
	int wq;
};

struct mailbox_ctrl {
	u32 wptr;
	u32 rptr;
	u32 sgmt_len;
	u32 sgmt_ofs;
};

struct mailbox_header {
	struct mailbox_ctrl f2hctrl[MAILBOX_F2HCTRL_COUNT];
};

struct npu_log_ops {
	void (*fw_rprt_gather)(void);
	void (*npu_check_unposted_mbox)(int channel);
};

struct npu_log_state {
	struct npu_log_ops *log_ops;
};

struct npu_interface_state {
	struct mailbox_header *mbox_hdr;
	mutex_t lock;
};

struct msgid_pool_entry {
	atomic_t occupied;
	int pt_type;
	void *ref;
};

struct msgid_pool {
	struct msgid_pool_entry pool[NPU_MAX_MSG_ID_CNT];
	u32 magic;
};

static struct npu_store_log fw_report;
static struct npu_store_log fw_profile;
static struct npu_log_ops g_log_ops;
static struct npu_log_state npu_log = { &g_log_ops };
static struct npu_interface_state interface;
static struct mailbox_header g_mailbox_header;
static char g_report_ring[2048];
static struct msgid_pool g_msgid_pool;

static int atomic_read(const atomic_t *value)
{
	return value->counter;
}

static void atomic_set(atomic_t *value, int next)
{
	value->counter = next;
}

static void npu_log_memlog_sync_to_file(void) { }

static void mock_unposted_check(int channel)
{
	(void)channel;
	++g_unposted_check_calls;
}

static void mock_gather_entry(void);

static void reset_locks_and_observers(void)
{
	fw_report_lock.held = 0;
	fw_profile_lock.held = 0;
	g_irq_enabled = 1;
	g_lock_errors = 0;
	g_mutex_errors = 0;
	g_mutex_lock_calls = 0;
	g_mutex_unlock_calls = 0;
	g_wakeup_calls = 0;
	g_flush_sleep_calls = 0;
	g_actual_gather_calls = 0;
	g_actual_report_store_calls = 0;
	g_unposted_check_calls = 0;
	g_bug_calls = 0;
	g_bug_armed = 0;
	interface.lock.held = 0;
}

static int locks_are_clear(void)
{
	return !fw_report_lock.held && !fw_profile_lock.held &&
		g_irq_enabled == 1 && g_lock_errors == 0;
}

static void reset_mailbox(void)
{
	memset(&g_mailbox_header, 0, sizeof(g_mailbox_header));
	memset(g_report_ring, 0, sizeof(g_report_ring));
	interface.mbox_hdr = &g_mailbox_header;
	interface.lock.held = 0;
}

static void setup_log_ops(void)
{
	g_log_ops.fw_rprt_gather = mock_gather_entry;
	g_log_ops.npu_check_unposted_mbox = mock_unposted_check;
}

static int npu_fw_report_store(char *strRep, int nSize);

/* ACTUAL_LOG_FUNCTIONS */
/* ACTUAL_GATHER_FUNCTION */
/* ACTUAL_MSGID_FUNCTIONS */

static int npu_fw_report_store(char *strRep, int nSize)
{
	++g_actual_report_store_calls;
	return npu_fw_report_store_body(strRep, nSize);
}

static void mock_gather_entry(void)
{
	++g_actual_gather_calls;
	fw_rprt_gather();
}

#define CHECK(condition, message) do { \
	if (!(condition)) { \
		fprintf(stderr, "FAIL: %s\n", message); \
		++g_failures; \
	} \
} while (0)

static void test_null_buffer_returns(int baseline)
{
	static char report_storage[PAGE_SIZE];
	static char profile_storage[PAGE_SIZE];
	int irq_start;
	int result;

	for (irq_start = 0; irq_start <= 1; ++irq_start) {
		reset_locks_and_observers();
		memset(&fw_report, 0, sizeof(fw_report));
		g_irq_enabled = irq_start;
		result = npu_fw_report_store("x", 1);
		CHECK(result == -ENOMEM,
			"report store preserves -ENOMEM for NULL buffer");
		if (baseline) {
			CHECK(fw_report_lock.held && g_irq_enabled == 0,
				"baseline report store reproduces held report lock/disabled IRQs");
			puts("REPRO: baseline report store returns with fw_report_lock held");
		} else {
			CHECK(!fw_report_lock.held && !fw_profile_lock.held &&
				g_irq_enabled == irq_start && g_lock_errors == 0,
				"patched report store restores its entry IRQ state");
		}

		reset_locks_and_observers();
		memset(&fw_profile, 0, sizeof(fw_profile));
		g_irq_enabled = irq_start;
		result = npu_fw_profile_store("x", 1);
		CHECK(result == -ENOMEM,
			"profile store preserves -ENOMEM for NULL buffer");
		if (baseline) {
			CHECK(fw_profile_lock.held && g_irq_enabled == 0,
				"baseline profile store reproduces held profile lock/disabled IRQs");
			puts("REPRO: baseline profile store returns with fw_profile_lock held");
		} else {
			CHECK(!fw_profile_lock.held && !fw_report_lock.held &&
				g_irq_enabled == irq_start && g_lock_errors == 0,
				"patched profile store restores its entry IRQ state");
		}

		reset_locks_and_observers();
		memset(&fw_report, 0, sizeof(fw_report));
		reset_mailbox();
		g_irq_enabled = irq_start;
		result = fw_will_note_to_kernel(8);
		CHECK(result == -ENOMEM,
			"kernel report note preserves -ENOMEM for NULL buffer");
		if (baseline) {
			CHECK(fw_report_lock.held && g_irq_enabled == 0,
				"baseline kernel note reproduces held report lock");
			puts("REPRO: baseline fw_will_note_to_kernel leaks fw_report_lock");
		} else {
			CHECK(!fw_report_lock.held && g_irq_enabled == irq_start &&
				g_lock_errors == 0,
				"patched kernel note restores its entry IRQ state");
		}
		CHECK(g_actual_gather_calls == 1 &&
			g_actual_report_store_calls == 0 && !interface.lock.held &&
			g_mutex_errors == 0,
			"empty note gather returns through actual body before NULL report check");

		reset_locks_and_observers();
		memset(&fw_report, 0, sizeof(fw_report));
		reset_mailbox();
		g_irq_enabled = irq_start;
		result = fw_will_note(8);
		CHECK(result == -ENOMEM,
			"report note preserves -ENOMEM for NULL buffer");
		if (baseline) {
			CHECK(fw_report_lock.held && g_irq_enabled == 0,
				"baseline report note reproduces held report lock");
			puts("REPRO: baseline fw_will_note leaks fw_report_lock");
		} else {
			CHECK(!fw_report_lock.held && g_irq_enabled == irq_start &&
				g_lock_errors == 0,
				"patched report note restores its entry IRQ state");
		}
		CHECK(g_actual_gather_calls == 1 &&
			g_actual_report_store_calls == 0 && !interface.lock.held &&
			g_mutex_errors == 0,
			"empty note gather returns through actual body before NULL report check");
	}

	/* Keep extracted init/deinit compiled and exercise their healthy pair too. */
	reset_locks_and_observers();
	npu_fw_report_init(report_storage, sizeof(report_storage));
	npu_fw_profile_init(profile_storage, sizeof(profile_storage));
	CHECK(fw_report.st_buf == report_storage && fw_profile.st_buf == profile_storage,
		"actual report/profile init functions publish supplied storage");
	npu_fw_report_deinit();
	npu_fw_profile_deinit();
	CHECK(!fw_report.st_buf && !fw_profile.st_buf && locks_are_clear(),
		"actual report/profile deinit functions clear storage with balanced locks");
	CHECK(g_wakeup_calls == 2 && g_flush_sleep_calls == 2,
		"actual deinit paths retain wake-and-flush ordering hooks");
}

static void test_nonnull_store_and_wrap(void)
{
	static char report_storage[PAGE_SIZE];
	static char profile_storage[PAGE_SIZE];
	const char report_text[] = "a\nb\n";
	const char profile_text[] = "p\nq\n";

	reset_locks_and_observers();
	reset_mailbox();
	npu_fw_report_init(report_storage, sizeof(report_storage));
	npu_fw_profile_init(profile_storage, sizeof(profile_storage));
	CHECK(npu_fw_report_store((char *)report_text, sizeof(report_text) - 1) == 0,
		"actual report store accepts non-NULL storage");
	CHECK(npu_fw_profile_store((char *)profile_text, sizeof(profile_text) - 1) == 0,
		"actual profile store accepts non-NULL storage");
	CHECK(strcmp(report_storage, report_text) == 0 &&
		strcmp(profile_storage, profile_text) == 0,
		"actual stores preserve ordinary synthetic text and newline termination");
	CHECK(locks_are_clear(), "ordinary store paths balance locks and IRQ state");

	fw_report.wr_pos = sizeof(report_storage) - 3;
	CHECK(npu_fw_report_store("WXYZ", 4) == 0,
		"actual report store accepts wrap-triggering data");
	CHECK(memcmp(report_storage, "WXYZ", 4) == 0 && fw_report.wr_pos == 4 &&
		fw_report.last_dump_line_cnt == 1,
		"actual report store retains its existing wrap behavior");

	fw_profile.wr_pos = sizeof(profile_storage) - 3;
	CHECK(npu_fw_profile_store("1234", 4) == 0,
		"actual profile store accepts wrap-triggering data");
	CHECK(memcmp(profile_storage, "1234", 4) == 0 && fw_profile.wr_pos == 4 &&
		fw_profile.last_dump_line_cnt == 1,
		"actual profile store retains its existing wrap behavior");
	CHECK(locks_are_clear(), "wrap paths balance locks and IRQ state");
}

static void test_gather_store_error(int baseline)
{
	struct mailbox_ctrl *ctrl;

	reset_locks_and_observers();
	reset_mailbox();
	memset(&fw_report, 0, sizeof(fw_report));
	ctrl = &g_mailbox_header.f2hctrl[MAILBOX_F2HCTRL_REPORT];
	ctrl->sgmt_len = 16;
	ctrl->rptr = 0;
	ctrl->wptr = 3;
	memcpy(g_report_ring, "xyz", 3);
	g_irq_enabled = 0;
	fw_rprt_gather();
	CHECK(ctrl->rptr == 3,
		"actual gather retains its pinned pointer advance after store error");
	CHECK(g_actual_report_store_calls == 1,
		"actual gather error fixture enters the actual report-store body once");
	CHECK(!interface.lock.held && g_mutex_lock_calls == 1 &&
		g_mutex_unlock_calls == 1 && g_mutex_errors == 0,
		"actual gather releases interface.lock after the store error");
	if (baseline) {
		CHECK(fw_report_lock.held && g_irq_enabled == 0,
			"baseline gather exposes the report-store lock leak on NULL storage");
		puts("REPRO: baseline gather ignores NULL-store error and leaks report lock");
	} else {
		CHECK(!fw_report_lock.held && g_irq_enabled == 0 && g_lock_errors == 0,
			"patched gather's failed store restores its saved IRQ state");
		puts("PASS: gather preserves its existing read-pointer advance while store error unwinds lock");
	}
}

static void test_actual_gather_ring_wrap_and_note(void)
{
	static char report_storage[PAGE_SIZE];
	struct mailbox_ctrl *ctrl;

	reset_locks_and_observers();
	reset_mailbox();
	npu_fw_report_init(report_storage, sizeof(report_storage));
	ctrl = &g_mailbox_header.f2hctrl[MAILBOX_F2HCTRL_REPORT];
	ctrl->sgmt_len = 16;
	ctrl->rptr = 13;
	ctrl->wptr = 3;
	memcpy(&g_report_ring[13], "ab\n", 3);
	memcpy(&g_report_ring[0], "cd\n", 3);
	fw_rprt_gather();
	CHECK(ctrl->rptr == 3 && fw_report.wr_pos == 6,
		"actual gather consumes one wrapped synthetic mailbox segment");
	CHECK(g_actual_report_store_calls == 2,
		"wrapped actual gather enters report-store body for both ring segments");
	CHECK(memcmp(report_storage, "ab\ncd\n", 6) == 0,
		"actual gather forwards bytes through the actual report-store body");
	CHECK(g_mutex_lock_calls == 1 && g_mutex_unlock_calls == 1 &&
		!interface.lock.held && g_mutex_errors == 0,
		"actual gather balances its interface mutex on the ring-wrap path");
	CHECK(fw_will_note(6) == 0,
		"actual note handles gathered newline records with valid storage");
	CHECK(locks_are_clear() && !interface.lock.held && g_mutex_errors == 0,
		"gather plus note leaves all modeled locks/IRQ state balanced");
	CHECK(g_suppressed_log_calls > 0,
		"newline reporting paths ran through suppressed host logging shims");
	puts("PASS: actual gather wrap and newline paths completed with payload logging suppressed");
}

static void test_invalid_firmware_id_path(int baseline)
{
	int irq_start;
	for (irq_start = 0; irq_start <= 1; ++irq_start) {
		int result_type = -99;
		int jumped;
		struct mailbox_ctrl *ctrl;

		reset_locks_and_observers();
		reset_mailbox();
		memset(&fw_report, 0, sizeof(fw_report));
		memset(&g_msgid_pool, 0, sizeof(g_msgid_pool));
		g_msgid_pool.magic = MSGID_POOL_MAGIC;
		atomic_set(&g_msgid_pool.pool[0].occupied, 1);
		ctrl = &g_mailbox_header.f2hctrl[MAILBOX_F2HCTRL_REPORT];
		ctrl->sgmt_len = 16;
		ctrl->rptr = 0;
		ctrl->wptr = 3;
		memcpy(g_report_ring, "xyz", 3);
		g_irq_enabled = irq_start;
		g_bug_armed = 1;
		jumped = setjmp(g_bug_env);
		if (jumped == 0)
			result_type = msgid_get_pt_type(&g_msgid_pool,
				NPU_MAX_MSG_ID_CNT);
		g_bug_armed = 0;

		if (baseline) {
			CHECK(jumped != 0 && g_bug_calls > 0,
				"pinned baseline invalid firmware ID still reaches its BUG_ON");
			CHECK(fw_report_lock.held && g_irq_enabled == 0,
				"baseline composed high-ID path leaks report lock before BUG");
			CHECK(g_actual_gather_calls == 1 &&
				g_actual_report_store_calls == 1 && ctrl->rptr == 3,
				"baseline high-ID note gathers a segment through actual store");
			CHECK(!interface.lock.held && g_mutex_lock_calls == 1 &&
				g_mutex_unlock_calls == 1 && g_mutex_errors == 0,
				"baseline composed shim continues past leaked spinlock and balances interface mutex");
			CHECK(g_lock_errors == 1,
				"baseline shim records the recursive report-lock attempt before BUG");
			puts("REPRO: baseline composed high-ID path leaks in actual gather/store; shim records relock then BUGs");
		} else {
			CHECK(jumped == 0 && result_type == -1 && g_bug_calls == 0,
				"patched invalid firmware ID type lookup returns -1 without BUG_ON");
			CHECK(!fw_report_lock.held && !fw_profile_lock.held &&
				g_irq_enabled == irq_start && g_lock_errors == 0,
				"composed actual store and note paths restore entry IRQ state and release locks");
			CHECK(g_actual_gather_calls == 1 &&
				g_actual_report_store_calls == 1 && ctrl->rptr == 3,
				"patched high-ID note gathers one segment through actual NULL report store");
			CHECK(!interface.lock.held && g_mutex_lock_calls == 1 &&
				g_mutex_unlock_calls == 1 && g_mutex_errors == 0,
				"actual gather releases interface mutex on the composed store-error path");
			puts("PASS: high-ID validator reaches actual gather/store(NULL)/note with balanced locks");
		}
		CHECK(atomic_read(&g_msgid_pool.pool[0].occupied) == 1,
			"invalid high ID leaves an unrelated valid pool slot untouched");
	}
}

int main(void)
{
#ifdef EXPECT_BASELINE
	const int baseline = 1;
#else
	const int baseline = 0;
#endif

	setup_log_ops();
	test_null_buffer_returns(baseline);
	test_gather_store_error(baseline);
	test_nonnull_store_and_wrap();
	test_actual_gather_ring_wrap_and_note();
	test_invalid_firmware_id_path(baseline);
	if (g_failures) {
		fprintf(stderr, "FAIL: %d extracted-C assertion(s) failed\n", g_failures);
		return 1;
	}
	puts("PASS: extracted pinned NPU report/profile lock-unwind scenarios");
	return 0;
}
