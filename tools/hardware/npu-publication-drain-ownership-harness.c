/*
 * Supplemental test body inserted into the existing pthread shim around the
 * exact extracted npu-session.c waiter and protodrv POWER_CTL worker.
 *
 * EXPECT_DRAIN_OWNERSHIP_PATCH_VALUE is set by the Python driver.  This tests
 * lock/list interleavings only; the session-close thread below models the
 * source-audited shared global_lock acquisition, not the full kernel teardown.
 */
static void *drain_close_main(void *opaque)
{
	pthread_mutex_t *global_lock = opaque;
	atomic_store(&drain_close_started, 1);
	gate_signal(&drain_close_started);
	pthread_mutex_lock(global_lock);
	atomic_store(&drain_close_returned, 1);
	gate_signal(&drain_close_returned);
	pthread_mutex_unlock(global_lock);
	return NULL;
}

static void register_new_waiter_with_fresh_cookie(
		struct npu_power_waiter *waiter, u64 old_cookie, npu_req_id_t req_id)
{
	unsigned long flags = 0;

	memset(waiter, 0, sizeof(*waiter));
	INIT_LIST_HEAD(&waiter->list);
	init_completion(&waiter->completion);
	init_completion(&waiter->publish_done);
	for (;;) {
		waiter->cookie = atomic64_inc_return(&npu_power_wait_cookie);
		spin_lock_irqsave(&npu_power_waiters_lock, flags);
		if (waiter->cookie && !npu_power_waiter_find(waiter->cookie))
			break;
		spin_unlock_irqrestore(&npu_power_waiters_lock, flags);
	}
	list_add_tail(&waiter->list, &npu_power_waiters);
	spin_unlock_irqrestore(&npu_power_waiters_lock, flags);
	expect(waiter->cookie != old_cookie,
	       "new waiter reused the canceled waiter's cookie");
	expect(npu_session_power_wait_assign_req_id(waiter->cookie, req_id) == 0,
	       "new waiter request ID assignment failed");
}

static void check_old_cookie_does_not_touch_new_waiter(
		struct npu_power_waiter *new_waiter, u64 old_cookie,
		npu_req_id_t old_req_id)
{
	struct nw_result stale = { 0 };
	unsigned long flags = 0;

	stale.nw.param0 = (u32)old_cookie;
	stale.nw.param1 = (u32)(old_cookie >> 32);
	/* Reuse the request ID deliberately: the opaque cookie must still isolate. */
	stale.nw.npu_req_id = old_req_id;
	stale.result_code = -EIO;
	npu_session_save_power_result(NULL, stale);
	npu_session_save_power_result(NULL, stale);
	npu_session_power_wait_finish_publish(old_cookie, old_req_id);
	npu_session_power_wait_finish_publish(old_cookie, old_req_id);
	spin_lock_irqsave(&npu_power_waiters_lock, flags);
	expect(npu_power_waiter_find(new_waiter->cookie) == new_waiter &&
	       new_waiter->req_id == old_req_id && !new_waiter->done &&
	       !new_waiter->cancelled && !new_waiter->publishing &&
	       !new_waiter->publish_committed,
	       "late callback/finish for old cookie modified the newer waiter");
	list_del_init(&new_waiter->list);
	spin_unlock_irqrestore(&npu_power_waiters_lock, flags);
}

