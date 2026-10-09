/*
 * Host-only execution of selected GPL-2.0 callback bodies from the pinned
 * Samsung kernel source. Hardware and kernel services are replaced with
 * explicit test shims; this does not execute Linux or validate a fix.
 */
#include <errno.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <limits.h>

#define CONFIG_DSP_USE_VS4L 1
#define CONFIG_NPU_USE_BOOT_IOCTL 1
#define NPU_HWDEV_ID_DNC 0x1
#define NPU_HWDEV_ID_NPU 0x2
#define NPU_HWDEV_ID_DSP 0x4
#define NPU_STM_ENABLED 1
#define NPU_FW_LOAD_SUCCESS 1
#define NPU_STM_NEED_BUSP_DIV 0
#define npu_info(...) ((void)0)
#define npu_err(...) ((void)0)
#define probe_err(...) ((void)0)
#define dsp_enter() ((void)0)
#define dsp_leave() ((void)0)
#define dsp_warn(...) ((void)0)
#define BUG_ON(value) do { if (value) abort(); } while (0)
#define mutex_lock pthread_mutex_lock
#define mutex_unlock pthread_mutex_unlock

typedef uint32_t u32;
typedef uint32_t __u32;
typedef struct { atomic_int value; } atomic_t;

struct npu_device;
struct npu_hw_device;
struct npu_system { unsigned int fw_load_success; };
struct dsp_kernel_manager { pthread_mutex_t lock; unsigned int dl_init; };
struct npu_device {
	struct npu_system system;
	struct dsp_kernel_manager kmgr;
	bool emergency;
};
struct npu_hw_refcount {
	atomic_t refcount;
	struct npu_hw_device *hdev;
	int (*first)(struct npu_device *, struct npu_hw_device *);
	int (*final)(struct npu_device *, struct npu_hw_device *);
};
struct npu_hw_ops {
	int (*init)(struct npu_hw_device *, bool);
	int (*boot)(struct npu_hw_device *, bool);
};
struct npu_hw_device {
	char *name;
	char *parent;
	uint32_t id;
	struct npu_device *device;
	struct npu_hw_refcount boot_cnt;
	struct npu_hw_refcount init_cnt;
	struct npu_hw_ops ops;
};
struct npu_stm_data_shim {
	atomic_t enabled;
	atomic_t guarantee;
	atomic_t guarantee_working;
	unsigned int enable_cnt;
};

static struct npu_stm_data_shim npu_stm_data;
static struct npu_hw_device *g_hwdev_list[3];
static int g_hwdev_num = 3;
static int binary_load_result;
static int manager_open_result;
static int manager_dl_deinit_calls;
static pthread_mutex_t test_gate_lock = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t test_gate_cond = PTHREAD_COND_INITIALIZER;
static bool gate_entered;
static bool gate_released;

static int atomic_inc_return(atomic_t *value)
{
	return atomic_fetch_add_explicit(&value->value, 1, memory_order_seq_cst) + 1;
}

static int atomic_dec_return(atomic_t *value)
{
	return atomic_fetch_sub_explicit(&value->value, 1, memory_order_seq_cst) - 1;
}

static int atomic_read(const atomic_t *value)
{
	return atomic_load_explicit(&value->value, memory_order_seq_cst);
}

static void atomic_set(atomic_t *value, int next)
{
	atomic_store_explicit(&value->value, next, memory_order_seq_cst);
}

static int atomic_cmpxchg(atomic_t *value, int old, int next)
{
	atomic_compare_exchange_strong_explicit(&value->value, &old, next,
			memory_order_seq_cst, memory_order_seq_cst);
	return old;
}

static struct npu_hw_device *npu_get_hdev(char *name)
{
	for (int i = 0; i < g_hwdev_num; i++)
		if (g_hwdev_list[i] && g_hwdev_list[i]->name &&
		    strcmp(g_hwdev_list[i]->name, name) == 0)
			return g_hwdev_list[i];
	return NULL;
}

static int dsp_system_load_binary(struct npu_system *system)
{
	(void)system;
	return binary_load_result;
}

static int dsp_kernel_manager_open(struct npu_system *system,
		struct dsp_kernel_manager *manager)
{
	(void)system;
	if (!manager_open_result)
		manager->dl_init = 1;
	return manager_open_result;
}

static void __dsp_kernel_manager_dl_deinit(struct dsp_kernel_manager *manager)
{
	(void)manager;
	manager_dl_deinit_calls++;
}

static int npu_cmd_map(struct npu_system *system, const char *name)
{
	(void)system;
	(void)name;
	return 0;
}

