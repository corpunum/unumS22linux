/* Scenarios appended to the pinned PM worker C template by the host test. */
static void reset_duplicate_world(void)
{
	int previous_failures = g_failures;

	reset_world();
	g_failures += previous_failures;
	g_duplicate_trace_queue_enabled = false;
	g_duplicate_queue_trace_calls = 0;
	g_queue_work_under_lock_calls = 0;
	g_flush_work_under_lock_calls = 0;
}

static void enqueue_mixed_fe_be_fifo(void)
{
	int work_before = g_queue_work_calls;
	int head;
	int next;

	CHECK(abox_rdma_trigger(&g_component, &g_substream,
			SNDRV_PCM_TRIGGER_START) == 0,
		"nonduplicate FE START retains its accepted enqueue behavior");
	CHECK(abox_rdma_mute_stream(&g_dai, 1,
			SNDRV_PCM_STREAM_PLAYBACK) == 0,
		"nonduplicate BE STOP retains its accepted enqueue behavior");
	head = g_abox.ipc_queue_start;
	next = (head + 1) % ABOX_IPC_QUEUE_SIZE;
	CHECK(queue_depth() == 2 && !g_dma.enabled &&
		g_abox.ipc_queue[head].msg.msg.pcmtask.param.trigger == 1 &&
		g_abox.ipc_queue[next].msg.msg.pcmtask.param.trigger == 0,
		"mixed nonduplicate FE/BE directions retain START then STOP FIFO state");
	CHECK(g_queue_work_calls == work_before + 2,
		"nonduplicate FE and BE requests each retain one queue-work call");
	CHECK(g_queue_work_under_lock_calls == 0 &&
		g_flush_work_under_lock_calls == 0,
		"ordinary FE/BE scheduling never runs under ipc_queue_lock");
}

static void fail_worker_pm_without_retry(void)
{
	int work_before = g_queue_work_calls;
	int start_before = g_abox.ipc_queue_start;
	int end_before = g_abox.ipc_queue_end;

	g_resume_result = -EIO;
	g_abox_dev.runtime_active = false;
	abox_process_ipc(&g_abox.ipc_work);
	CHECK(g_queue_work_calls == work_before,
		"negative PM resume does not automatically queue a worker retry");
	CHECK(queue_depth() == 2 && g_abox.ipc_queue_start == start_before &&
		g_abox.ipc_queue_end == end_before && !g_dma.enabled,
		"negative PM resume preserves the accepted FE/BE FIFO and cache tail");
	CHECK(g_send_calls == 0 && g_unpowered_send_calls == 0 &&
		g_failsafe_calls == 0,
		"negative PM resume sends nothing and enters no failsafe path");
}

static int duplicate_stop_callback(bool backend)
{
	if (backend)
		return abox_rdma_mute_stream(&g_dai, 1,
				SNDRV_PCM_STREAM_PLAYBACK);
	return abox_rdma_trigger(&g_component, &g_substream,
			SNDRV_PCM_TRIGGER_STOP);
}

static void test_duplicate_rekick_after_pm_failure(bool backend)
{
	int work_before;
	int queue_trace_before;
	int pm_before;
	int start_before;
	int end_before;
	int ret;

	reset_duplicate_world();
	enqueue_mixed_fe_be_fifo();
	fail_worker_pm_without_retry();
	work_before = g_queue_work_calls;
	queue_trace_before = g_duplicate_queue_trace_calls;
	pm_before = g_resume_calls;
	start_before = g_abox.ipc_queue_start;
	end_before = g_abox.ipc_queue_end;
	g_duplicate_trace_queue_enabled = true;
	ret = duplicate_stop_callback(backend);
	CHECK(ret == 0,
		"same-direction FE/BE STOP remains an explicit duplicate success");
	CHECK(g_queue_work_calls == work_before + 1,
		backend ?
		"pending BE STOP duplicate schedules the retained FIFO exactly once" :
		"pending FE STOP duplicate schedules the retained FIFO exactly once");
	CHECK(queue_depth() == 2 && g_abox.ipc_queue_start == start_before &&
		g_abox.ipc_queue_end == end_before && !g_dma.enabled,
		"duplicate re-kick adds no queue message or cached-state mutation");
	CHECK(g_duplicate_queue_trace_calls == queue_trace_before,
		"duplicate re-kick emits no false queue-insertion trace event");
	CHECK(g_resume_calls == pm_before,
		"queue-work scheduling does not run PM synchronously in the callback");
	CHECK(g_queue_work_under_lock_calls == 0 &&
		g_flush_work_under_lock_calls == 0,
		"duplicate re-kick calls queue_work only after ipc_queue_lock release");

#ifndef EXPECT_DUPLICATE_REKICK_BUG
	if (g_queue_work_calls == work_before + 1) {
		g_resume_result = 1;
		abox_process_ipc(&g_abox.ipc_work);
		CHECK(queue_depth() == 0 && g_send_calls == 2 &&
			g_sent_trigger_count == 2 && g_sent_triggers[0] == 1 &&
			g_sent_triggers[1] == 0,
			"successful re-kick drains only retained START then STOP FIFO");
		CHECK(g_resume_calls == 2 && g_put_noidle_calls == 1 &&
			g_put_autosuspend_calls == 1 && g_mark_busy_calls == 1 &&
			g_abox_dev.usage_count == 4,
			"failed and successful PM worker references stay balanced");
		CHECK(!g_dma.enabled && g_queue_work_calls == work_before + 1 &&
			g_failsafe_calls == 0,
			"draining retained FIFO does not change cached state or auto-rekick");
	} else {
		CHECK(false, "scheduled duplicate re-kick drains the retained FIFO");
	}
#else
	CHECK(queue_depth() == 2 && g_send_calls == 0,
		"pre-fix duplicate leaves retained entries waiting without queue work");
#endif
}

