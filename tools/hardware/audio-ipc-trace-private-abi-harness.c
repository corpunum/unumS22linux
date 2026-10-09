/* Host-only support and assertions composed with the pinned PM harness. */
/* ABI_TRACE_STATE_BEGIN */
#define __rcu
#define GFP_KERNEL 0
#define rcu_dereference(pointer) (pointer)
#define rcu_access_pointer(pointer) (pointer)
#define rcu_dereference_protected(pointer, condition) (pointer)
#define rcu_assign_pointer(pointer, value) ((pointer) = (value))
#define DEFINE_SPINLOCK(name) spinlock_t name = PTHREAD_MUTEX_INITIALIZER

static pthread_rwlock_t g_abi_rcu_lock = PTHREAD_RWLOCK_INITIALIZER;
static int g_abi_synchronize_entered;

static void rcu_read_lock(void)
{
	pthread_rwlock_rdlock(&g_abi_rcu_lock);
}

static void rcu_read_unlock(void)
{
	pthread_rwlock_unlock(&g_abi_rcu_lock);
}

static void synchronize_rcu(void)
{
	__sync_add_and_fetch(&g_abi_synchronize_entered, 1);
	pthread_rwlock_wrlock(&g_abi_rcu_lock);
	pthread_rwlock_unlock(&g_abi_rcu_lock);
}

static void *devm_kzalloc(struct device *dev, size_t size, int flags)
{
	(void)flags;
	if (dev->fail_devm_allocation)
		return NULL;
	dev->devm_allocation = calloc(1, size);
	return dev->devm_allocation;
}

static int devm_add_action_or_reset(struct device *dev,
		void (*action)(void *), void *argument)
{
	if (dev->fail_devm_action || dev->devm_action) {
		action(argument);
		return -ENOMEM;
	}
	dev->devm_action = action;
	dev->devm_action_argument = argument;
	return 0;
}

static void abi_release_devm(struct device *dev)
{
	if (dev->devm_action)
		dev->devm_action(dev->devm_action_argument);
	free(dev->devm_allocation);
	dev->devm_action = NULL;
	dev->devm_action_argument = NULL;
	dev->devm_allocation = NULL;
}

#define ABI_TRACE_CAPACITY 32
struct abi_trace_event {
	u64 sequence;
	int channel;
	int result;
};

static pthread_mutex_t g_abi_trace_lock = PTHREAD_MUTEX_INITIALIZER;
static bool g_abi_queue_enabled;
static bool g_abi_send_enabled;
static bool g_duplicate_trace_queue_enabled;
static int g_duplicate_queue_trace_calls;
static int g_queue_work_under_lock_calls;
static int g_flush_work_under_lock_calls;
static struct abi_trace_event g_abi_queue_events[ABI_TRACE_CAPACITY];
static struct abi_trace_event g_abi_send_events[ABI_TRACE_CAPACITY];
static int g_abi_queue_event_count;
static int g_abi_send_event_count;
static pthread_barrier_t g_abi_send_barrier;
static bool g_abi_send_barrier_enabled;

static void abi_reset_trace_events(void)
{
	pthread_mutex_lock(&g_abi_trace_lock);
	memset(g_abi_queue_events, 0, sizeof(g_abi_queue_events));
	memset(g_abi_send_events, 0, sizeof(g_abi_send_events));
	g_abi_queue_event_count = 0;
	g_abi_send_event_count = 0;
	pthread_mutex_unlock(&g_abi_trace_lock);
}

static bool trace_abox_pcm_trigger_queue_enabled(void)
{
	return g_abi_queue_enabled || g_duplicate_trace_queue_enabled;
}

static bool trace_abox_pcm_trigger_send_enabled(void)
{
	return g_abi_send_enabled;
}

static void trace_abox_pcm_trigger_queue(u64 sequence, u64 mono_ns,
		int ipc_id, int channel, int message_type, int result,
		int attempt, bool atomic, bool sync)
{
	(void)mono_ns;
	(void)ipc_id;
	(void)message_type;
	(void)attempt;
	(void)atomic;
	(void)sync;
	pthread_mutex_lock(&g_abi_trace_lock);
	if (g_duplicate_trace_queue_enabled)
		__sync_add_and_fetch(&g_duplicate_queue_trace_calls, 1);
	if (g_abi_queue_event_count < ABI_TRACE_CAPACITY) {
		struct abi_trace_event *event =
			&g_abi_queue_events[g_abi_queue_event_count++];
		event->sequence = sequence;
		event->channel = channel;
		event->result = result;
	}
	pthread_mutex_unlock(&g_abi_trace_lock);
}