static void test_cancel_between_begin_and_authorize(void)
{
	struct caller_state caller;
	struct npu_power_waiter new_waiter;
	pthread_t caller_thread, close_thread;
	u64 old_cookie;
	npu_req_id_t old_req_id;
	bool cancelled = false, done = false, publishing = false, committed = false;

	reset_runtime(MODE_BEFORE_AUTHORIZE);
	atomic_store(&drain_cancel_observed, 0);
	atomic_store(&drain_close_started, 0);
	atomic_store(&drain_close_returned, 0);
	start_caller(&caller, &caller_thread);
	wait_or_fail(&begin_paused, "publisher pause after actual publish begin");
	wait_or_fail(&drain_cancel_observed, "cancellation at the pre-authorization boundary");
	old_cookie = atomic_load(&active_cookie);
	old_req_id = (npu_req_id_t)atomic_load(&active_req_id);

#if EXPECT_DRAIN_OWNERSHIP_PATCH_VALUE
	wait_or_fail(&caller_returned, "pre-authorization cancellation to unlink waiter");
	expect(!registered_waiter_state(&cancelled, &done, &publishing, &committed),
	       "canceled pre-authorization waiter remained registered after caller return");
	register_new_waiter_with_fresh_cookie(&new_waiter, old_cookie, old_req_id);
	expect(atomic_load(&begin_paused) && !atomic_load(&worker_done),
	       "publisher was not still paused before its authorization lookup");
	expect(pthread_create(&close_thread, NULL, drain_close_main,
	                      caller.session.global_lock) == 0,
	       "failed to start source-lock-serialized close context");
	wait_or_fail(&drain_close_started, "close context start");
	wait_or_fail(&drain_close_returned,
	             "session-close lock acquisition while old publisher is pre-authorization");
	expect(!atomic_load(&worker_done) && atomic_load(&begin_paused),
	       "close test released or completed the paused publisher");
	gate_signal(&release_begin);
	wait_or_fail(&worker_done, "old publisher failed authorization and finished");
	expect(pthread_join(publisher_thread, NULL) == 0,
	       "pre-authorization publisher did not join");
	expect(pthread_join(caller_thread, NULL) == 0,
	       "pre-authorization caller did not join");
	expect(pthread_join(close_thread, NULL) == 0,
	       "source-lock-serialized close context did not join");
	expect(caller.result == -ETIMEDOUT &&
	       atomic_load(&mailbox_callback_count) == 0 &&
	       atomic_load(&lsm_move_count) == 1 &&
	       atomic_load(&lsm_last_state) == FREE &&
	       !registered_waiter_state(&cancelled, &done, &publishing, &committed),
	       "unlinked pre-authorization request was posted, retained, or re-registered");
	check_old_cookie_does_not_touch_new_waiter(&new_waiter, old_cookie, old_req_id);
	puts("PASS patch10 actual C: pre-auth cancel unlinks; old authorize/finish and late callbacks cannot touch a fresh-cookie waiter; session-close lock proceeds while AST is paused");
#else
	expect(registered_waiter_state(&cancelled, &done, &publishing, &committed) &&
	       cancelled && !done && publishing && !committed,
	       "baseline did not retain its canceled waiter in the pre-authorization window");
	expect(!atomic_load(&caller_returned),
	       "baseline caller unexpectedly returned before publication finish");
	expect(pthread_create(&close_thread, NULL, drain_close_main,
	                      caller.session.global_lock) == 0,
	       "failed to start baseline close context");
	wait_or_fail(&drain_close_started, "baseline close context start");
	expect(!wait_flag(&drain_close_returned, 120),
	       "baseline session-close lock passed the still-blocked POWER_NOTIFY caller");
	expect(!atomic_load(&caller_returned) && atomic_load(&begin_paused),
	       "baseline drain did not remain blocked behind the paused publisher");
	puts("BASELINE REPRO actual C: canceled pre-authorization waiter remains linked; unbounded drain blocks caller/session-close lock until AST resumes");
	gate_signal(&release_begin);
	wait_or_fail(&caller_returned, "baseline drain after failed authorization");
	wait_or_fail(&worker_done, "baseline publisher failed authorization and finished");
	expect(pthread_join(publisher_thread, NULL) == 0,
	       "baseline pre-authorization publisher did not join");
	expect(pthread_join(caller_thread, NULL) == 0,
	       "baseline pre-authorization caller did not join");
	wait_or_fail(&drain_close_returned, "baseline session close after caller releases global_lock");
	expect(pthread_join(close_thread, NULL) == 0,
	       "baseline close context did not join");
	expect(caller.result == -ETIMEDOUT &&
	       atomic_load(&mailbox_callback_count) == 0 &&
	       atomic_load(&lsm_last_state) == FREE &&
	       !registered_waiter_state(&cancelled, &done, &publishing, &committed),
	       "baseline failed to clean up after publisher finish");
	register_new_waiter_with_fresh_cookie(&new_waiter, old_cookie, old_req_id);
	check_old_cookie_does_not_touch_new_waiter(&new_waiter, old_cookie, old_req_id);
	puts("PASS baseline actual C: failed authorization and late old-cookie events do not affect a later waiter");
#endif
}
