/* Host test template; the driver and ASoC functions are extracted at runtime. */
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
#define ENOTSUPP 524
#define EPROBE_DEFER 517
#define EIO 5
#define IPC_SYSTEM 1
#define IPC_PCMPLAYBACK 2
#define PCM_PLTDAI_TRIGGER 17
#define ABOX_RDMA0_BE 8
#define SNDRV_PCM_STREAM_PLAYBACK 0
#define SNDRV_PCM_STREAM_CAPTURE 1
#define SNDRV_PCM_TRIGGER_START 10
#define SNDRV_PCM_TRIGGER_RESUME 11
#define SNDRV_PCM_TRIGGER_PAUSE_RELEASE 12
#define SNDRV_PCM_TRIGGER_STOP 13
#define SNDRV_PCM_TRIGGER_SUSPEND 14
#define SNDRV_PCM_TRIGGER_PAUSE_PUSH 15
#define SND_SOC_DAPM_PRE_PMU 20
#define SND_SOC_DAPM_POST_PMU 21
#define SND_SOC_DAPM_PRE_PMD 22
#define SND_SOC_DAPM_POST_PMD 23
#define SND_SOC_DAPM_DIR_OUT 0
#define SND_SOC_DAPM_DIR_IN 1
#define SND_SOC_DAPM_STREAM_START 30
#define SZ_128 128
#define ARRAY_SIZE(array) (sizeof(array) / sizeof((array)[0]))
#define unlikely(value) (value)
#define list_empty(list) false
#define WARN_ON(condition) (condition)
#define WARN(condition, ...) ((void)(condition))
#define kfree(pointer) ((void)(pointer))
#define dev_err(device, ...) ((void)(device))
#define dev_warn(device, ...) ((void)(device))
#define abox_dbg(device, ...) ((void)(device))
#define abox_info(device, ...) ((void)(device))
#define abox_err(device, ...) ((void)(device))
#define abox_warn(device, ...) ((void)(device))
#define EXPORT_SYMBOL(symbol)
#define asoc_substream_to_rtd(substream) ((substream)->rtd)
#define asoc_rtd_to_cpu(runtime, index) ((void)(index), (runtime)->cpu)
#define snd_soc_dai_get_drvdata(dai) ((dai)->driver_data)
#define for_each_rtd_dais(runtime, index, dai) \
	for ((index) = 0, (dai) = (runtime)->dai; (index) < 1; ++(index))
#define snd_soc_dapm_widget_for_each_sink_path(widget, path) \
	for ((path) = (widget)->test_path; (path); (path) = NULL)
#define snd_soc_dapm_widget_for_each_source_path(widget, path) \
	for ((path) = (widget)->test_path; (path); (path) = NULL)
#define mutex_lock_nested(lock, subclass) ((void)(lock), (void)(subclass))
#define mutex_unlock(lock) ((void)(lock))
#define cancel_delayed_work(work) ((void)(work))

typedef uint64_t u64;
typedef pthread_mutex_t spinlock_t;

#define spin_lock_irqsave(lock, flags) do { \
	(void)(flags); \
	pthread_mutex_lock(lock); \
} while (0)
#define spin_unlock_irqrestore(lock, flags) do { \
	(void)(flags); \
	pthread_mutex_unlock(lock); \
} while (0)

/* ACTUAL_IPC_RETRY_DEFINE */
/* ACTUAL_QUEUE_SIZE_DEFINE */
/* ACTUAL_QUEUE_STATE_API_DEFINE */

struct abox_data;
struct snd_soc_dai;
struct snd_pcm_substream;
struct snd_soc_dapm_widget;
struct snd_soc_dapm_path;

