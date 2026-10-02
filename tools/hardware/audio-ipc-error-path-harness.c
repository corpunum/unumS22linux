/* Host test template. The test injects the two extracted production functions. */
#include <stdbool.h>
#include <stdio.h>
#include <string.h>

#define EBUSY 16
#define EINVAL 22
#define IPC_PCMPLAYBACK 1
#define PCM_PLTDAI_TRIGGER 2
#define SNDRV_PCM_TRIGGER_START 10
#define SNDRV_PCM_TRIGGER_RESUME 11
#define SNDRV_PCM_TRIGGER_PAUSE_RELEASE 12
#define SNDRV_PCM_TRIGGER_STOP 13
#define SNDRV_PCM_TRIGGER_SUSPEND 14
#define SNDRV_PCM_TRIGGER_PAUSE_PUSH 15

struct device { int unused; };
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
struct completion { bool done; };
struct abox_dma_data {
	bool enabled;
	int id;
	struct device *dev;
	struct completion func_changed;
};
struct snd_soc_component { int unused; };
struct snd_soc_dai { struct abox_dma_data *data; };
struct snd_soc_pcm_runtime { struct snd_soc_dai *cpu; };
struct snd_pcm_substream {
	bool backend;
	struct snd_soc_pcm_runtime *runtime;
};

#define asoc_substream_to_rtd(substream) ((substream)->runtime)
#define asoc_rtd_to_cpu(runtime, index) ((void)(index), (runtime)->cpu)
#define snd_soc_dai_get_drvdata(dai) ((dai)->data)
#define abox_info(device, ...) ((void)(device))

static int failures;
#define CHECK(condition, label) do { \
	if (!(condition)) { \
		fprintf(stderr, "FAIL: %s\n", label); \
		++failures; \
	} \
} while (0)

static int request_result;
static unsigned int request_calls;
static int last_atomic;
static int last_sync;
static struct ABOX_IPC_MSG last_msg;

static int abox_rdma_request_ipc(struct abox_dma_data *data,
		struct ABOX_IPC_MSG *msg, int atomic, int sync)
{
	(void)data;
	++request_calls;
	last_atomic = atomic;
	last_sync = sync;
	last_msg = *msg;
	return request_result;
}

static bool abox_rdma_backend(struct snd_pcm_substream *substream)
{
	return substream->backend;
}

static bool completion_done(struct completion *completion)
{
	return completion->done;
}

static void complete(struct completion *completion)
{
	completion->done = true;
}

/* ACTUAL_TRIGGER_IPC_FUNCTION */
/* ACTUAL_TRIGGER_CALLER_FUNCTION */

static void reset_request(int result)
{
	request_result = result;
	request_calls = 0;
	last_atomic = -1;
	last_sync = -1;
	memset(&last_msg, 0, sizeof(last_msg));
}

static void test_start_failure_reaches_caller_and_remains_retryable(void)
{
	struct device device = {0};
	struct abox_dma_data data = {.enabled = false, .id = 3, .dev = &device};
	struct snd_soc_dai dai = {.data = &data};
	struct snd_soc_pcm_runtime runtime = {.cpu = &dai};
	struct snd_pcm_substream substream = {.runtime = &runtime};
	struct snd_soc_component component = {0};

	reset_request(-EBUSY);
	CHECK(abox_rdma_trigger(&component, &substream,
			SNDRV_PCM_TRIGGER_START) == -EBUSY,
		"START propagates queue rejection to its caller");
	CHECK(!data.enabled, "failed START preserves the last accepted state");
	CHECK(request_calls == 1 && last_atomic == 1 && last_sync == 0,
		"START uses the original atomic asynchronous queue path");
	CHECK(last_msg.ipcid == IPC_PCMPLAYBACK && last_msg.task_id == 3 &&
		last_msg.msg.pcmtask.channel_id == 3 &&
		last_msg.msg.pcmtask.msgtype == PCM_PLTDAI_TRIGGER &&
		last_msg.msg.pcmtask.param.trigger == 1,
		"START request fields are preserved");

	reset_request(0);
	CHECK(abox_rdma_trigger(&component, &substream,
			SNDRV_PCM_TRIGGER_START) == 0,
		"a caller retry after explicit START request succeeds");
	CHECK(data.enabled && request_calls == 1,
		"failed START did not suppress the later request");
	CHECK(last_msg.msg.pcmtask.param.trigger == 1,
		"retry still requests START");
}

