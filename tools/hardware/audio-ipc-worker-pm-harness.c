/* Host template for actual extracted ABOX queue, trigger and worker C. */
#include <errno.h>
#include <pthread.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#define EBUSY 16
#define EINVAL 22
#define ENODATA 61
#define EIO 5
#define IPC_SYSTEM 1
#define IPC_PCMPLAYBACK 2
#define PCM_PLTDAI_TRIGGER 17
#define ABOX_RDMA0_BE 8
#define SNDRV_PCM_STREAM_PLAYBACK 0
#define SNDRV_PCM_TRIGGER_START 10
#define SNDRV_PCM_TRIGGER_RESUME 11
#define SNDRV_PCM_TRIGGER_PAUSE_RELEASE 12
#define SNDRV_PCM_TRIGGER_STOP 13
#define SNDRV_PCM_TRIGGER_SUSPEND 14
#define SNDRV_PCM_TRIGGER_PAUSE_PUSH 15
#define SZ_128 128
#define ARRAY_SIZE(array) (sizeof(array) / sizeof((array)[0]))
#define unlikely(value) (value)
#define container_of(pointer, type, member) \
	((type *)((char *)(pointer) - offsetof(type, member)))
#define asoc_substream_to_rtd(substream) ((substream)->rtd)
#define asoc_rtd_to_cpu(runtime, index) ((void)(index), (runtime)->cpu)
#define snd_soc_dai_get_drvdata(dai) ((dai)->driver_data)

/* ACTUAL_IPC_RETRY_DEFINE */
/* ACTUAL_QUEUE_SIZE_DEFINE */

typedef uint64_t u64;
typedef pthread_mutex_t spinlock_t;

#define spin_lock_irqsave(lock, flags) do { \
	(void)sizeof(flags); \
	pthread_mutex_lock(lock); \
} while (0)
#define spin_unlock_irqrestore(lock, flags) do { \
	(void)sizeof(flags); \
	pthread_mutex_unlock(lock); \
} while (0)

struct abox_data;
struct snd_pcm_substream;
struct snd_soc_pcm_runtime;
struct snd_soc_dai;
struct snd_soc_component;

struct device {
	void *driver_data;
	int usage_count;
	bool runtime_active;
};

struct IPC_PCMTASK_MSG {
	int msgtype;
	int channel_id;
	struct { int trigger; } param;
};

struct ABOX_IPC_MSG {
	int ipcid;
	int task_id;
	struct { struct IPC_PCMTASK_MSG pcmtask; } msg;
};
typedef struct ABOX_IPC_MSG ABOX_IPC_MSG;

struct abox_ipc {
	struct device *dev;
	int hw_irq;
	unsigned long long put_time;
	unsigned long long get_time;
	u64 trace_seq;
	size_t size;
	ABOX_IPC_MSG msg;
};

struct work_struct { int unused; };
struct workqueue_struct { int unused; };

struct abox_data {
	struct device *dev;
	struct abox_ipc ipc_queue[ABOX_IPC_QUEUE_SIZE];
	int ipc_queue_start;
	int ipc_queue_end;
	spinlock_t ipc_queue_lock;
	struct workqueue_struct *ipc_workqueue;
	struct work_struct ipc_work;
};

struct completion { bool done; };

struct abox_dma_data {
	bool enabled;
	int id;
	struct device *dev;
	struct device *dev_abox;
	struct snd_pcm_substream *substream;
	struct completion func_changed;
};

struct snd_soc_dai {
	int id;
	struct device *dev;
	void *driver_data;
};

struct snd_soc_pcm_runtime {
	struct snd_soc_dai *cpu;
};

struct snd_pcm_substream {
	struct snd_soc_pcm_runtime *rtd;
	int stream;
};

struct snd_soc_component { int unused; };

static int g_failures;
#define CHECK(condition, label) do { \
	if (!(condition)) { \
		fprintf(stderr, "FAIL: %s\n", label); \
		++g_failures; \
	} \
} while (0)

