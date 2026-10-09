/* Host harness template; Python injects SHA-verified production C fragments. */
#include <errno.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef uint32_t u32;
typedef uint64_t u64;
typedef u32 npu_errno_t;
typedef u32 npu_req_id_t;
typedef struct { int counter; } atomic_t;

#define CONFIG_NPU_USE_BOOT_IOCTL 1
#define CONFIG_NPU_MAILBOX_VERSION 9
#define CONFIG_NPU_COMMAND_VERSION 10
#define CONFIG_DSP_USE_VS4L 1
#define NPU_MAX_MSG_ID_CNT NPU_MAX_MSG_ID_CNT_VALUE
#define NPU_HWDEV_ID_NPU NPU_HWDEV_ID_NPU_VALUE
#define NPU_HWDEV_ID_DSP NPU_HWDEV_ID_DSP_VALUE
#define MSGID_POOL_MAGIC MSGID_POOL_MAGIC_VALUE
#define MESSAGE_MAGIC MESSAGE_MAGIC_VALUE
#define EXPECT_OWNERSHIP_PATCH EXPECT_OWNERSHIP_PATCH_VALUE
#define EXPECTED_FIRST_RESULT EXPECTED_FIRST_RESULT_VALUE
#define NPU_ERR_CODE(value) ((u32)(value) & 0xffffU)
#define NPU_CRITICAL_DRIVER(code) (0xdc000000U | ((u32)(code) & 0xffffU))
#define EPARAM 41
#define ERESOURCE 43
#define EALIGN 44
#define MAILBOX_TIMEOUT_VALUE 3
#define MAILBOX_CLEAR_CHECK_TIMEOUT MAILBOX_TIMEOUT_VALUE
#define FAKE_RING_BYTES 4096U
/* Pinned ABI declarations are injected before harness structs. */
#define TRUE 1
#define FALSE 0
#define NPU_LOG_DBG 1
#define NPU_LOG_INFO 2
#define NPU_LOG_ERR 3
#define NPU_MBOX_REQUEST_LOW 0
#define NPU_MBOX_REQUEST_MEDIUM 1
#define NPU_MBOX_REQUEST_HIGH 2
#define FW_LOGSIZE 0
#define likely(value) (value)
#define unlikely(value) (value)
#define dmb(order) ((void)0)
#define dsb(order) fake_dsb()
#define spin_lock_irqsave(lock, flags) do { (void)(lock); (flags) = 0; } while (0)
#define spin_unlock_irqrestore(lock, flags) do { (void)(lock); (void)(flags); } while (0)
#define LINE_TO_SGMT(length, ptr) ((ptr) & ((length) - 1))
#define NPU_MBOX_BASE(pointer) ((char *)(pointer))
#define NPU_MAILBOX_GET_CTRL(underlay, offset) ((char *)fake_mailbox_ring)
#define npu_dbg(...) ((void)0)
#define npu_info(...) ((void)0)
#define npu_warn(...) ((void)0)
#define npu_err(...) ((void)0)
#define npu_uinfo(...) ((void)0)
#define npu_uwarn(...) ((void)0)
#define npu_utrace(...) ((void)0)
#define npu_unotice(...) ((void)0)
#define npu_log_protodrv_set_data(...) ((void)0)
#define npu_log_ipc_set_date(...) ((void)0)
#define BUG_ON(condition) do { if (condition) fail("production BUG_ON fired"); } while (0)
#define WARN_ON(condition) ((void)(condition))

struct npu_session { int hids; };
struct npu_nw;
struct nw_result;
struct proto_req_nw;
struct msgid_pool;

struct npu_nw {
	int uid;
	int bound_id;
	npu_req_id_t npu_req_id;
	npu_errno_t result_code;
	int result_value;
	struct npu_session *session;
	nw_cmd_e cmd;
	int param0;
	int param1;
	int msgid;
	int (*notify_func)(struct npu_session *, struct nw_result);
};

struct nw_result {
	npu_errno_t result_code;
	struct npu_nw nw;
};