static void trace_abox_pcm_trigger_send(u64 sequence, u64 mono_ns,
		int ipc_id, int channel, int message_type, int result)
{
	(void)mono_ns;
	(void)ipc_id;
	(void)message_type;
	pthread_mutex_lock(&g_abi_trace_lock);
	if (g_abi_send_event_count < ABI_TRACE_CAPACITY) {
		struct abi_trace_event *event =
			&g_abi_send_events[g_abi_send_event_count++];
		event->sequence = sequence;
		event->channel = channel;
		event->result = result;
	}
	pthread_mutex_unlock(&g_abi_trace_lock);
}

static void abi_wait_before_send(void)
{
	if (g_abi_send_barrier_enabled)
		pthread_barrier_wait(&g_abi_send_barrier);
}
/* ABI_TRACE_STATE_END */

/* ABI_SCENARIOS_BEGIN */
/* ABI_DUPLICATE_SCENARIOS */
struct abox_ipc_without_trace_sequence {
	struct device *dev;
	int hw_irq;
	unsigned long long put_time;
	unsigned long long get_time;
	size_t size;
	ABOX_IPC_MSG msg;
};

static void abi_init_data(struct abox_data *data, struct device *dev)
{
	memset(data, 0, sizeof(*data));
	memset(dev, 0, sizeof(*dev));
	pthread_mutex_init(&data->ipc_queue_lock, NULL);
	data->dev = dev;
	data->ipc_workqueue = (struct workqueue_struct *)data;
	dev->driver_data = data;
	dev->runtime_active = true;
}

static void abi_destroy_data(struct abox_data *data, struct device *dev)
{
	abi_release_devm(dev);
	pthread_mutex_destroy(&data->ipc_queue_lock);
}

static int abi_queue_trigger(struct device *dev, int channel,
		bool *enabled, bool start)
{
	return abox_request_pcm_trigger_ipc(dev, channel, false, enabled, start);
}

static void abi_reset_world_preserving_failures(void)
{
	int previous_failures = g_failures;

	reset_world();
	g_failures += previous_failures;
}

static int abi_queue_get_with_sequence(struct abox_data *data,
		struct abox_ipc *ipc, u64 *trace_sequence)
{
#ifdef EXPECT_BASELINE_PRIVATE_ABI_BUG
	int ret = abox_ipc_queue_get(data, ipc);

	*trace_sequence = ipc->trace_seq;
	return ret;
#else
	return abox_ipc_queue_get(data, ipc, trace_sequence);
#endif
}

static void test_abi_visible_layout(void)
{
#ifdef EXPECT_BASELINE_PRIVATE_ABI_BUG
	CHECK(sizeof(struct abox_ipc) ==
			sizeof(struct abox_ipc_without_trace_sequence),
		"baseline struct abox_ipc preserves its exported ABI size");
#else
	CHECK(sizeof(struct abox_ipc) ==
			sizeof(struct abox_ipc_without_trace_sequence),
		"private trace metadata restores the ABI-visible IPC layout");
#endif
}

static bool trace_pair_matches(u64 sequence, int channel)
{
	int index;
	bool found = false;

	pthread_mutex_lock(&g_abi_trace_lock);
	for (index = 0; index < g_abi_send_event_count; ++index) {
		if (g_abi_send_events[index].sequence == sequence &&
				g_abi_send_events[index].channel == channel)
			found = true;
	}
	pthread_mutex_unlock(&g_abi_trace_lock);
	return found;
}

static void *abi_run_worker(void *argument)
{
	struct abox_data *data = argument;

	abox_process_ipc(&data->ipc_work);
	return NULL;
}