static void test_empty_duplicate_stays_noop(void)
{
	int work_before;
	int trace_before;
	int pm_before;

	reset_duplicate_world();
	work_before = g_queue_work_calls;
	trace_before = g_duplicate_queue_trace_calls;
	pm_before = g_resume_calls;
	g_duplicate_trace_queue_enabled = true;
	CHECK(abox_rdma_trigger(&g_component, &g_substream,
			SNDRV_PCM_TRIGGER_STOP) == 0,
		"empty-queue FE STOP remains an idempotent duplicate");
	CHECK(queue_depth() == 0 && !g_dma.enabled &&
		g_queue_work_calls == work_before,
		"empty-queue duplicate creates no worker scheduling or state change");
	CHECK(g_resume_calls == pm_before &&
		g_duplicate_queue_trace_calls == trace_before,
		"empty duplicate causes neither PM activity nor false insertion trace");
	CHECK(g_queue_work_under_lock_calls == 0 &&
		g_flush_work_under_lock_calls == 0,
		"empty duplicate holds no queue lock across scheduling APIs");
}

static void fill_generic_queue(void)
{
	ABOX_IPC_MSG message = {0};
	int index;

	message.ipcid = IPC_SYSTEM;
	for (index = 0; index < ABOX_IPC_QUEUE_SIZE - 1; ++index) {
		message.task_id = index;
		CHECK(abox_request_ipc(&g_abox_dev, message.ipcid, &message,
				sizeof(message), 1, 0) == 0,
			"generic source IPC fills an available ring slot");
	}
	CHECK(queue_depth() == ABOX_IPC_QUEUE_SIZE - 1,
		"generic producer reaches the actual ring-full boundary");
}

static void test_full_queue_duplicate_rekicks_without_insert(void)
{
	int start_before;
	int end_before;
	int work_before;
	int delay_before;
	int flush_before;
	int trace_before;

	reset_duplicate_world();
	fill_generic_queue();
	start_before = g_abox.ipc_queue_start;
	end_before = g_abox.ipc_queue_end;
	work_before = g_queue_work_calls;
	delay_before = g_mdelay_calls;
	flush_before = g_flush_work_calls;
	trace_before = g_duplicate_queue_trace_calls;
	g_duplicate_trace_queue_enabled = true;
	CHECK(abox_rdma_trigger(&g_component, &g_substream,
			SNDRV_PCM_TRIGGER_STOP) == 0,
		"full-ring same-state FE STOP remains duplicate success");
	CHECK(g_queue_work_calls == work_before + 1,
		"full-queue duplicate kicks worker once instead of retrying insertion");
	CHECK(queue_depth() == ABOX_IPC_QUEUE_SIZE - 1 &&
		g_abox.ipc_queue_start == start_before &&
		g_abox.ipc_queue_end == end_before && !g_dma.enabled,
		"full-queue duplicate adds no entry and preserves cached state");
	CHECK(g_mdelay_calls == delay_before && g_flush_work_calls == flush_before,
		"full-queue duplicate bypasses insertion retry/delay/flush loops");
	CHECK(g_resume_calls == 0 &&
		g_duplicate_queue_trace_calls == trace_before,
		"full-queue re-kick schedules without PM work or false queue trace");
	CHECK(g_queue_work_under_lock_calls == 0 &&
		g_flush_work_under_lock_calls == 0,
		"full-queue duplicate schedules only outside ipc_queue_lock");
}