struct npu_power_waiter {
	u64 cookie;
	npu_req_id_t req_id;
	int done;
	int cancelled;
	int registered;
	int completion;
	struct nw_result result;
};

struct proto_req_nw {
	int state;
	struct npu_nw nw;
};

struct npu_if_protodrv_mbox_ops {
	int (*nw_post_request)(int msgid, struct npu_nw *nw);
	int (*nw_get_result)(int *msgid, struct npu_nw *nw);
};

struct npu_if_protodrv_mbox {
	const struct npu_if_protodrv_mbox_ops *npu_if_protodrv_mbox_ops;
};

struct npu_proto_drv {
	struct msgid_pool msgid_pool;
	void *npu_device;
};

struct mailbox_ctrl {
	u32 sgmt_ofs;
	u32 sgmt_len;
	u32 wptr;
	u32 rptr;
};

struct mailbox_header {
	struct mailbox_ctrl h2fctrl[8];
};

struct mailbox_sfr_group {
	volatile u32 ms;
	volatile u32 s;
	volatile u32 c;
	volatile u32 g;
};

struct mailbox_sfr {
	struct mailbox_sfr_group grp[4];
};

struct interface_state {
	struct mailbox_header *mbox_hdr;
	volatile struct mailbox_sfr *sfr;
};

enum { FREE = 0, PROCESSING = 1, COMPLETED = 2, REQUESTED = 3, STUCKED = 4 };

static char fake_mailbox_ring[FAKE_RING_BYTES];
static struct npu_proto_drv npu_proto_drv;
static struct npu_power_waiter fake_power_waiter;
static int npu_power_waiters_lock;
static struct mailbox_header fake_mailbox_header;
static struct mailbox_sfr fake_sfr;
static struct interface_state interface;
static struct npu_if_protodrv_mbox protodrv_mbox;
static struct npu_if_protodrv_mbox_ops fake_mbox_ops;
static int fake_debug_dump_count;
static int fake_publish_begin_count;
static int fake_publish_authorize_count;
static int fake_publish_finish_count;
static int fake_publish_waiter_active;
static int fake_lsm_move_count;
static int fake_last_lsm_state;
static int fake_emergency_set_count;
static struct proto_req_nw *fake_last_lsm_entry;
static int fake_result_ready;
static int fake_result_msgid;
static int fake_result_code;
static int fake_result_value;
static int fake_reply_on_commit;

static void fail(const char *message);
static void fake_dsb(void);
static int atomic_read(const atomic_t *value) { return value->counter; }
static void atomic_set(atomic_t *value, int next) { value->counter = next; }
static int atomic_cmpxchg(atomic_t *value, int old, int next)
{
	return __sync_val_compare_and_swap(&value->counter, old, next);
}
static int atomic_xchg(atomic_t *value, int next)
{
	return __sync_lock_test_and_set(&value->counter, next);
}

static struct npu_proto_drv *protodr;
int npu_session_save_power_result(struct npu_session *session,
		struct nw_result result);
int nw_req_manager(int msgid, struct npu_nw *nw);
int npu_nw_mbox_ops_get(struct msgid_pool *pool, struct proto_req_nw **target);
int npu_nw_mbox_ops_put(struct msgid_pool *pool, struct proto_req_nw *src);
int msgid_issue_save_ref(struct msgid_pool *handle, const int pt_type,
		void *ref, struct npu_session *session);
void msgid_claim(struct msgid_pool *handle, const int msg_id);
void *msgid_claim_get_ref(struct msgid_pool *handle, const int msg_id,
		const int expected_type);
void msgid_pool_init(struct msgid_pool *handle);
static int npu_protodrv_handler_nw_processing(void);
static int nw_mgmt_op_get_request(struct proto_req_nw *target);
static int nw_mbox_ops_get(struct proto_req_nw **target);
static int nw_mbox_ops_put(struct proto_req_nw *src);
static int run_actual_power_ctl_case(struct proto_req_nw *entry);
static int is_stucked_req_nw(const struct proto_req_nw *req_nw);
static void actual_stucked_transition(struct proto_req_nw *entry);
static void set_emergency_err_from_req_nw(struct proto_req_nw *entry);
static struct npu_power_waiter *npu_power_waiter_find(u64 cookie);
static void complete(int *completion);
static void dbg_dump_mbox(void);
static void mbx_ipc_print_dbg(char *underlay, volatile struct mailbox_ctrl *ctrl);
static void dbg_print_msg(struct message *msg, struct command *cmd);
static void dbg_print_ctrl(volatile struct mailbox_ctrl *ctrl);