static int get_stm_trace_set(void) { return 0; }
static void npu_stm_set_stm_sel(struct npu_system *system, u32 selection)
{
	(void)system;
	(void)selection;
}
static int __busp_div_1to1(struct npu_system *system, int enable)
{
	(void)system;
	(void)enable;
	return 0;
}
static int __busp_div_1to4(struct npu_system *system, int enable)
{
	(void)system;
	(void)enable;
	return 0;
}
static int __npu_stm_sync(struct npu_system *system)
{
	(void)system;
	return 0;
}
static int npu_enable_stm_sfr(struct npu_system *system, int hid);
int npu_stm_disable(struct npu_system *system, int hid);
void dsp_kernel_manager_close(struct dsp_kernel_manager *manager,
		unsigned int count);
static void npu_update_stm_ts_stat(void) { }

#include "npu-hwdev-callbacks-extracted.inc"

static int npu_enable_stm_sfr(struct npu_system *system, int hid)
{
	return __enable_npu_stm_sfr(system, (u32)get_stm_trace_set(), hid);
}

static int init_first(struct npu_device *device, struct npu_hw_device *hdev)
{
	return npu_hw_ref_init(device, hdev);
}

static int init_final(struct npu_device *device, struct npu_hw_device *hdev)
{
	return npu_hw_ref_deinit(device, hdev);
}

static void configure_device(struct npu_device *device,
		struct npu_hw_device devices[3])
{
	memset(device, 0, sizeof(*device));
	memset(devices, 0, sizeof(*devices) * 3);
	pthread_mutex_init(&device->kmgr.lock, NULL);
	device->system.fw_load_success = NPU_FW_LOAD_SUCCESS;
	for (int i = 0; i < 3; i++) {
		devices[i].device = device;
		devices[i].id = (uint32_t)(1U << i);
		devices[i].name = i == 0 ? "DNC" : i == 1 ? "NPU" : "DSP";
		devices[i].parent = i == 0 ? NULL : "DNC";
		devices[i].boot_cnt.hdev = &devices[i];
		devices[i].init_cnt.hdev = &devices[i];
		devices[i].init_cnt.first = init_first;
		devices[i].init_cnt.final = init_final;
	}
	devices[0].ops.init = npu_hwdev_dnc_init;
	devices[1].ops.init = npu_hwdev_npu_init;
	devices[2].ops.init = npu_hwdev_dsp_init;
	for (int i = 0; i < 3; i++)
		g_hwdev_list[i] = &devices[i];
	atomic_set(&npu_stm_data.enabled, NPU_STM_ENABLED);
	atomic_set(&npu_stm_data.guarantee, 0);
	atomic_set(&npu_stm_data.guarantee_working, 0);
	npu_stm_data.enable_cnt = 0;
	binary_load_result = 0;
	manager_open_result = -EIO;
	manager_dl_deinit_calls = 0;
}

static void expect(bool condition, const char *message)
{
	if (!condition) {
		fprintf(stderr, "FAIL: %s\n", message);
		exit(1);
	}
}

static void test_failed_dsp_init_and_unsafe_inverse(void)
{
	struct npu_device device;
	struct npu_hw_device devices[3];
	int ret;

	configure_device(&device, devices);
	ret = npu_hwdev_bootup(&device, NPU_HWDEV_ID_DSP);
	expect(ret == 0, "baseline bootup no longer ignores DSP init callback error");
	expect(atomic_read(&devices[2].init_cnt.refcount) == 1,
	       "failed DSP first acquisition must retain its incremented refcount");
	expect(atomic_read(&devices[0].init_cnt.refcount) == 1,
	       "failed DSP init must retain its acquired parent DNC refcount");
	expect(device.kmgr.dl_init == 0 && manager_dl_deinit_calls == 0,
	       "failed manager-open fixture must have no initialized manager to close");
	expect(npu_stm_data.enable_cnt == 0,
	       "failed DSP init must precede any matching STM enable");

	ret = npu_hw_ref_put(&device, &devices[2].init_cnt);
	expect(ret == 0, "generic inverse callback unexpectedly failed");
	expect(atomic_read(&devices[2].init_cnt.refcount) == 0 &&
	       atomic_read(&devices[0].init_cnt.refcount) == 0,
	       "generic inverse did not release DSP and parent reference counts");
	expect(npu_stm_data.enable_cnt == UINT_MAX - 1U,
	       "actual STM callback sequence should wrap after two unmatched disables");
	puts("REPRO actual C: failed DSP acquire is hidden; generic inverse leaves STM count UINT_MAX-1");
}

