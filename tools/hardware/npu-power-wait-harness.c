/*
 * Host harness for the exact POWER_CTL waiter helpers extracted from
 * npu-session-lifecycle-fix.patch by test-npu-session-lifecycle.py.
 *
 * The shims below model only Linux list, spinlock, and completion primitives
 * needed by those helpers. They do not model IRQ context, scheduler behavior,
 * memory ordering in the kernel, mailbox MMIO, device references, or module
 * lifetime. This is a C execution test of production helper text, not a
 * kernel-runtime test.
 */
#include <errno.h>
#include <pthread.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <stdatomic.h>

typedef uint64_t u64;
typedef uint32_t u32;
typedef unsigned int npu_req_id_t;
typedef int npu_errno_t;
typedef uint64_t atomic64_t;

#define ATOMIC64_INIT(value) (value)

struct list_head {
	struct list_head *next;
	struct list_head *prev;
};

#define INIT_LIST_HEAD(head) do { \
	(head)->next = (head); \
	(head)->prev = (head); \
} while (0)

#define LIST_HEAD(name) \
	struct list_head name = { &(name), &(name) }

#define container_of(ptr, type, member) \
	((type *)((char *)(ptr) - offsetof(type, member)))

#define list_for_each_entry(pos, head, member) \
	for ((pos) = container_of((head)->next, __typeof__(*(pos)), member); \
	     &(pos)->member != (head); \
	     (pos) = container_of((pos)->member.next, __typeof__(*(pos)), member))

static inline void list_add_tail(struct list_head *entry, struct list_head *head)
{
	entry->prev = head->prev;
	entry->next = head;
	head->prev->next = entry;
	head->prev = entry;
}

static inline void list_del_init(struct list_head *entry)
{
	entry->prev->next = entry->next;
	entry->next->prev = entry->prev;
	INIT_LIST_HEAD(entry);
}

static inline int list_empty(const struct list_head *head)
{
	return head->next == head;
}

struct completion {
	pthread_mutex_t lock;
	pthread_cond_t changed;
	unsigned int done;
};

static inline void init_completion(struct completion *completion)
{
	pthread_mutex_init(&completion->lock, NULL);
	pthread_cond_init(&completion->changed, NULL);
	completion->done = 0;
}

static inline void reinit_completion(struct completion *completion)
{
	pthread_mutex_lock(&completion->lock);
	completion->done = 0;
	pthread_mutex_unlock(&completion->lock);
}

static inline void complete(struct completion *completion)
{
	pthread_mutex_lock(&completion->lock);
	completion->done++;
	pthread_cond_broadcast(&completion->changed);
	pthread_mutex_unlock(&completion->lock);
}

static inline void complete_all(struct completion *completion)
{
	pthread_mutex_lock(&completion->lock);
	completion->done = UINT32_MAX;
	pthread_cond_broadcast(&completion->changed);
	pthread_mutex_unlock(&completion->lock);
}

static inline void wait_for_completion(struct completion *completion)
{
	pthread_mutex_lock(&completion->lock);
	while (!completion->done)
		pthread_cond_wait(&completion->changed, &completion->lock);
	pthread_mutex_unlock(&completion->lock);
}

typedef pthread_mutex_t spinlock_t;
#define DEFINE_SPINLOCK(name) pthread_mutex_t name = PTHREAD_MUTEX_INITIALIZER
#define spin_lock_irqsave(lock, flags) do { \
	(void)(flags); \
	pthread_mutex_lock(lock); \
} while (0)
#define spin_unlock_irqrestore(lock, flags) do { \
	(void)(flags); \
	pthread_mutex_unlock(lock); \
} while (0)

struct npu_session;
struct npu_result_nw {
	u32 param0;
	u32 param1;
	npu_req_id_t npu_req_id;
};

struct nw_result {
	struct npu_result_nw nw;
	npu_errno_t result_code;
};

/* Exact production C is emitted here by the Python test; do not hand-copy it. */
#include "npu-power-wait-extracted.inc"

struct drain_thread_state {
	struct npu_power_waiter *waiter;
	atomic_bool returned;
};

static void *drain_thread(void *opaque)
{
	struct drain_thread_state *state = opaque;

	npu_power_wait_cancel_and_drain(state->waiter);
	atomic_store(&state->returned, true);
	return NULL;
}

static void fail(const char *message)
{
	fprintf(stderr, "npu-power-wait-harness: %s\n", message);
	exit(1);
}

static void expect(bool condition, const char *message)
{
	if (!condition)
		fail(message);
}