static int g_queue_work_calls;
static int g_flush_work_calls;
static int g_mdelay_calls;
static int g_failsafe_calls;
static int g_send_calls;
static int g_unpowered_send_calls;
static int g_send_result;
static int g_resume_result;
static int g_resume_calls;
static int g_put_noidle_calls;
static int g_put_autosuspend_calls;
static int g_mark_busy_calls;
static int g_calliope_calls;
static int g_pm_error_log_calls;
static int g_pm_error_code;
static bool g_pm_error_log_shape;
static int g_sched_clock_calls;
static int g_sent_triggers[8];
static int g_sent_trigger_count;
static int g_clock;
static u64 g_trace_sequence;
static u64 abox_pcm_trigger_trace_seq;

static int queue_work(struct workqueue_struct *queue,
		struct work_struct *work)
{
	(void)queue;
	(void)work;
	++g_queue_work_calls;
	return 1;
}

static void flush_work(struct work_struct *work)
{
	(void)work;
	++g_flush_work_calls;
}

static void mdelay(unsigned int milliseconds)
{
	(void)milliseconds;
	++g_mdelay_calls;
}

static unsigned long long sched_clock(void)
{
	++g_sched_clock_calls;
	return (unsigned long long)++g_clock;
}

static u64 ktime_get_ns(void)
{
	return (u64)++g_clock;
}

static u64 atomic64_inc_return(u64 *counter)
{
	return ++*counter;
}

static bool trace_abox_pcm_trigger_queue_enabled(void) { return false; }
static bool trace_abox_pcm_trigger_send_enabled(void) { return false; }
static void trace_abox_pcm_trigger_queue(u64 sequence, u64 mono_ns,
		int ipc_id, int channel, int message_type, int result,
		int attempt, bool atomic, bool sync)
{
	(void)sequence; (void)mono_ns; (void)ipc_id; (void)channel;
	(void)message_type; (void)result; (void)attempt; (void)atomic;
	(void)sync;
}
static void trace_abox_pcm_trigger_send(u64 sequence, u64 mono_ns,
		int ipc_id, int channel, int message_type, int result)
{
	(void)sequence; (void)mono_ns; (void)ipc_id; (void)channel;
	(void)message_type; (void)result;
}

static struct abox_data *dev_get_drvdata(struct device *dev)
{
	return dev->driver_data;
}

static void host_pm_resume_error(struct device *dev, const char *format,
		const char *function, int error)
{
	(void)dev;
	++g_pm_error_log_calls;
	g_pm_error_code = error;
	g_pm_error_log_shape =
		!strcmp(format, "%s: IPC runtime resume failed: %d\n") &&
		!strcmp(function, "abox_process_ipc");
}

#define dev_err_ratelimited(dev, format, function, error) \
	host_pm_resume_error((dev), (format), (function), (error))
#define abox_dbg(dev, ...) ((void)(dev))
#define abox_info(dev, ...) ((void)(dev))
#define abox_err(dev, ...) ((void)(dev))
#define abox_warn(dev, ...) ((void)(dev))

/* Mirrors the pinned pm_runtime_get_sync usage-count rule in the shim. */
static int pm_runtime_get_sync(struct device *dev)
{
	++dev->usage_count;
	++g_resume_calls;
	dev->runtime_active = g_resume_result >= 0;
	return g_resume_result;
}

static void pm_runtime_put_noidle(struct device *dev)
{
	++g_put_noidle_calls;
	if (dev->usage_count > 0)
		--dev->usage_count;
}

static void pm_runtime_mark_last_busy(struct device *dev)
{
	(void)dev;
	++g_mark_busy_calls;
}

static void pm_runtime_put_autosuspend(struct device *dev)
{
	++g_put_autosuspend_calls;
	if (dev->usage_count > 0)
		--dev->usage_count;
}

static void abox_failsafe_report(struct device *dev, bool error)
{
	(void)dev;
	(void)error;
	++g_failsafe_calls;
}