struct device {
	void *driver_data;
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

struct snd_soc_dai_ops {
	int (*mute_stream)(struct snd_soc_dai *dai, int mute, int stream);
	bool no_capture_mute;
};

struct snd_soc_dai_driver {
	struct snd_soc_dai_ops *ops;
};

struct snd_soc_dai {
	int id;
	int rate;
	int channels;
	int sample_bits;
	struct device *dev;
	void *driver_data;
	struct snd_soc_dai_driver *driver;
};

struct snd_soc_card {
	int pcm_mutex;
	int pcm_subclass;
};

struct snd_soc_pcm_runtime {
	struct snd_soc_dai *cpu;
	struct snd_soc_dai *dai;
	struct snd_soc_card *card;
	struct device *dev;
	int pop_wait;
	int delayed_work;
};

struct snd_pcm_substream {
	struct snd_soc_pcm_runtime *rtd;
	void *runtime;
	int stream;
};

struct snd_soc_component { int unused; };
struct snd_kcontrol { int unused; };

struct snd_soc_dapm_widget {
	void *priv;
	int edges[2];
	struct snd_soc_dapm_path *test_path;
};

struct snd_soc_dapm_path {
	struct snd_soc_dapm_widget *source;
	struct snd_soc_dapm_widget *sink;
};

static int g_failures;
#define CHECK(condition, label) do { \
	if (!(condition)) { \
		fprintf(stderr, "FAIL: %s\n", label); \
		++g_failures; \
	} \
} while (0)

static volatile int g_queue_work_calls;
static volatile int g_flush_work_calls;
static volatile int g_mdelay_calls;
static volatile int g_failsafe_calls;
static volatile int g_send_calls;
static volatile int g_send_result;
static volatile unsigned long long g_clock;
static volatile u64 g_trace_seq;
static volatile u64 abox_pcm_trigger_trace_seq;

static pthread_mutex_t g_gate_lock = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t g_gate_cond = PTHREAD_COND_INITIALIZER;
static pthread_t g_gate_target;
static bool g_gate_armed;
static bool g_gate_entered;
static bool g_gate_release;
static bool g_fe_thread_ready;

static int atomic_read_int(volatile int *value)
{
	return __sync_add_and_fetch(value, 0);
}

static int queue_work(struct workqueue_struct *queue,
		struct work_struct *work)
{
	(void)queue;
	(void)work;
	__sync_add_and_fetch(&g_queue_work_calls, 1);
	pthread_mutex_lock(&g_gate_lock);
	if (g_gate_armed && pthread_equal(pthread_self(), g_gate_target)) {
		g_gate_armed = false;
		g_gate_entered = true;
		pthread_cond_broadcast(&g_gate_cond);
		while (!g_gate_release)
			pthread_cond_wait(&g_gate_cond, &g_gate_lock);
	}
	pthread_mutex_unlock(&g_gate_lock);
	return 1;
}

static void flush_work(struct work_struct *work)
{
	(void)work;
	__sync_add_and_fetch(&g_flush_work_calls, 1);
}

static void mdelay(unsigned int milliseconds)
{
	(void)milliseconds;
	__sync_add_and_fetch(&g_mdelay_calls, 1);
}

static unsigned long long sched_clock(void)
{
	return __sync_add_and_fetch(&g_clock, 1);
}

static u64 ktime_get_ns(void)
{
	return __sync_add_and_fetch(&g_clock, 1);
}

static u64 atomic64_inc_return(volatile u64 *counter)
{
	return __sync_add_and_fetch(counter, 1);
}

static bool trace_abox_pcm_trigger_queue_enabled(void) { return false; }
static bool trace_abox_pcm_trigger_send_enabled(void) { return false; }
static void trace_abox_pcm_trigger_queue(u64 seq, u64 ns, int ipc, int ch,
		int type, int result, int attempt, bool atomic, bool sync)
{
	(void)seq; (void)ns; (void)ipc; (void)ch; (void)type; (void)result;
	(void)attempt; (void)atomic; (void)sync;
}
static void trace_abox_pcm_trigger_send(u64 seq, u64 ns, int ipc, int ch,
		int type, int result)
{
	(void)seq; (void)ns; (void)ipc; (void)ch; (void)type; (void)result;
}

static void abox_failsafe_report(struct device *dev, bool fatal)
{
	(void)dev;
	(void)fatal;
	__sync_add_and_fetch(&g_failsafe_calls, 1);
}

static struct abox_data *dev_get_drvdata(struct device *dev)
{
	return dev->driver_data;
}

static int abox_ipc_send(struct device *dev, const ABOX_IPC_MSG *msg,
		size_t size, void *response, size_t response_size)
{
	(void)dev; (void)msg; (void)size; (void)response; (void)response_size;
	__sync_add_and_fetch(&g_send_calls, 1);
	return atomic_read_int(&g_send_result);
}

static bool completion_done(struct completion *completion)
{
	return completion->done;
}