static void sleep_ms(unsigned int ms)
{
	struct timespec delay = {
		.tv_sec = ms / 1000,
		.tv_nsec = (long)(ms % 1000) * 1000000L,
	};
	nanosleep(&delay, NULL);
}

static bool wait_until_cancelled(struct npu_power_waiter *waiter)
{
	unsigned int i;

	for (i = 0; i < 1000; i++) {
		unsigned long flags = 0;
		bool cancelled;

		spin_lock_irqsave(&npu_power_waiters_lock, flags);
		cancelled = waiter->cancelled;
		spin_unlock_irqrestore(&npu_power_waiters_lock, flags);
		if (cancelled)
			return true;
		sleep_ms(1);
	}
	return false;
}

static void init_waiter(struct npu_power_waiter *waiter, u64 cookie,
			npu_req_id_t req_id)
{
	unsigned long flags = 0;

	memset(waiter, 0, sizeof(*waiter));
	INIT_LIST_HEAD(&waiter->list);
	init_completion(&waiter->completion);
	init_completion(&waiter->publish_done);
	waiter->cookie = cookie;
	spin_lock_irqsave(&npu_power_waiters_lock, flags);
	list_add_tail(&waiter->list, &npu_power_waiters);
	spin_unlock_irqrestore(&npu_power_waiters_lock, flags);
	expect(npu_session_power_wait_assign_req_id(cookie, req_id) == 0,
	       "request ID assignment failed");
}

static void remove_waiter(struct npu_power_waiter *waiter)
{
	unsigned long flags = 0;

	spin_lock_irqsave(&npu_power_waiters_lock, flags);
	if (!list_empty(&waiter->list))
		list_del_init(&waiter->list);
	spin_unlock_irqrestore(&npu_power_waiters_lock, flags);
}

static bool waiter_is_registered(struct npu_power_waiter *waiter)
{
	unsigned long flags = 0;
	bool found;

	spin_lock_irqsave(&npu_power_waiters_lock, flags);
	found = npu_power_waiter_find(waiter->cookie) == waiter;
	spin_unlock_irqrestore(&npu_power_waiters_lock, flags);
	return found;
}

static void start_drain(struct drain_thread_state *state, pthread_t *thread,
		struct npu_power_waiter *waiter)
{
	state->waiter = waiter;
	atomic_init(&state->returned, false);
	expect(pthread_create(thread, NULL, drain_thread, state) == 0,
	       "failed to start cancellation/drain thread");
	expect(wait_until_cancelled(waiter), "drain did not cancel waiter");
}

static struct nw_result make_response(u64 cookie, npu_req_id_t req_id,
				      npu_errno_t result_code)
{
	struct nw_result result = { 0 };

	result.nw.param0 = (u32)cookie;
	result.nw.param1 = (u32)(cookie >> 32);
	result.nw.npu_req_id = req_id;
	result.result_code = result_code;
	return result;
}

static void test_stalled_publication_and_late_callback(void)
{
	struct npu_power_waiter waiter;
	struct drain_thread_state drain = { 0 };
	pthread_t thread;
	struct nw_result response;
	unsigned long flags = 0;
	bool done, publishing, committed;

	init_waiter(&waiter, 0x1020304050607080ULL, 101);
	expect(npu_session_power_wait_begin_publish(waiter.cookie, waiter.req_id),
	       "publisher lease did not begin");
	expect(npu_session_power_wait_authorize_publish(waiter.cookie, waiter.req_id),
	       "publisher lease did not authorize");
	start_drain(&drain, &thread, &waiter);

	/* The actual helper must retain the stack waiter while the publisher stalls. */
	sleep_ms(30);
	expect(!atomic_load(&drain.returned),
	       "caller drain returned while synchronous publication was stalled");
	expect(waiter_is_registered(&waiter),
	       "stalled publication lost its registered waiter");
	spin_lock_irqsave(&npu_power_waiters_lock, flags);
	done = waiter.done;
	publishing = waiter.publishing;
	committed = waiter.publish_committed;
	spin_unlock_irqrestore(&npu_power_waiters_lock, flags);
	expect(!done && publishing && committed,
	       "timeout must retain the committed publication lease");

	response = make_response(waiter.cookie, waiter.req_id, -EIO);
	npu_session_save_power_result(NULL, response);
	npu_session_save_power_result(NULL, response); /* duplicate/late response */
	spin_lock_irqsave(&npu_power_waiters_lock, flags);
	done = waiter.done;
	spin_unlock_irqrestore(&npu_power_waiters_lock, flags);
	expect(!done, "callback after cancellation must be ignored");

	/* Model the synchronous mailbox call returning after its stall. */
	npu_session_power_wait_finish_publish(waiter.cookie, waiter.req_id);
	expect(pthread_join(thread, NULL) == 0, "drain thread did not join");
	expect(atomic_load(&drain.returned), "drain did not finish after publisher return");
	spin_lock_irqsave(&npu_power_waiters_lock, flags);
	expect(waiter.cancelled && !waiter.publishing && !waiter.publish_committed,
	       "finish did not release the publication lease");
	spin_unlock_irqrestore(&npu_power_waiters_lock, flags);
	remove_waiter(&waiter);
	expect(!waiter_is_registered(&waiter), "terminal timeout left waiter registered");
	puts("PASS actual C helper: stalled publication retains waiter; late/duplicate callback ignored");
}