static void test_two_owner_worker_correlation(void)
{
	struct abox_data first;
	struct abox_data second;
	struct device first_dev;
	struct device second_dev;
	bool first_enabled = false;
	bool second_enabled = false;
	pthread_t first_thread;
	pthread_t second_thread;
	u64 first_sequence;
	u64 second_sequence;

	abi_init_data(&first, &first_dev);
	abi_init_data(&second, &second_dev);
#ifndef EXPECT_BASELINE_PRIVATE_ABI_BUG
	CHECK(abox_ipc_trace_owner_register(&first_dev, &first) == 0 &&
			abox_ipc_trace_owner_register(&second_dev, &second) == 0,
		"different ABOX owners receive distinct private sidecars");
#endif
	abi_reset_trace_events();
	g_abi_queue_enabled = true;
	g_abi_send_enabled = true;
	g_resume_result = 1;
	CHECK(abi_queue_trigger(&first_dev, 101, &first_enabled, true) == 0 &&
			abi_queue_trigger(&second_dev, 202, &second_enabled, true) == 0,
		"two owners enqueue independent PCM triggers");
	CHECK(g_abi_queue_event_count == 2,
		"two-owner setup records both queue tracepoints");
	first_sequence = g_abi_queue_events[0].sequence;
	second_sequence = g_abi_queue_events[1].sequence;
	CHECK(first_sequence != 0 && second_sequence != 0 &&
			first_sequence != second_sequence,
		"trace IDs are unique across simultaneous ABOX owners");
	pthread_barrier_init(&g_abi_send_barrier, NULL, 2);
	g_abi_send_barrier_enabled = true;
	pthread_create(&first_thread, NULL, abi_run_worker, &first);
	pthread_create(&second_thread, NULL, abi_run_worker, &second);
	pthread_join(first_thread, NULL);
	pthread_join(second_thread, NULL);
	g_abi_send_barrier_enabled = false;
	pthread_barrier_destroy(&g_abi_send_barrier);
#ifdef EXPECT_BASELINE_PRIVATE_ABI_BUG
	CHECK(trace_pair_matches(first_sequence, 101) &&
			trace_pair_matches(second_sequence, 202),
		"baseline concurrent workers preserve both message/trace pairs");
#else
	CHECK(g_abi_send_event_count == 2 &&
			trace_pair_matches(first_sequence, 101) &&
			trace_pair_matches(second_sequence, 202),
		"invocation-local worker scratch preserves both owner message/trace pairs");
#endif
	abi_destroy_data(&first, &first_dev);
	abi_destroy_data(&second, &second_dev);
}

#ifndef EXPECT_BASELINE_PRIVATE_ABI_BUG
static u64 abi_read_private_slot(struct abox_data *data, size_t slot)
{
	struct abox_ipc_trace_owner *owner;
	unsigned long flags;
	u64 sequence = 0;

	rcu_read_lock();
	owner = abox_ipc_trace_owner_find(data);
	if (owner) {
		spin_lock_irqsave(&data->ipc_queue_lock, flags);
		sequence = owner->trace_seq[slot];
		spin_unlock_irqrestore(&data->ipc_queue_lock, flags);
	}
	rcu_read_unlock();
	return sequence;
}

#ifdef MUTATION_OMIT_ZERO_OVERWRITE
static void abi_seed_private_slot(struct abox_data *data, size_t slot,
		u64 sequence)
{
	struct abox_ipc_trace_owner *owner;
	unsigned long flags;

	rcu_read_lock();
	owner = abox_ipc_trace_owner_find(data);
	if (owner) {
		spin_lock_irqsave(&data->ipc_queue_lock, flags);
		owner->trace_seq[slot] = sequence;
		spin_unlock_irqrestore(&data->ipc_queue_lock, flags);
	}
	rcu_read_unlock();
}
#endif
#endif