static struct npu_power_waiter *npu_power_waiter_find(u64 cookie)
{
	if (fake_power_waiter.registered && fake_power_waiter.cookie == cookie)
		return &fake_power_waiter;
	return NULL;
}

static void complete(int *completion)
{
	(*completion)++;
}

static int npu_session_power_wait_assign_req_id(u64 cookie, npu_req_id_t req_id)
{
	(void)cookie;
	(void)req_id;
	return 0;
}

static struct npu_nw fake_queued_nw;
static int fake_queue_ready;
static npu_req_id_t fake_next_request_id;

static int npu_ncp_mgmt_get(struct npu_nw *target)
{
	if (!fake_queue_ready)
		return 0;
	*target = fake_queued_nw;
	fake_queue_ready = 0;
	return 1;
}

static npu_req_id_t get_next_npu_req_id(void)
{
	return fake_next_request_id;
}

static int npu_session_power_wait_begin_publish(u64 cookie, npu_req_id_t req_id)
{
	(void)cookie;
	(void)req_id;
	fake_publish_begin_count++;
	return fake_publish_waiter_active;
}

static int npu_session_power_wait_authorize_publish(u64 cookie, npu_req_id_t req_id)
{
	(void)cookie;
	(void)req_id;
	fake_publish_authorize_count++;
	return fake_publish_waiter_active;
}

static void npu_session_power_wait_finish_publish(u64 cookie, npu_req_id_t req_id)
{
	(void)cookie;
	(void)req_id;
	fake_publish_finish_count++;
}

static int npu_device_is_emergency_err(void *device)
{
	(void)device;
	return 0;
}

static void fw_will_note(int code)
{
	(void)code;
}

static void proto_nw_lsm_move_entry(int state, struct proto_req_nw *entry)
{
	entry->state = state;
	fake_lsm_move_count++;
	fake_last_lsm_state = state;
	fake_last_lsm_entry = entry;
}

static void set_emergency_err_from_req_nw(struct proto_req_nw *entry)
{
	(void)entry;
	fake_emergency_set_count++;
}

static struct {
	void (*lsm_move_entry)(int state, struct proto_req_nw *entry);
} proto_nw_lsm = { .lsm_move_entry = proto_nw_lsm_move_entry };

static void dbg_print_msg(struct message *msg, struct command *cmd)
{
	(void)msg;
	(void)cmd;
}

static void dbg_print_ctrl(volatile struct mailbox_ctrl *ctrl)
{
	(void)ctrl;
}

static void dbg_dump_mbox(void)
{
	fake_debug_dump_count++;
}

static void mbx_ipc_print_dbg(char *underlay, volatile struct mailbox_ctrl *ctrl)
{
	(void)underlay;
	(void)ctrl;
}

static void fake_dsb(void)
{
	if (fake_reply_on_commit && fake_mailbox_header.h2fctrl[0].wptr) {
		memcpy(&fake_result_msgid, fake_mailbox_ring + sizeof(u32), sizeof(u32));
		fake_result_ready = 1;
		fake_reply_on_commit = 0;
	}
}

static int fake_nw_get_result(int *msgid, struct npu_nw *nw)
{
	if (!fake_result_ready)
		return 0;
	*msgid = fake_result_msgid;
	nw->result_code = (npu_errno_t)fake_result_code;
	nw->result_value = fake_result_value;
	fake_result_ready = 0;
	return 1;
}

static int rejected_callback_ewouldblock(int msgid, struct npu_nw *nw)
{
	(void)msgid;
	(void)nw;
	return -EWOULDBLOCK;
}