static int abox_ipc_send(struct device *dev, const ABOX_IPC_MSG *msg,
		size_t size, void *response, size_t response_size)
{
	(void)size;
	(void)response;
	(void)response_size;
	++g_send_calls;
	if (!dev->runtime_active)
		++g_unpowered_send_calls;
	if (msg->ipcid == IPC_PCMPLAYBACK &&
			msg->msg.pcmtask.msgtype == PCM_PLTDAI_TRIGGER &&
			g_sent_trigger_count < (int)ARRAY_SIZE(g_sent_triggers))
		g_sent_triggers[g_sent_trigger_count++] =
			msg->msg.pcmtask.param.trigger;
	return g_send_result;
}

static bool abox_can_calliope_ipc(struct device *dev,
		struct abox_data *data)
{
	(void)dev;
	(void)data;
	++g_calliope_calls;
	return true;
}

static bool completion_done(struct completion *completion)
{
	return completion->done;
}

static void complete(struct completion *completion)
{
	completion->done = true;
}

static bool abox_dma_can_start(struct snd_soc_pcm_runtime *runtime,
		int stream)
{
	return runtime && stream == SNDRV_PCM_STREAM_PLAYBACK;
}

static bool abox_dma_can_stop(struct snd_soc_pcm_runtime *runtime,
		int stream)
{
	return runtime && stream == SNDRV_PCM_STREAM_PLAYBACK;
}

/* ACTUAL_QUEUE_EMPTY_FUNCTION */
/* ACTUAL_QUEUE_FULL_FUNCTION */
/* ACTUAL_QUEUE_PUT_FUNCTION */
/* ACTUAL_QUEUE_GET_FUNCTION */
/* ACTUAL_PROCESS_IPC_FUNCTION */
/* ACTUAL_WORKER_FUNCTION */
/* ACTUAL_SCHEDULER_FUNCTION */
/* ACTUAL_GENERIC_REQUEST_FUNCTION */
/* ACTUAL_SPECIALIZED_REQUEST_FUNCTION */
/* ACTUAL_RDMA_BACKEND_FUNCTION */
/* ACTUAL_TRIGGER_HELPER_FUNCTION */
/* ACTUAL_FE_TRIGGER_FUNCTION */
/* ACTUAL_BE_MUTE_FUNCTION */

static struct abox_data g_abox;
static struct device g_abox_dev;
static struct device g_dma_dev;
static struct abox_dma_data g_dma;
static struct snd_soc_dai g_dai;
static struct snd_soc_pcm_runtime g_runtime;
static struct snd_pcm_substream g_substream;
static struct snd_soc_component g_component;
static bool g_lock_initialized;

static void reset_counters(void)
{
	g_failures = 0;
	g_queue_work_calls = 0;
	g_flush_work_calls = 0;
	g_mdelay_calls = 0;
	g_failsafe_calls = 0;
	g_send_calls = 0;
	g_unpowered_send_calls = 0;
	g_send_result = 0;
	g_resume_result = 0;
	g_resume_calls = 0;
	g_put_noidle_calls = 0;
	g_put_autosuspend_calls = 0;
	g_mark_busy_calls = 0;
	g_calliope_calls = 0;
	g_pm_error_log_calls = 0;
	g_pm_error_code = 0;
	g_pm_error_log_shape = false;
	g_sched_clock_calls = 0;
	g_sent_trigger_count = 0;
	memset(g_sent_triggers, 0, sizeof(g_sent_triggers));
	g_clock = 0;
	g_trace_sequence = 0;
	abox_pcm_trigger_trace_seq = 0;
}