static void complete(struct completion *completion)
{
	completion->done = true;
}

static bool abox_dma_can_start(struct snd_soc_pcm_runtime *runtime, int stream)
{
	return runtime && stream == SNDRV_PCM_STREAM_PLAYBACK;
}

static bool abox_dma_can_stop(struct snd_soc_pcm_runtime *runtime, int stream)
{
	return runtime && stream == SNDRV_PCM_STREAM_PLAYBACK;
}

static int snd_soc_dai_link_event_pre_pmu(struct snd_soc_dapm_widget *widget,
		struct snd_pcm_substream *substream)
{
	(void)widget; (void)substream;
	return 0;
}

static int snd_soc_dai_hw_free(struct snd_soc_dai *dai,
		struct snd_pcm_substream *substream)
{
	(void)dai; (void)substream;
	return 0;
}

static int snd_soc_dai_deactivate(struct snd_soc_dai *dai, int stream)
{
	(void)dai; (void)stream;
	return 0;
}

static int snd_soc_dai_shutdown(struct snd_soc_dai *dai,
		struct snd_pcm_substream *substream, int rollback)
{
	(void)dai; (void)substream; (void)rollback;
	return 0;
}

static int abox_rdma_can_send_direct(struct device *dev,
		const ABOX_IPC_MSG *msg, size_t size)
{
	return 0;
}

/* ACTUAL_QUEUE_EMPTY_FUNCTION */
/* ACTUAL_QUEUE_FULL_FUNCTION */
/* ACTUAL_QUEUE_PUT_FUNCTION */
/* ACTUAL_QUEUE_GET_FUNCTION */
/* ACTUAL_PROCESS_IPC_FUNCTION */
/* ACTUAL_SCHEDULER_FUNCTION */
/* ACTUAL_GENERIC_REQUEST_FUNCTION */
/* ACTUAL_SPECIALIZED_REQUEST_FUNCTION */
/* ACTUAL_RDMA_REQUEST_FUNCTION */
/* ACTUAL_RDMA_BACKEND_FUNCTION */
/* ACTUAL_TRIGGER_HELPER_FUNCTION */
/* ACTUAL_FE_TRIGGER_FUNCTION */
/* ACTUAL_BE_MUTE_FUNCTION */
/* ACTUAL_SOC_DAI_RET_FUNCTION */
#define soc_dai_ret(dai, ret) _soc_dai_ret(dai, __func__, ret)
/* ACTUAL_DIGITAL_MUTE_FUNCTION */
/* ACTUAL_DAPM_LINK_EVENT_FUNCTION */

static struct abox_data g_abox;
static struct device g_abox_dev;
static struct device g_dma_dev;
static struct abox_dma_data g_dma;
static struct snd_soc_dai g_dai;
static struct snd_soc_dai_ops g_dai_ops;
static struct snd_soc_dai_driver g_dai_driver;
static struct snd_soc_component g_component;
static struct snd_soc_card g_card;
static struct snd_soc_pcm_runtime g_runtime;
static struct snd_pcm_substream g_substream;
static bool g_ipc_lock_initialized;

static void reset_counters(void)
{
	g_queue_work_calls = 0;
	g_flush_work_calls = 0;
	g_mdelay_calls = 0;
	g_failsafe_calls = 0;
	g_send_calls = 0;
	g_send_result = 0;
}