static void test_stop_failure_reaches_caller_and_remains_retryable(void)
{
	struct device device = {0};
	struct abox_dma_data data = {.enabled = true, .id = 5, .dev = &device};
	struct snd_soc_dai dai = {.data = &data};
	struct snd_soc_pcm_runtime runtime = {.cpu = &dai};
	struct snd_pcm_substream substream = {.runtime = &runtime};
	struct snd_soc_component component = {0};

	reset_request(-EBUSY);
	CHECK(abox_rdma_trigger(&component, &substream,
			SNDRV_PCM_TRIGGER_STOP) == -EBUSY,
		"STOP propagates queue rejection to its caller");
	CHECK(data.enabled, "failed STOP preserves the last accepted state");
	CHECK(request_calls == 1 && last_atomic == 1 && last_sync == 0,
		"STOP uses the original atomic asynchronous queue path");
	CHECK(last_msg.msg.pcmtask.param.trigger == 0,
		"STOP request fields are preserved");

	reset_request(0);
	CHECK(abox_rdma_trigger(&component, &substream,
			SNDRV_PCM_TRIGGER_STOP) == 0,
		"a caller retry after explicit STOP request succeeds");
	CHECK(!data.enabled && request_calls == 1,
		"failed STOP did not suppress the later request");
	CHECK(last_msg.msg.pcmtask.param.trigger == 0,
		"retry still requests STOP");
}

static void test_success_duplicate_backend_and_invalid_commands(void)
{
	struct device device = {0};
	struct abox_dma_data data = {.enabled = false, .id = 6, .dev = &device};
	struct snd_soc_dai dai = {.data = &data};
	struct snd_soc_pcm_runtime runtime = {.cpu = &dai};
	struct snd_pcm_substream substream = {.runtime = &runtime};
	struct snd_soc_component component = {0};

	reset_request(0);
	CHECK(abox_rdma_trigger(&component, &substream,
			SNDRV_PCM_TRIGGER_RESUME) == 0 && data.enabled,
		"successful RESUME commits the accepted START state");
	CHECK(request_calls == 1, "RESUME publishes one trigger request");
	CHECK(abox_rdma_trigger(&component, &substream,
			SNDRV_PCM_TRIGGER_PAUSE_RELEASE) == 0 && request_calls == 1,
		"duplicate START remains elided");
	CHECK(abox_rdma_trigger(&component, &substream,
			SNDRV_PCM_TRIGGER_PAUSE_PUSH) == 0 && !data.enabled &&
			request_calls == 2,
		"PAUSE_PUSH publishes STOP and commits accepted state");
	CHECK(abox_rdma_trigger(&component, &substream,
			SNDRV_PCM_TRIGGER_SUSPEND) == 0 && request_calls == 2,
		"duplicate STOP remains elided");

	CHECK(abox_rdma_trigger(&component, &substream, 999) == -EINVAL &&
		request_calls == 2 && !data.enabled,
		"invalid command does not publish or change state");

	substream.backend = true;
	CHECK(abox_rdma_trigger(&component, &substream,
			SNDRV_PCM_TRIGGER_START) == 0 && request_calls == 2 &&
			!data.enabled,
		"backend caller retains its existing no-op path");
}

int main(void)
{
	test_start_failure_reaches_caller_and_remains_retryable();
	test_stop_failure_reaches_caller_and_remains_retryable();
	test_success_duplicate_backend_and_invalid_commands();
	if (failures) {
		fprintf(stderr, "%d host assertions failed\n", failures);
		return 1;
	}
	puts("PASS: extracted trigger helper and caller scenarios");
	return 0;
}