static void reset_fixture(void)
{
	memset(&npu_proto_drv, 0, sizeof(npu_proto_drv));
	memset(&fake_power_waiter, 0, sizeof(fake_power_waiter));
	memset(&fake_mailbox_header, 0, sizeof(fake_mailbox_header));
	memset(&fake_sfr, 0, sizeof(fake_sfr));
	memset(fake_mailbox_ring, 0, sizeof(fake_mailbox_ring));
	interface.mbox_hdr = &fake_mailbox_header;
	interface.sfr = &fake_sfr;
	fake_mailbox_header.h2fctrl[0].sgmt_len = FAKE_RING_BYTES;
	fake_debug_dump_count = 0;
	fake_publish_begin_count = 0;
	fake_publish_authorize_count = 0;
	fake_publish_finish_count = 0;
	fake_publish_waiter_active = 1;
	fake_lsm_move_count = 0;
	fake_last_lsm_state = -1;
	fake_emergency_set_count = 0;
	fake_last_lsm_entry = NULL;
	fake_result_ready = 0;
	fake_result_msgid = -1;
	fake_result_code = 0;
	fake_result_value = 0;
	fake_reply_on_commit = 0;
	fake_queue_ready = 0;
	msgid_pool_init(&npu_proto_drv.msgid_pool);
	fake_mbox_ops.nw_post_request = nw_req_manager;
	fake_mbox_ops.nw_get_result = fake_nw_get_result;
	protodrv_mbox.npu_if_protodrv_mbox_ops = &fake_mbox_ops;
	protodr = &npu_proto_drv;
}

static void init_waiter_entry(struct proto_req_nw *entry, int tag)
{
	memset(entry, 0, sizeof(*entry));
	entry->state = REQUESTED;
	entry->nw.cmd = NPU_NW_CMD_POWER_CTL;
	entry->nw.uid = tag;
	entry->nw.session = NULL;
	entry->nw.notify_func = npu_session_save_power_result;
	entry->nw.param0 = (u32)(100 + tag);
	entry->nw.param1 = (u32)(200 + tag);
	entry->nw.npu_req_id = 0;
	entry->nw.result_code = NPU_NW_JUST_STARTED;
}

static int committed_records(void)
{
	return (int)(fake_mailbox_header.h2fctrl[0].wptr /
		(sizeof(struct message) + sizeof(struct command)));
}

static int occupied_count(const struct msgid_pool *pool)
{
	int count = 0;
	int index;
	for (index = 0; index < NPU_MAX_MSG_ID_CNT; index++)
		count += atomic_read(&pool->pool[index].occupied) != 0;
	return count;
}

static int run_requested_worker_once(struct proto_req_nw *entry)
{
	/* The actual LSM only invokes its REQUESTED callback for REQUESTED entries. */
	if (entry->state != REQUESTED)
		return 0;
	if (!entry->nw.npu_req_id) {
		fake_queued_nw = entry->nw;
		fake_queue_ready = 1;
		fake_next_request_id = (npu_req_id_t)(300 + entry->nw.uid);
		if (nw_mgmt_op_get_request(entry) <= 0)
			fail("actual request-queue adapter failed to assign POWER_CTL identity");
	}
	return run_actual_power_ctl_case(entry);
}

static void fail(const char *message)
{
	fprintf(stderr, "npu-publication-ownership-c: %s\n", message);
	exit(1);
}

static void expect(bool condition, const char *message)
{
	if (!condition)
		fail(message);
}

/* Exact pinned production C is emitted here by the Python test. */