static void reset_world(bool initially_enabled)
{
	if (g_ipc_lock_initialized)
		pthread_mutex_destroy(&g_abox.ipc_queue_lock);
	memset(&g_abox, 0, sizeof(g_abox));
	memset(&g_abox_dev, 0, sizeof(g_abox_dev));
	memset(&g_dma_dev, 0, sizeof(g_dma_dev));
	memset(&g_dma, 0, sizeof(g_dma));
	memset(&g_dai, 0, sizeof(g_dai));
	memset(&g_dai_ops, 0, sizeof(g_dai_ops));
	memset(&g_dai_driver, 0, sizeof(g_dai_driver));
	memset(&g_card, 0, sizeof(g_card));
	memset(&g_runtime, 0, sizeof(g_runtime));
	memset(&g_substream, 0, sizeof(g_substream));
	pthread_mutex_init(&g_abox.ipc_queue_lock, NULL);
	g_ipc_lock_initialized = true;
	g_abox.ipc_workqueue = (struct workqueue_struct *)&g_abox;
	g_abox.dev = &g_abox_dev;
	g_abox_dev.driver_data = &g_abox;
	g_dma_dev.driver_data = &g_dma;
	g_dma.enabled = initially_enabled;
	g_dma.id = 3;
	g_dma.dev = &g_dma_dev;
	g_dma.dev_abox = &g_abox_dev;
	g_dma.substream = &g_substream;
	g_dai.id = 3;
	g_dai.dev = &g_dma_dev;
	g_dai.driver_data = &g_dma;
	g_dai.driver = &g_dai_driver;
	g_dai_ops.mute_stream = abox_rdma_mute_stream;
	g_dai_driver.ops = &g_dai_ops;
	g_runtime.cpu = &g_dai;
	g_runtime.dai = &g_dai;
	g_runtime.card = &g_card;
	g_runtime.dev = &g_dma_dev;
	g_substream.rtd = &g_runtime;
	g_substream.stream = SNDRV_PCM_STREAM_PLAYBACK;
	reset_counters();
	pthread_mutex_lock(&g_gate_lock);
	g_gate_armed = false;
	g_gate_entered = false;
	g_gate_release = false;
	g_fe_thread_ready = false;
	pthread_mutex_unlock(&g_gate_lock);
}

static void seed_queue_full(void)
{
	ABOX_IPC_MSG msg = {0};
	int i;

	msg.ipcid = IPC_SYSTEM;
	for (i = 0; i < ABOX_IPC_QUEUE_SIZE - 1; ++i) {
		msg.task_id = i;
		CHECK(abox_request_ipc(&g_abox_dev, msg.ipcid, &msg,
				sizeof(msg), 0, 0) == 0,
			"test setup accepts a generic queue entry");
	}
	CHECK(g_abox.ipc_queue_end == ABOX_IPC_QUEUE_SIZE - 1 &&
		g_abox.ipc_queue_start == 0,
		"test setup reaches the actual ring full boundary");
	reset_counters();
}

static int queue_depth(void)
{
	if (g_abox.ipc_queue_end >= g_abox.ipc_queue_start)
		return g_abox.ipc_queue_end - g_abox.ipc_queue_start;
	return ABOX_IPC_QUEUE_SIZE - g_abox.ipc_queue_start +
		g_abox.ipc_queue_end;
}

static void test_baseline_failure_contracts(void)
{
	int ret;

	reset_world(false);
	seed_queue_full();
	ret = abox_rdma_trigger(&g_component, &g_substream,
			SNDRV_PCM_TRIGGER_START);
	CHECK(ret == -EBUSY, "failed START reports queue rejection");
	CHECK(!g_dma.enabled, "failed START preserves accepted state");
	ret = abox_rdma_trigger(&g_component, &g_substream,
			SNDRV_PCM_TRIGGER_START);
	CHECK(ret == -EBUSY, "failed START remains explicitly retryable");

	reset_world(true);
	seed_queue_full();
	ret = abox_rdma_trigger(&g_component, &g_substream,
			SNDRV_PCM_TRIGGER_STOP);
	CHECK(ret == -EBUSY, "failed STOP reports queue rejection");
	CHECK(g_dma.enabled, "failed STOP preserves accepted state");
	ret = abox_rdma_trigger(&g_component, &g_substream,
			SNDRV_PCM_TRIGGER_STOP);
	CHECK(ret == -EBUSY, "failed STOP remains explicitly retryable");
}