static void reset_world(void)
{
	if (g_lock_initialized)
		pthread_mutex_destroy(&g_abox.ipc_queue_lock);
	memset(&g_abox, 0, sizeof(g_abox));
	memset(&g_abox_dev, 0, sizeof(g_abox_dev));
	memset(&g_dma_dev, 0, sizeof(g_dma_dev));
	memset(&g_dma, 0, sizeof(g_dma));
	memset(&g_dai, 0, sizeof(g_dai));
	memset(&g_runtime, 0, sizeof(g_runtime));
	memset(&g_substream, 0, sizeof(g_substream));
	pthread_mutex_init(&g_abox.ipc_queue_lock, NULL);
	g_lock_initialized = true;
	g_abox_dev.driver_data = &g_abox;
	g_abox_dev.usage_count = 4;
	g_abox.dev = &g_abox_dev;
	g_abox.ipc_workqueue = (struct workqueue_struct *)&g_abox;
	g_dma_dev.driver_data = &g_dma;
	g_dma.enabled = false;
	g_dma.id = 3;
	g_dma.dev = &g_dma_dev;
	g_dma.dev_abox = &g_abox_dev;
	g_dma.substream = &g_substream;
	g_dai.id = 3;
	g_dai.dev = &g_dma_dev;
	g_dai.driver_data = &g_dma;
	g_runtime.cpu = &g_dai;
	g_substream.rtd = &g_runtime;
	g_substream.stream = SNDRV_PCM_STREAM_PLAYBACK;
	reset_counters();
}

static int queue_depth(void)
{
	if (g_abox.ipc_queue_end >= g_abox.ipc_queue_start)
		return g_abox.ipc_queue_end - g_abox.ipc_queue_start;
	return ABOX_IPC_QUEUE_SIZE - g_abox.ipc_queue_start +
		g_abox.ipc_queue_end;
}

static void queue_fe_start_be_stop(void)
{
	CHECK(abox_rdma_trigger(&g_component, &g_substream,
			SNDRV_PCM_TRIGGER_START) == 0,
		"FE START is locally accepted into the actual queue");
	CHECK(abox_rdma_mute_stream(&g_dai, 1,
			SNDRV_PCM_STREAM_PLAYBACK) == 0,
		"BE STOP is locally accepted into the actual queue");
	CHECK(queue_depth() == 2 && !g_dma.enabled,
		"mixed FE START then BE STOP retain FIFO and last accepted state");
}

static void test_negative_resume(void)
{
	int initial_usage;
	int initial_start;
	int initial_end;
	int initial_clock_calls;
	int work_calls_before;

	reset_world();
	queue_fe_start_be_stop();
	initial_usage = g_abox_dev.usage_count;
	initial_start = g_abox.ipc_queue_start;
	initial_end = g_abox.ipc_queue_end;
	initial_clock_calls = g_sched_clock_calls;
	work_calls_before = g_queue_work_calls;
	g_resume_result = -EIO;
	g_abox_dev.runtime_active = false;

	abox_process_ipc(&g_abox.ipc_work);

	CHECK(g_resume_calls == 1,
		"one failed runtime-PM resume is observed");
	CHECK(g_put_noidle_calls == 1 && g_put_autosuspend_calls == 0 &&
		g_abox_dev.usage_count == initial_usage,
		"failed get_sync reference is balanced once with put_noidle");
	CHECK(g_abox.ipc_queue_start == initial_start &&
		g_abox.ipc_queue_end == initial_end && queue_depth() == 2 &&
		g_sched_clock_calls == initial_clock_calls,
		"failed resume returns before dequeue and preserves FIFO contents");
	CHECK(g_send_calls == 0 && g_unpowered_send_calls == 0,
		"failed resume never sends IPC while runtime resume failed");
	CHECK(g_calliope_calls == 0,
		"failed resume returns before Calliope gating");
	CHECK(g_mark_busy_calls == 0 && g_put_autosuspend_calls == 0,
		"failed resume does not enter successful worker PM-release path");
	CHECK(g_pm_error_log_calls == 1 && g_pm_error_code == -EIO &&
		g_pm_error_log_shape,
		"failed resume emits a rate-limited function-and-errno log");
	CHECK(g_failsafe_calls == 0 && g_queue_work_calls == work_calls_before,
		"PM failure invokes no failsafe and schedules no automatic retry");
	CHECK(!g_dma.enabled,
		"PM failure does not speculate-reset the accepted BE STOP state");
}