static void test_full_nonduplicate_retry_contract_unchanged(void)
{
	int work_before;
	int delay_before;
	int flush_before;

	reset_duplicate_world();
	fill_generic_queue();
	work_before = g_queue_work_calls;
	delay_before = g_mdelay_calls;
	flush_before = g_flush_work_calls;
	CHECK(abox_rdma_trigger(&g_component, &g_substream,
			SNDRV_PCM_TRIGGER_START) == -EBUSY,
		"full-ring nonduplicate FE START still reports insertion failure");
	CHECK(g_queue_work_calls == work_before + IPC_RETRY + 1 &&
		g_mdelay_calls == delay_before + IPC_RETRY + 1 &&
		g_flush_work_calls == flush_before,
		"full-ring nonduplicate keeps original queue_work and atomic-delay retries");
	CHECK(queue_depth() == ABOX_IPC_QUEUE_SIZE - 1 && !g_dma.enabled,
		"failed full-ring nonduplicate inserts nothing or commits state");
	CHECK(g_queue_work_under_lock_calls == 0 &&
		g_flush_work_under_lock_calls == 0,
		"ordinary full-queue retry APIs run outside ipc_queue_lock");
}

static void test_generic_api_and_sync_contract_unchanged(void)
{
	ABOX_IPC_MSG message = {0};
	int work_before;
	int flush_before;
	int send_before;

	reset_duplicate_world();
	message.ipcid = IPC_PCMPLAYBACK;
	message.task_id = 3;
	message.msg.pcmtask.msgtype = PCM_PLTDAI_TRIGGER;
	message.msg.pcmtask.channel_id = 3;
	message.msg.pcmtask.param.trigger = 0;
	CHECK(abox_request_ipc(&g_abox_dev, message.ipcid, &message,
			sizeof(message), 1, 0) == 0 &&
		abox_request_ipc(&g_abox_dev, message.ipcid, &message,
				sizeof(message), 1, 0) == 0,
		"generic trigger IPC API keeps both identical requests");
	CHECK(queue_depth() == 2 && g_queue_work_calls == 2 &&
		!g_dma.enabled,
		"generic API remains non-deduplicating and does not own FE/BE state");

	work_before = g_queue_work_calls;
	flush_before = g_flush_work_calls;
	CHECK(abox_request_ipc(&g_abox_dev, message.ipcid, &message,
			sizeof(message), 0, 1) == 0,
		"generic non-atomic synchronous scheduling retains its success result");
	CHECK(g_queue_work_calls == work_before + 1 &&
		g_flush_work_calls == flush_before + 1 && queue_depth() == 3,
		"generic synchronous request keeps queue_work then flush_work behavior");

	g_abox_dev.runtime_active = true;
	work_before = g_queue_work_calls;
	flush_before = g_flush_work_calls;
	send_before = g_send_calls;
	CHECK(abox_request_ipc(&g_abox_dev, message.ipcid, &message,
			sizeof(message), 1, 1) == 0,
		"generic atomic+sync direct-send result remains unchanged");
	CHECK(g_queue_work_calls == work_before &&
		g_flush_work_calls == flush_before && queue_depth() == 3 &&
		g_send_calls == send_before + 1 && g_resume_calls == 0,
		"generic direct-send branch bypasses queue and PM exactly as before");
	CHECK(g_queue_work_under_lock_calls == 0 &&
		g_flush_work_under_lock_calls == 0,
		"generic scheduling and flush remain outside ipc_queue_lock");
}

static void run_duplicate_rekick_scenarios(void)
{
	test_empty_duplicate_stays_noop();
	test_duplicate_rekick_after_pm_failure(true);
	test_duplicate_rekick_after_pm_failure(false);
	test_full_queue_duplicate_rekicks_without_insert();
	test_full_nonduplicate_retry_contract_unchanged();
	test_generic_api_and_sync_contract_unchanged();
}

#ifdef EXPECT_DUPLICATE_REKICK_BUG
int main(void)
{
	run_duplicate_rekick_scenarios();
	if (g_failures) {
		fprintf(stderr, "%d expected duplicate re-kick assertions failed\n",
			g_failures);
		return 1;
	}
	puts("FAIL: composed pre-fix source unexpectedly re-kicked duplicates");
	return 2;
}
#else
int main(void)
{
	run_duplicate_rekick_scenarios();
	if (g_failures) {
		fprintf(stderr, "%d host assertions failed\n", g_failures);
		return 1;
	}
	puts("PASS: ABOX duplicate re-kick, PM FIFO, queue bounds, and generic paths");
	return 0;
}
#endif