static void test_actual_queue_get_zero_and_physical_slot_reuse(void)
{
	struct abox_ipc dequeued = {0};
	ABOX_IPC_MSG generic = {0};
	bool enabled = false;
	u64 first_sequence;
	u64 dequeued_sequence;
	int get_result;
	int index;
	int send_before;

	abi_reset_world_preserving_failures();
	abi_reset_trace_events();
#ifndef EXPECT_BASELINE_PRIVATE_ABI_BUG
	CHECK(abox_ipc_trace_owner_register(&g_abox_dev, &g_abox) == 0,
		"physical-slot test registers its private owner");
#endif
	g_abi_queue_enabled = true;
	g_abi_send_enabled = true;
	g_resume_result = 1;
	CHECK(abi_queue_trigger(&g_abox_dev, 61, &enabled, true) == 0,
		"first trigger occupies physical IPC ring slot zero");
	first_sequence = g_abi_queue_events[0].sequence;
	get_result = abi_queue_get_with_sequence(&g_abox, &dequeued,
			&dequeued_sequence);
	CHECK(get_result == 0 && dequeued_sequence == first_sequence &&
			dequeued.msg.ipcid == IPC_PCMPLAYBACK &&
			dequeued.msg.msg.pcmtask.channel_id == 61,
		"actual queue_get returns the first trigger sequence and message");
#ifndef EXPECT_BASELINE_PRIVATE_ABI_BUG
	CHECK(abi_read_private_slot(&g_abox, 0) == 0,
		"actual queue_get clears the consumed private sequence slot");
#endif

	/* Drain entries through the actual ring until producer/consumer wrap to 0. */
	g_abi_queue_enabled = false;
	g_abi_send_enabled = false;
	generic.ipcid = IPC_SYSTEM;
	for (index = 0; index < ABOX_IPC_QUEUE_SIZE - 1; ++index) {
		dequeued = (struct abox_ipc){0};
		dequeued_sequence = UINT64_MAX;
		CHECK(abox_request_ipc(&g_abox_dev, generic.ipcid, &generic,
				sizeof(generic), 0, 0) == 0,
			"generic FIFO advance is accepted while wrapping the ring");
		get_result = abi_queue_get_with_sequence(&g_abox, &dequeued,
				&dequeued_sequence);
		CHECK(get_result == 0 && dequeued_sequence == 0 &&
				dequeued.msg.ipcid == IPC_SYSTEM,
			"actual queue_get returns zero for generic IPC");
	}
	CHECK(g_abox.ipc_queue_start == 0 && g_abox.ipc_queue_end == 0,
		"actual producer and consumer wrap back to physical slot zero");

#ifdef MUTATION_OMIT_ZERO_OVERWRITE
	abi_seed_private_slot(&g_abox, 0, UINT64_C(0x5a5a));
#endif
	/* Both tracepoints are off at enqueue; this PCM message's seq is zero. */
	CHECK(abi_queue_trigger(&g_abox_dev, 62, &enabled, false) == 0,
		"zero-sequence PCM STOP reuses slot zero after a full ring wrap");
#ifndef EXPECT_BASELINE_PRIVATE_ABI_BUG
	CHECK(abi_read_private_slot(&g_abox, 0) == 0,
		"zero-sequence put overwrites reused private metadata before dequeue");
#endif
	g_abi_send_enabled = true;
	send_before = g_abi_send_event_count;
	dequeued = (struct abox_ipc){0};
	dequeued_sequence = UINT64_MAX;
	get_result = abi_queue_get_with_sequence(&g_abox, &dequeued,
			&dequeued_sequence);
	if (get_result == 0)
		__abox_process_ipc(dequeued.dev, &g_abox, dequeued.hw_irq,
				&dequeued.msg, dequeued.size, dequeued_sequence);
	CHECK(get_result == 0 && dequeued_sequence == 0 &&
			g_abi_send_event_count == send_before &&
			dequeued.msg.msg.pcmtask.channel_id == 62,
		"actual put zero-overwrites the reused slot before traced dequeue");
	abi_destroy_data(&g_abox, &g_abox_dev);
}