static void test_ambiguous_publish_and_late_reply(void)
{
	struct proto_req_nw first, second;
	int first_id;
	int records_after_first;

	reset_fixture();
	init_waiter_entry(&first, 1);
	fake_sfr.grp[1].ms = 1;
	if (run_requested_worker_once(&first) != EXPECTED_FIRST_RESULT)
		fail("ambiguous publication returned an unexpected worker result");
	first_id = first.nw.msgid;
	records_after_first = committed_records();
	expect(first_id == 0 && records_after_first == 1 &&
	       fake_debug_dump_count == 1,
	       "actual ring producer must commit before actual bounded interrupt timeout");
	expect(fake_publish_finish_count == 1,
	       "synchronous POWER_CTL callback must finish the publication lease");

#if EXPECT_OWNERSHIP_PATCH
	expect(first.state == PROCESSING &&
	       atomic_read(&npu_proto_drv.msgid_pool.pool[first_id].occupied) == 1 &&
	       npu_proto_drv.msgid_pool.pool[first_id].ref == &first,
	       "patch must keep committed request and message ID in flight");
	expect(run_requested_worker_once(&first) == 0 &&
	       committed_records() == records_after_first,
	       "processing entry must not be automatically republished from REQUESTED");
#else
	expect(first.state == REQUESTED &&
	       atomic_read(&npu_proto_drv.msgid_pool.pool[first_id].occupied) == 0,
	       "baseline must recycle ID and leave ambiguous publication retryable");
#endif

	init_waiter_entry(&second, 2);
	fake_sfr.grp[1].ms = 0;
	expect(run_requested_worker_once(&second) == 1,
	       "second POWER_CTL request should publish successfully");
#if EXPECT_OWNERSHIP_PATCH
	expect(second.nw.msgid != first_id && second.state == PROCESSING,
	       "patch must allocate a distinct ID while first reply is unresolved");
#else
	expect(second.nw.msgid == first_id && second.state == PROCESSING,
	       "baseline must reuse unresolved ID for second request");
#endif

	fake_result_ready = 1;
	fake_result_msgid = first_id;
	fake_result_code = 0x1234;
	fake_result_value = 0x5678;
	expect(npu_protodrv_handler_nw_processing() == 1,
	       "actual response adapter and protocol processing handler must consume reply");
#if EXPECT_OWNERSHIP_PATCH
	expect(fake_last_lsm_entry == &first && first.state == COMPLETED &&
	       first.nw.result_code == 0x1234 &&
	       atomic_read(&npu_proto_drv.msgid_pool.pool[first_id].occupied) == 0,
	       "late response must complete original committed entry and release its ID");
	puts("PASS patched actual C: ambiguous commit retains original owner through late response");
#else
	expect(fake_last_lsm_entry == &second &&
	       second.state == COMPLETED && second.nw.result_code == 0x1234,
	       "baseline must reproduce late response misattribution after ID reuse");
	puts("BASELINE REPRO actual C: late POWER_CTL response is attributed to a different entry");
#endif
}

static void test_reply_ready_before_state_transition(void)
{
	struct proto_req_nw entry;
	int response_count;

	reset_fixture();
	init_waiter_entry(&entry, 3);
	fake_sfr.grp[1].ms = 1;
	fake_reply_on_commit = 1;
	/* AST's actual task order checks PROCESSING before it handles REQUESTED. */
	expect(npu_protodrv_handler_nw_processing() == 0,
	       "the current AST pass should see no result before publication");
	(void)run_requested_worker_once(&entry);
	expect(fake_result_ready == 1,
	       "test firmware must make response available at committed wptr update");
#if EXPECT_OWNERSHIP_PATCH
	expect(entry.state == PROCESSING &&
	       npu_proto_drv.msgid_pool.pool[entry.nw.msgid].ref == &entry,
	       "request must enter PROCESSING before the next serialized AST result pass");
	response_count = npu_protodrv_handler_nw_processing();
	expect(response_count == 1 && entry.state == COMPLETED &&
	       fake_last_lsm_entry == &entry,
	       "actual processing worker must accept response made ready before transition");
	puts("PASS actual C: response ready at ring commit is consumed after serialized PROCESSING transition");
#else
	response_count = npu_protodrv_handler_nw_processing();
	expect(response_count == 0 && entry.state == REQUESTED &&
	       occupied_count(&npu_proto_drv.msgid_pool) == 0,
	       "baseline must drop immediate response after recycling its committed ID");
	puts("BASELINE REPRO actual C: pre-transition response loses its recycled message-ID owner");
#endif
}