#ifdef HAS_PCM_TRIGGER_QUEUE_STATE
static void test_failure_retry_start_stop(bool start)
{
	struct abox_ipc item;
	int initial_state = !start;
	int ret;
	int i;

	reset_world(initial_state);
	seed_queue_full();
	ret = abox_rdma_trigger(&g_component, &g_substream,
			start ? SNDRV_PCM_TRIGGER_START : SNDRV_PCM_TRIGGER_STOP);
	CHECK(ret == -EBUSY, "full trigger queue error reaches FE caller");
	CHECK(g_dma.enabled == initial_state,
		"failed trigger leaves last locally accepted state unchanged");
	CHECK(atomic_read_int(&g_queue_work_calls) == 11 &&
		atomic_read_int(&g_mdelay_calls) == 11,
		"atomic queue retry count and scheduling delay remain unchanged");
	CHECK(atomic_read_int(&g_flush_work_calls) == 0,
		"atomic queue failure does not change flush behavior");

	ret = abox_rdma_trigger(&g_component, &g_substream,
			start ? SNDRV_PCM_TRIGGER_START : SNDRV_PCM_TRIGGER_STOP);
	CHECK(ret == -EBUSY, "same-direction caller retry is not suppressed");
	CHECK(atomic_read_int(&g_queue_work_calls) == 22,
		"each explicit retry receives the original scheduling attempts");

	CHECK(abox_ipc_queue_get(&g_abox, &item) == 0,
		"dequeue one older entry to make room for explicit retry");
	ret = abox_rdma_trigger(&g_component, &g_substream,
			start ? SNDRV_PCM_TRIGGER_START : SNDRV_PCM_TRIGGER_STOP);
	CHECK(ret == 0 && g_dma.enabled == start,
		"retry is accepted and commits state with queue publication");
	CHECK(queue_depth() == ABOX_IPC_QUEUE_SIZE - 1,
		"accepted retry adds exactly one item to the ring");
	for (i = 0; i < ABOX_IPC_QUEUE_SIZE - 2; ++i) {
		CHECK(abox_ipc_queue_get(&g_abox, &item) == 0 &&
			item.msg.ipcid == IPC_SYSTEM,
			"older generic queue entries retain their FIFO order");
	}
	CHECK(abox_ipc_queue_get(&g_abox, &item) == 0 &&
		item.msg.ipcid == IPC_PCMPLAYBACK &&
		item.msg.msg.pcmtask.msgtype == PCM_PLTDAI_TRIGGER &&
		item.msg.msg.pcmtask.param.trigger == start,
		"accepted trigger follows older queue entries");
	CHECK(abox_ipc_queue_get(&g_abox, &item) == -ENODATA,
		"queue is empty after FIFO verification");
}

struct fe_thread_args { int result; };

static void *run_fe_start(void *argument)
{
	struct fe_thread_args *args = argument;
	pthread_mutex_lock(&g_gate_lock);
	g_gate_target = pthread_self();
	g_gate_armed = true;
	g_fe_thread_ready = true;
	pthread_cond_broadcast(&g_gate_cond);
	pthread_mutex_unlock(&g_gate_lock);
	args->result = abox_rdma_trigger(&g_component, &g_substream,
			SNDRV_PCM_TRIGGER_START);
	return NULL;
}

static void test_controlled_fe_start_be_stop_fifo(void)
{
	struct fe_thread_args args = {.result = -1};
	struct abox_ipc item;
	pthread_t thread;
	int ret;

	reset_world(false);
	CHECK(pthread_create(&thread, NULL, run_fe_start, &args) == 0,
		"controlled FE worker starts");
	pthread_mutex_lock(&g_gate_lock);
	while (!g_fe_thread_ready || !g_gate_entered)
		pthread_cond_wait(&g_gate_cond, &g_gate_lock);
	pthread_mutex_unlock(&g_gate_lock);

	CHECK(g_dma.enabled,
		"START state is committed before queue scheduling leaves callback");
	ret = abox_rdma_mute_stream(&g_dai, 1, SNDRV_PCM_STREAM_PLAYBACK);
	CHECK(ret == 0 && !g_dma.enabled,
		"overlapping BE STOP is queued and commits after accepted FE START");
	CHECK(queue_depth() == 2,
		"both overlapping directions are present in the queue");

	pthread_mutex_lock(&g_gate_lock);
	g_gate_release = true;
	pthread_cond_broadcast(&g_gate_cond);
	pthread_mutex_unlock(&g_gate_lock);
	CHECK(pthread_join(thread, NULL) == 0,
		"controlled FE worker joins");
	CHECK(args.result == 0, "FE START returns local queue acceptance");
	CHECK(abox_ipc_queue_get(&g_abox, &item) == 0 &&
		item.msg.msg.pcmtask.param.trigger == 1,
		"accepted FE START remains first in FIFO");
	CHECK(abox_ipc_queue_get(&g_abox, &item) == 0 &&
		item.msg.msg.pcmtask.param.trigger == 0,
		"overlapping BE STOP follows FE START in FIFO");
	CHECK(abox_ipc_queue_get(&g_abox, &item) == -ENODATA,
		"only the accepted START and STOP were enqueued");
}