#ifndef EXPECT_BASELINE_PM_BUG
static void test_explicit_callback_retries_retained_fifo(void)
{
	int initial_usage;

	reset_world();
	queue_fe_start_be_stop();
	initial_usage = g_abox_dev.usage_count;
	g_resume_result = -EIO;
	g_abox_dev.runtime_active = false;
	abox_process_ipc(&g_abox.ipc_work);
	CHECK(queue_depth() == 2 && !g_dma.enabled,
		"failed worker retains accepted FE/BE FIFO and cached tail state");

	/* A new user/ASoC callback, not the worker, is the next retry trigger. */
	g_resume_result = 1;
	CHECK(abox_rdma_trigger(&g_component, &g_substream,
			SNDRV_PCM_TRIGGER_RESUME) == 0,
		"later explicit FE RESUME is accepted after PM failure");
	CHECK(g_dma.enabled && queue_depth() == 3,
		"explicit callback appends behind the retained FIFO and commits locally");
	abox_process_ipc(&g_abox.ipc_work);

	CHECK(g_sent_trigger_count == 3 &&
		g_sent_triggers[0] == 1 && g_sent_triggers[1] == 0 &&
		g_sent_triggers[2] == 1,
		"later successful worker sends retained START, STOP, then RESUME FIFO");
	CHECK(queue_depth() == 0 && g_send_calls == 3 &&
		g_unpowered_send_calls == 0,
		"positive get_sync result is accepted and drains only when powered");
	CHECK(g_dma.enabled,
		"worker drain leaves the latest locally accepted direction unchanged");
	CHECK(g_resume_calls == 2 && g_put_noidle_calls == 1 &&
		g_put_autosuspend_calls == 1 && g_mark_busy_calls == 1 &&
		g_abox_dev.usage_count == initial_usage,
		"failed and successful worker PM references are each released once");
	CHECK(g_calliope_calls == 1 && g_failsafe_calls == 0 &&
		g_pm_error_log_calls == 1,
		"explicit success preserves sender path without PM-failure recovery work");
}

static void test_existing_sender_error_and_pm_balance(void)
{
	ABOX_IPC_MSG message = {0};
	int initial_usage;

	reset_world();
	initial_usage = g_abox_dev.usage_count;
	message.ipcid = IPC_SYSTEM;
	message.task_id = 99;
	CHECK(abox_request_ipc(&g_abox_dev, message.ipcid, &message,
			sizeof(message), 1, 0) == 0,
		"generic async IPC is locally queued before worker execution");
	g_resume_result = 0;
	g_send_result = -EIO;
	abox_process_ipc(&g_abox.ipc_work);
	CHECK(queue_depth() == 0 && g_send_calls == 1 &&
		g_unpowered_send_calls == 0,
		"successful resume keeps the existing sender call and dequeue path");
	CHECK(g_failsafe_calls == 1,
		"unrelated post-resume sender failure keeps existing failsafe report");
	CHECK(g_resume_calls == 1 && g_put_noidle_calls == 0 &&
		g_put_autosuspend_calls == 1 && g_mark_busy_calls == 1 &&
		g_abox_dev.usage_count == initial_usage,
		"ordinary successful worker balances its get_sync through autosuspend");
	CHECK(g_pm_error_log_calls == 0,
		"sender failures are not misreported as runtime-resume failures");
}

int main(void)
{
	test_negative_resume();
	test_explicit_callback_retries_retained_fifo();
	test_existing_sender_error_and_pm_balance();
	if (g_failures) {
		fprintf(stderr, "%d host assertions failed\n", g_failures);
		return 1;
	}
	puts("PASS: extracted ABOX PM worker, queue retention, and explicit FIFO retry");
	return 0;
}
#else
int main(void)
{
	test_negative_resume();
	if (g_failures) {
		fprintf(stderr, "%d expected baseline PM assertions failed\n",
			g_failures);
		return 1;
	}
	puts("FAIL: pre-fix worker unexpectedly passed PM-resume assertions");
	return 2;
}
#endif