static void test_precommit_full_ring_retry_and_callback_provenance(void)
{
	struct proto_req_nw entry;
	struct mailbox_ctrl *ctrl;
	int ret;

	reset_fixture();
	init_waiter_entry(&entry, 4);
	ctrl = &fake_mailbox_header.h2fctrl[0];
	ctrl->wptr = FAKE_RING_BYTES;
	ctrl->rptr = 0;
	ret = run_requested_worker_once(&entry);
	expect(ret == 0 && ctrl->wptr == FAKE_RING_BYTES &&
	       fake_debug_dump_count == 0 && entry.state == REQUESTED &&
	       occupied_count(&npu_proto_drv.msgid_pool) == 0,
	       "actual pre-commit -ERESOURCE must reclaim ID and remain retryable");
	ctrl->rptr = ctrl->wptr;
	expect(run_requested_worker_once(&entry) == 1 && entry.state == PROCESSING,
	       "full-ring retry must publish once ring capacity returns");
	puts("PASS actual C: -ERESOURCE remains a pre-commit retry and recycles only its ID");

	reset_fixture();
	init_waiter_entry(&entry, 5);
	fake_mbox_ops.nw_post_request = rejected_callback_ewouldblock;
	fake_sfr.grp[1].ms = 1;
	ret = run_requested_worker_once(&entry);
	expect(ret == 0 && entry.state == REQUESTED &&
	       fake_mailbox_header.h2fctrl[0].wptr == 0 &&
	       occupied_count(&npu_proto_drv.msgid_pool) == 0,
	       "generic -EWOULDBLOCK from a different callback must not imply ring commit");
	puts("PASS actual adapter: errno-only failure from non-hardware callback is reclaimed, not retained");
}

static void test_stucked_late_response_keeps_entry_owned(void)
{
	struct proto_req_nw entry;
	int id;

	reset_fixture();
	init_waiter_entry(&entry, 6);
	fake_sfr.grp[1].ms = 1;
	expect(run_requested_worker_once(&entry) == EXPECTED_FIRST_RESULT,
	       "POWER_CTL timeout case must execute");
#if EXPECT_OWNERSHIP_PATCH
	struct nw_result canceled_waiter_result;
	id = entry.nw.msgid;
	expect(entry.state == PROCESSING &&
	       atomic_read(&npu_proto_drv.msgid_pool.pool[id].occupied) == 1,
	       "unanswered committed request must retain the LSM entry while ID is live");
	entry.nw.result_code = NPU_CRITICAL_DRIVER(NPU_ERR_QUEUE_TIMEOUT);
	expect(is_stucked_req_nw(&entry),
	       "actual queue-timeout classifier must quarantine timed-out NW entry");
	actual_stucked_transition(&entry);
	expect(entry.state == STUCKED && fake_lsm_move_count == 2 &&
	       fake_last_lsm_entry == &entry && fake_last_lsm_state == STUCKED &&
	       fake_emergency_set_count == 1,
	       "actual completion transition must retain the timed-out request in STUCKED");
	fake_power_waiter.cookie = ((u64)entry.nw.param1 << 32) | entry.nw.param0;
	fake_power_waiter.req_id = entry.nw.npu_req_id;
	fake_power_waiter.cancelled = 1;
	fake_power_waiter.registered = 1;
	canceled_waiter_result.nw = entry.nw;
	canceled_waiter_result.nw.result_code = 0;
	expect(npu_session_save_power_result(NULL, canceled_waiter_result) == 0 &&
	       fake_power_waiter.done == 0 && fake_power_waiter.completion == 0 &&
	       entry.state == STUCKED &&
	       atomic_read(&npu_proto_drv.msgid_pool.pool[id].occupied) == 1,
	       "actual late callback must ignore canceled waiter without releasing retained msgid");
	fake_result_ready = 1;
	fake_result_msgid = id;
	fake_result_code = 0;
	fake_result_value = 9;
	expect(npu_protodrv_handler_nw_processing() == 0 &&
	       entry.state == STUCKED &&
	       atomic_read(&npu_proto_drv.msgid_pool.pool[id].occupied) == 0 &&
	       fake_lsm_move_count == 2 && fake_last_lsm_entry == &entry &&
	       fake_last_lsm_state == STUCKED && fake_emergency_set_count == 1,
	       "late reply after STUCKED must release only its ID, not free/reuse entry");
	puts("PASS actual source C: canceled waiter ignores late callback; STUCKED entry survives reply");
#else
	id = entry.nw.msgid;
	expect(entry.state == REQUESTED &&
	       atomic_read(&npu_proto_drv.msgid_pool.pool[id].occupied) == 0,
	       "baseline has no owned processing entry to quarantine after ambiguous timeout");
	expect(!is_stucked_req_nw(&entry),
	       "actual STUCKED classifier must leave the baseline unowned request unquarantined");
	puts("BASELINE REPRO actual C: ambiguous timeout leaves no owned processing lifetime");
#endif
}