static void test_response_before_publication_finish(void)
{
	struct npu_power_waiter waiter;
	struct drain_thread_state drain = { 0 };
	pthread_t thread;
	struct nw_result response;
	unsigned long flags = 0;
	bool done, publishing;

	init_waiter(&waiter, 0x8877665544332211ULL, 202);
	expect(npu_session_power_wait_begin_publish(waiter.cookie, waiter.req_id),
	       "response-race publisher lease did not begin");
	expect(npu_session_power_wait_authorize_publish(waiter.cookie, waiter.req_id),
	       "response-race publisher lease did not authorize");
	response = make_response(waiter.cookie, waiter.req_id, -EIO);
	npu_session_save_power_result(NULL, response);
	npu_session_save_power_result(NULL, response); /* duplicate callback */
	spin_lock_irqsave(&npu_power_waiters_lock, flags);
	done = waiter.done;
	spin_unlock_irqrestore(&npu_power_waiters_lock, flags);
	expect(done, "matching response before publication finish was lost");
	expect(waiter.result.result_code == response.result_code &&
	       waiter.result.nw.npu_req_id == response.nw.npu_req_id,
	       "matching response result was not retained");

	start_drain(&drain, &thread, &waiter);
	sleep_ms(30);
	expect(!atomic_load(&drain.returned),
	       "response completion let caller leave before publisher finish");
	npu_session_power_wait_finish_publish(waiter.cookie, waiter.req_id);
	expect(pthread_join(thread, NULL) == 0, "response-race drain did not join");
	expect(atomic_load(&drain.returned), "response-race drain did not finish");
	spin_lock_irqsave(&npu_power_waiters_lock, flags);
	publishing = waiter.publishing;
	spin_unlock_irqrestore(&npu_power_waiters_lock, flags);
	expect(!publishing, "response-race publication lease remained active");
	remove_waiter(&waiter);
	puts("PASS actual C helper: response-before-finish and duplicate callback remain lifetime-safe");
}

static void test_cancel_revokes_uncommitted_publication(void)
{
	struct npu_power_waiter waiter;
	struct drain_thread_state drain = { 0 };
	pthread_t thread;
	struct nw_result response;

	init_waiter(&waiter, 0xabcdef0102030405ULL, 303);
	expect(npu_session_power_wait_begin_publish(waiter.cookie, waiter.req_id),
	       "uncommitted publisher lease did not begin");
	start_drain(&drain, &thread, &waiter);
	response = make_response(waiter.cookie, waiter.req_id, 0);
	npu_session_save_power_result(NULL, response);
	expect(!npu_session_power_wait_authorize_publish(waiter.cookie, waiter.req_id),
	       "cancellation did not revoke an uncommitted publication");
	/* Production worker calls finish on the failed-authorization path too. */
	npu_session_power_wait_finish_publish(waiter.cookie, waiter.req_id);
	expect(pthread_join(thread, NULL) == 0, "revoked-publication drain did not join");
	expect(atomic_load(&drain.returned), "revoked publication did not release waiter");
	expect(!waiter.done,
	       "late callback after cancellation revived the uncommitted waiter");
	remove_waiter(&waiter);
	puts("PASS actual C helper: cancellation revokes uncommitted publish; failed authorization drains");
}

int main(void)
{
	npu_power_wait_cookie = 0; /* Keep the production cookie declaration linked. */
	test_stalled_publication_and_late_callback();
	test_response_before_publication_finish();
	test_cancel_revokes_uncommitted_publication();
	puts("LIMIT: pthread/list/completion shims do not validate kernel teardown, device refs, or module lifetime");
	return 0;
}