#ifndef EXPECT_BASELINE_PRIVATE_ABI_BUG
static void test_queue_send_trace_and_slot_clear(void)
{
	bool enabled = false;
	ABOX_IPC_MSG generic = {0};
	u64 first_sequence;
	int send_before;

	abi_reset_world_preserving_failures();
	abi_reset_trace_events();
	CHECK(abox_ipc_trace_owner_register(&g_abox_dev, &g_abox) == 0,
		"probe-equivalent registration creates private trace metadata");
	g_abi_queue_enabled = true;
	g_abi_send_enabled = true;
	g_resume_result = 1;
	CHECK(abi_queue_trigger(&g_abox_dev, 31, &enabled, true) == 0,
		"FE-style trigger enters actual queue with private trace ID");
	CHECK(g_abi_queue_event_count == 1 &&
			g_abi_queue_events[0].channel == 31 &&
			g_abi_queue_events[0].sequence != 0,
		"successful PCM insertion retains queue trace correlation");
	first_sequence = g_abi_queue_events[0].sequence;
	abox_process_ipc(&g_abox.ipc_work);
	CHECK(g_abi_send_event_count == 1 &&
			g_abi_send_events[0].sequence == first_sequence &&
			g_abi_send_events[0].channel == 31,
		"queue metadata reaches the matching actual worker send");

	generic.ipcid = IPC_SYSTEM;
	generic.task_id = 9;
	send_before = g_abi_send_event_count;
	CHECK(abox_request_ipc(&g_abox_dev, generic.ipcid, &generic,
			sizeof(generic), 0, 0) == 0,
		"generic IPC is enqueued through the actual scheduling helper");
	abox_process_ipc(&g_abox.ipc_work);
	CHECK(g_abi_send_event_count == send_before && queue_depth() == 0,
		"generic IPC worker send is not treated as a PCM tracepoint");

	g_abi_queue_enabled = false;
	g_abi_send_enabled = true;
	CHECK(abi_queue_trigger(&g_abox_dev, 32, &enabled, false) == 0,
		"send-only trace enablement still queues a correlated STOP");
	CHECK(g_abi_queue_event_count == 1,
		"send-only tracing emits no queue marker");
	abox_process_ipc(&g_abox.ipc_work);
	CHECK(g_abi_send_event_count == send_before + 1 &&
			g_abi_send_events[send_before].channel == 32,
		"send-only tracing retains its sequence through dequeue");

	g_abi_queue_enabled = true;
	g_abi_send_enabled = false;
	CHECK(abi_queue_trigger(&g_abox_dev, 33, &enabled, true) == 0,
		"queue-only trace enablement stores a sequence for the accepted START");
	CHECK(g_abi_queue_event_count == 2 &&
			g_abi_queue_events[1].channel == 33,
		"queue-only tracing emits its queue marker");
	send_before = g_abi_send_event_count;
	abox_process_ipc(&g_abox.ipc_work);
	CHECK(g_abi_send_event_count == send_before,
		"disabled send tracepoint emits no send marker and still clears its slot");
	g_abi_send_enabled = true;
	CHECK(abox_request_ipc(&g_abox_dev, generic.ipcid, &generic,
			sizeof(generic), 0, 0) == 0,
		"generic reuse after send-tracing toggle has zero sequence");
	abox_process_ipc(&g_abox.ipc_work);
	CHECK(g_abi_send_event_count == send_before,
		"later send-tracing enablement cannot expose a cleared stale sequence");

	abi_destroy_data(&g_abox, &g_abox_dev);
}

static void test_missing_sidecar_degrades_only_diagnostics(bool fail_action)
{
	bool enabled = false;
	int ret;

	abi_reset_world_preserving_failures();
	abi_reset_trace_events();
	g_abox_dev.fail_devm_allocation = !fail_action;
	g_abox_dev.fail_devm_action = fail_action;
	ret = abox_ipc_trace_owner_register(&g_abox_dev, &g_abox);
	CHECK(ret == -ENOMEM,
		fail_action ?
		"devm action failure is returned without publishing an owner" :
		"sidecar allocation failure is returned without publishing an owner");
	g_abi_queue_enabled = true;
	g_abi_send_enabled = true;
	g_resume_result = 1;
	CHECK(abi_queue_trigger(&g_abox_dev, 41, &enabled, true) == 0 &&
			queue_depth() == 1,
		"missing diagnostics metadata does not reject functional IPC enqueue");
	CHECK(g_abi_queue_event_count == 0,
		"missing sidecar suppresses queue marker instead of fabricating correlation");
	abox_process_ipc(&g_abox.ipc_work);
	CHECK(g_send_calls == 1 && g_abi_send_event_count == 0 &&
			queue_depth() == 0,
		"missing sidecar preserves functional send and suppresses send marker");
	abi_destroy_data(&g_abox, &g_abox_dev);
}

static void test_retirement_and_same_address_reuse(void)
{
	bool enabled = false;
	u64 old_sequence;
	int sends_before;
	ABOX_IPC_MSG generic = {0};

	abi_reset_world_preserving_failures();
	abi_reset_trace_events();
	CHECK(abox_ipc_trace_owner_register(&g_abox_dev, &g_abox) == 0,
		"initial owner registration succeeds before retirement test");
	g_abi_queue_enabled = true;
	g_abi_send_enabled = true;
	g_resume_result = 1;
	CHECK(abi_queue_trigger(&g_abox_dev, 51, &enabled, true) == 0,
		"trigger is queued while old owner sidecar is registered");
	old_sequence = g_abi_queue_events[0].sequence;
	sends_before = g_send_calls;
	abox_ipc_trace_owner_unregister_data(&g_abox);
	abi_release_devm(&g_abox_dev);
	abox_process_ipc(&g_abox.ipc_work);
	CHECK(g_send_calls == sends_before + 1 && g_abi_send_event_count == 0,
		"queued IPC after owner retirement sends without stale correlation");

	CHECK(abox_ipc_trace_owner_register(&g_abox_dev, &g_abox) == 0,
		"same abox_data address receives a fresh zeroed owner sidecar");
	g_abi_queue_enabled = false;
	g_abi_send_enabled = true;
	generic.ipcid = IPC_SYSTEM;
	CHECK(abox_request_ipc(&g_abox_dev, generic.ipcid, &generic,
			sizeof(generic), 0, 0) == 0,
		"reused owner address accepts generic IPC without correlation");
	abox_process_ipc(&g_abox.ipc_work);
	CHECK(g_abi_send_event_count == 0 && old_sequence != 0,
		"reused owner cannot inherit the retired sidecar's sequence");
	abi_destroy_data(&g_abox, &g_abox_dev);
}