static void test_fixed_pool_retention_bound(void)
{
	struct proto_req_nw entries[NPU_MAX_MSG_ID_CNT + 1];
	struct mailbox_ctrl *ctrl;
	int index;
	int records_before_exhaustion;

	reset_fixture();
	fake_sfr.grp[1].ms = 1;
	for (index = 0; index < NPU_MAX_MSG_ID_CNT; index++) {
		init_waiter_entry(&entries[index], 1000 + index);
		expect(run_requested_worker_once(&entries[index]) == EXPECTED_FIRST_RESULT,
		       "each unanswered ambiguous request must finish one publication callback");
#if EXPECT_OWNERSHIP_PATCH
		expect(entries[index].state == PROCESSING,
		       "retained publication must leave REQUESTED and prevent replay");
#else
		expect(entries[index].state == REQUESTED,
		       "baseline ambiguous callback leaves request retryable");
#endif
	}
	ctrl = &fake_mailbox_header.h2fctrl[0];
	records_before_exhaustion = committed_records();
#if EXPECT_OWNERSHIP_PATCH
	expect(occupied_count(&npu_proto_drv.msgid_pool) == NPU_MAX_MSG_ID_CNT &&
	       records_before_exhaustion == NPU_MAX_MSG_ID_CNT,
	       "retention must be bounded by fixed msgid capacity and actual committed records");
#endif
	init_waiter_entry(&entries[NPU_MAX_MSG_ID_CNT], 9000);
#if EXPECT_OWNERSHIP_PATCH
	expect(run_requested_worker_once(&entries[NPU_MAX_MSG_ID_CNT]) == 0 &&
	       entries[NPU_MAX_MSG_ID_CNT].state == REQUESTED &&
	       committed_records() == records_before_exhaustion &&
	       occupied_count(&npu_proto_drv.msgid_pool) == NPU_MAX_MSG_ID_CNT,
	       "pool exhaustion must reject new request without another mailbox commit");
	puts("PASS actual C: unresolved ownership is capped at 64 IDs; exhaustion refuses another ring commit");
#else
	expect(run_requested_worker_once(&entries[NPU_MAX_MSG_ID_CNT]) == 0 &&
	       entries[NPU_MAX_MSG_ID_CNT].state == REQUESTED &&
	       committed_records() == records_before_exhaustion + 1 &&
	       occupied_count(&npu_proto_drv.msgid_pool) == 0,
	       "baseline should demonstrate another committed write after recycling its only ID");
	puts("BASELINE actual C: unowned requests can repeatedly commit while recycling one ID");
#endif
}

int main(void)
{
	(void)actual_stucked_transition;
	expect(sizeof(struct message) == 24 && sizeof(struct command) == 24,
	       "pinned v10 mailbox ABI must retain exact 24-byte records");
	test_ambiguous_publish_and_late_reply();
	test_reply_ready_before_state_transition();
	test_precommit_full_ring_retry_and_callback_provenance();
	test_stucked_late_response_keeps_entry_owned();
	test_fixed_pool_retention_bound();
#if EXPECT_OWNERSHIP_PATCH
	puts("LIMIT exact source C plus bounded host MMIO/list/worker shims; no kernel or device lifetime claim");
#else
	puts("LIMIT baseline exact source C plus bounded host shims; reproductions are not kernel/device evidence");
#endif
	return 0;
}