static void test_duplicate_and_generic_api_contracts(void)
{
	ABOX_IPC_MSG trigger = {0};
	int queued_before;
	int sent_before;
	int ret;

	reset_world(false);
	CHECK(abox_rdma_trigger(&g_component, &g_substream,
			SNDRV_PCM_TRIGGER_START) == 0,
		"first FE START is accepted");
	queued_before = atomic_read_int(&g_queue_work_calls);
	CHECK(abox_rdma_trigger(&g_component, &g_substream,
			SNDRV_PCM_TRIGGER_RESUME) == 0,
		"same-direction FE command is a duplicate success");
	CHECK(queue_depth() == 1 &&
		atomic_read_int(&g_queue_work_calls) == queued_before,
		"duplicate does not publish, schedule, or trace a queue item");

	reset_world(false);
	trigger.ipcid = IPC_PCMPLAYBACK;
	trigger.task_id = 3;
	trigger.msg.pcmtask.msgtype = PCM_PLTDAI_TRIGGER;
	trigger.msg.pcmtask.channel_id = 3;
	trigger.msg.pcmtask.param.trigger = 1;
	CHECK(abox_request_ipc(&g_abox_dev, trigger.ipcid, &trigger,
			sizeof(trigger), 0, 0) == 0 && !g_dma.enabled,
		"generic IPC API queues a trigger without owning RDMA state");
	CHECK(abox_request_ipc(&g_abox_dev, trigger.ipcid, &trigger,
			sizeof(trigger), 0, 0) == 0 && queue_depth() == 2,
		"generic IPC API retains its non-deduplicating queue contract");
	sent_before = atomic_read_int(&g_send_calls);
	g_send_result = -EIO;
	ret = abox_request_ipc(&g_abox_dev, trigger.ipcid, &trigger,
			sizeof(trigger), 1, 1);
	CHECK(ret == -EIO && atomic_read_int(&g_send_calls) == sent_before + 1,
		"generic direct atomic synchronous request remains unchanged");
}

static void test_be_error_and_actual_dapm_swallow(void)
{
	struct snd_soc_dapm_widget source = {0};
	struct snd_soc_dapm_widget sink = {.priv = &g_dai};
	struct snd_soc_dapm_widget widget = {.priv = &g_substream};
	struct snd_soc_dapm_path path = {.source = &source, .sink = &sink};
	int ret;

	reset_world(false);
	seed_queue_full();
	ret = snd_soc_dai_digital_mute(&g_dai, 0,
			SNDRV_PCM_STREAM_PLAYBACK);
	CHECK(ret == -EBUSY && !g_dma.enabled,
		"BE digital-mute reports queue error without committing state");
	CHECK(atomic_read_int(&g_queue_work_calls) == 11 &&
		atomic_read_int(&g_flush_work_calls) == 11,
		"BE atomic-context-independent path keeps retry and flush behavior");

	reset_world(false);
	seed_queue_full();
	widget.test_path = &path;
	ret = snd_soc_dai_link_event(&widget, NULL, SND_SOC_DAPM_POST_PMU);
	CHECK(ret == 0,
		"actual DAPM POST_PMU caller swallows BE unmute failure as before");
	CHECK(!g_dma.enabled,
		"swallowed DAPM failure still cannot claim an unqueued START");
}

int main(void)
{
	test_failure_retry_start_stop(true);
	test_failure_retry_start_stop(false);
	test_controlled_fe_start_be_stop_fifo();
	test_duplicate_and_generic_api_contracts();
	test_be_error_and_actual_dapm_swallow();
	if (g_failures) {
		fprintf(stderr, "%d host assertions failed\n", g_failures);
		return 1;
	}
	puts("PASS: extracted ABOX FE/BE queue, retry, FIFO, and DAPM paths");
	return 0;
}
#else
int main(void)
{
	test_baseline_failure_contracts();
	if (g_failures) {
		fprintf(stderr, "%d expected baseline assertions failed\n",
			g_failures);
		return 1;
	}
	puts("FAIL: baseline unexpectedly passed stale-state checks");
	return 2;
}
#endif