struct abi_rcu_lifetime_state {
	pthread_mutex_t lock;
	pthread_cond_t condition;
	struct abox_data *data;
	bool reader_ready;
	bool release_reader;
	bool unregister_done;
	bool reader_found_owner;
};

static void *abi_hold_owner_read_section(void *argument)
{
	struct abi_rcu_lifetime_state *state = argument;
	struct abox_ipc_trace_owner *owner;

	rcu_read_lock();
	owner = abox_ipc_trace_owner_find(state->data);
	pthread_mutex_lock(&state->lock);
	state->reader_found_owner = owner && owner->data == state->data;
	state->reader_ready = true;
	pthread_cond_broadcast(&state->condition);
	while (!state->release_reader)
		pthread_cond_wait(&state->condition, &state->lock);
	pthread_mutex_unlock(&state->lock);
	rcu_read_unlock();
	return NULL;
}

static void *abi_unregister_owner(void *argument)
{
	struct abi_rcu_lifetime_state *state = argument;

	abox_ipc_trace_owner_unregister_data(state->data);
	pthread_mutex_lock(&state->lock);
	state->unregister_done = true;
	pthread_cond_broadcast(&state->condition);
	pthread_mutex_unlock(&state->lock);
	return NULL;
}

static void test_rcu_retirement_waits_for_lookup(void)
{
	struct abox_data data;
	struct device dev;
	struct abi_rcu_lifetime_state state = {0};
	pthread_t reader;
	pthread_t remover;
	int sync_before = g_abi_synchronize_entered;

	abi_init_data(&data, &dev);
	CHECK(abox_ipc_trace_owner_register(&dev, &data) == 0,
		"lifetime test owner registers");
	state.data = &data;
	pthread_mutex_init(&state.lock, NULL);
	pthread_cond_init(&state.condition, NULL);
	pthread_create(&reader, NULL, abi_hold_owner_read_section, &state);
	pthread_mutex_lock(&state.lock);
	while (!state.reader_ready)
		pthread_cond_wait(&state.condition, &state.lock);
	pthread_mutex_unlock(&state.lock);
	pthread_create(&remover, NULL, abi_unregister_owner, &state);
	while (__sync_add_and_fetch(&g_abi_synchronize_entered, 0) == sync_before)
		sched_yield();
	pthread_mutex_lock(&state.lock);
	CHECK(!state.unregister_done,
		"owner retirement cannot finish while RCU lookup retains its sidecar");
	state.release_reader = true;
	pthread_cond_broadcast(&state.condition);
	pthread_mutex_unlock(&state.lock);
	pthread_join(reader, NULL);
	pthread_join(remover, NULL);
	CHECK(state.reader_found_owner && state.unregister_done,
		"RCU lookup completes before sidecar retirement returns");
	rcu_read_lock();
	CHECK(abox_ipc_trace_owner_find(&data) == NULL,
		"retired owner is no longer discoverable after synchronization");
	rcu_read_unlock();
	pthread_cond_destroy(&state.condition);
	pthread_mutex_destroy(&state.lock);
	abi_destroy_data(&data, &dev);
}
#endif

static void test_private_abi_scenarios(void)
{
#if defined(MUTATION_OMIT_ZERO_OVERWRITE) || defined(MUTATION_OMIT_CLEAR)
	test_actual_queue_get_zero_and_physical_slot_reuse();
#else
#ifndef EXPECT_BASELINE_PRIVATE_ABI_BUG
	test_missing_sidecar_degrades_only_diagnostics(false);
	test_missing_sidecar_degrades_only_diagnostics(true);
	test_retirement_and_same_address_reuse();
	test_rcu_retirement_waits_for_lookup();
	test_queue_send_trace_and_slot_clear();
#endif
	test_actual_queue_get_zero_and_physical_slot_reuse();
	test_abi_visible_layout();
	test_two_owner_worker_correlation();
#endif
}
/* ABI_SCENARIOS_END */