static void test_failed_dsp_inverse_consumes_active_npu_owner(void)
{
	struct npu_device device;
	struct npu_hw_device devices[3];
	int ret;

	configure_device(&device, devices);
	ret = npu_hw_ref_get(&device, &devices[1].init_cnt);
	expect(ret == 0 && atomic_read(&devices[0].init_cnt.refcount) == 1,
	       "NPU owner setup failed to acquire DNC parent");
	ret = npu_stm_enable(&device.system, NPU_HWDEV_ID_NPU);
	expect(ret == 0 && npu_stm_data.enable_cnt == 2,
	       "actual NPU STM-enable path should establish its two count units");

	ret = npu_hwdev_bootup(&device, NPU_HWDEV_ID_DSP);
	expect(ret == 0 && atomic_read(&devices[2].init_cnt.refcount) == 1,
	       "failed DSP callback must still be hidden by baseline bootup");
	expect(atomic_read(&devices[0].init_cnt.refcount) == 2,
	       "DSP attempt should hold a second shared DNC init reference");

	ret = npu_hw_ref_put(&device, &devices[2].init_cnt);
	expect(ret == 0 && npu_stm_data.enable_cnt == 1,
	       "failed DSP inverse should consume one active NPU STM count unit");
	expect(atomic_read(&devices[0].init_cnt.refcount) == 1,
	       "failed DSP inverse should release only its parent DNC reference");
	ret = npu_hw_ref_put(&device, &devices[1].init_cnt);
	expect(ret == 0 && npu_stm_data.enable_cnt == UINT_MAX,
	       "later normal NPU/DNC teardown should expose the unmatched STM decrements");
	puts("REPRO actual C: failed DSP inverse consumes active NPU STM ownership; later teardown wraps");
}

static int stalled_first(struct npu_device *device, struct npu_hw_device *hdev)
{
	(void)device;
	(void)hdev;
	pthread_mutex_lock(&test_gate_lock);
	gate_entered = true;
	pthread_cond_broadcast(&test_gate_cond);
	while (!gate_released)
		pthread_cond_wait(&test_gate_cond, &test_gate_lock);
	pthread_mutex_unlock(&test_gate_lock);
	return -EIO;
}

struct get_job {
	struct npu_device *device;
	struct npu_hw_refcount *ref;
	int result;
};

static void *get_thread(void *opaque)
{
	struct get_job *job = opaque;
	job->result = npu_hw_ref_get(job->device, job->ref);
	return NULL;
}

static void test_concurrent_get_observes_failed_acquisition_as_success(void)
{
	struct npu_device device = {0};
	struct npu_hw_device hdev = {0};
	struct npu_hw_refcount ref = {0};
	struct get_job first = { .device = &device, .ref = &ref, .result = 1 };
	struct get_job second = { .device = &device, .ref = &ref, .result = 1 };
	pthread_t first_thread;
	pthread_t second_thread;

	ref.hdev = &hdev;
	ref.first = stalled_first;
	atomic_set(&ref.refcount, 0);
	gate_entered = false;
	gate_released = false;
	expect(pthread_create(&first_thread, NULL, get_thread, &first) == 0,
	       "could not start first get thread");
	pthread_mutex_lock(&test_gate_lock);
	while (!gate_entered)
		pthread_cond_wait(&test_gate_cond, &test_gate_lock);
	pthread_mutex_unlock(&test_gate_lock);
	expect(atomic_read(&ref.refcount) == 1,
	       "first failing callback should run after the refcount increment");
	expect(pthread_create(&second_thread, NULL, get_thread, &second) == 0,
	       "could not start second get thread");
	expect(pthread_join(second_thread, NULL) == 0,
	       "second get thread did not return");
	expect(second.result == 0 && atomic_read(&ref.refcount) == 2,
	       "concurrent get should report success while first callback is pending");
	pthread_mutex_lock(&test_gate_lock);
	gate_released = true;
	pthread_cond_broadcast(&test_gate_cond);
	pthread_mutex_unlock(&test_gate_lock);
	expect(pthread_join(first_thread, NULL) == 0,
	       "first get thread did not return");
	expect(first.result == -EIO && atomic_read(&ref.refcount) == 2,
	       "failed first callback should leave both increments outstanding");
	puts("REPRO actual C helper: concurrent get succeeds before first callback fails; refcount remains 2");
}

int main(void)
{
	test_failed_dsp_init_and_unsafe_inverse();
	test_failed_dsp_inverse_consumes_active_npu_owner();
	test_concurrent_get_observes_failed_acquisition_as_success();
	puts("LIMIT: exact source bodies ran with host atomics, mutex/STM/MMIO/firmware shims; no kernel or hardware executed");
	return 0;
}
