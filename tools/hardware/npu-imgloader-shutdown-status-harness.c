/* Actual pinned provider and NPU functions are injected by the host runner. */
#include <errno.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef uint8_t u8;
typedef uint32_t u32;
typedef uint64_t phys_addr_t;

#ifndef TEST_S2MPU_CONFIG
#define TEST_S2MPU_CONFIG 1
#endif
#define CONFIG_EXYNOS_S2MPU TEST_S2MPU_CONFIG
#define IS_ENABLED(option) (option)
#define CONFIG_PM_SLEEP 1
#define CONFIG_NPU_HARDWARE 1

#ifdef TEST_BOOT_IOCTL
#define CONFIG_NPU_USE_BOOT_IOCTL 1
#endif
#ifdef TEST_SECURE_MODE
#define CONFIG_NPU_SECURE_MODE 1
#endif

#define NPU_DEVICE_ERR_STATE_EMERGENCY 3
#define container_of(pointer, type, member) \
	((type *)((char *)(pointer) - offsetof(type, member)))

#define BUG_ON(condition) do { \
	if (condition) { \
		fprintf(stderr, "unexpected BUG_ON at %s:%d\n", __FILE__, __LINE__); \
		exit(3); \
	} \
} while (0)
#define npu_err(...) ((void)0)
#define npu_info(...) ((void)0)
#define npu_warn(...) ((void)0)
#define imgloader_err(...) ((void)0)
#define EXPORT_SYMBOL(symbol)

#define REQUIRE(condition, message) do { \
	if (!(condition)) { \
		fprintf(stderr, "FAIL: %s (%s:%d)\n", message, __FILE__, __LINE__); \
		exit(2); \
	} \
} while (0)

static inline int test_bit(unsigned int bit, const unsigned long *value)
{
	return !!(*value & (1UL << bit));
}

static inline void set_bit(unsigned int bit, unsigned long *value)
{
	*value |= 1UL << bit;
}

static inline int test_and_set_bit(unsigned int bit, unsigned long *value)
{
	unsigned long mask = 1UL << bit;
	unsigned long old = __atomic_fetch_or(value, mask, __ATOMIC_SEQ_CST);

	return !!(old & mask);
}

static inline void clear_bit(unsigned int bit, unsigned long *value)
{
	*value &= ~(1UL << bit);
}

struct device {
	void *driver_data;
};

struct platform_device {
	struct device dev;
};

struct mailbox_hdr {
	uint32_t warm_boot_enable;
};

/* ACTUAL_IMGLOADER_TYPES */

struct npu_binary {
	struct imgloader_desc imgloader;
};

struct npu_memory {
	unsigned int token;
};

struct npu_clocks {
	unsigned int token;
};

struct wakeup_source {
	int active;
};

struct npu_system {
	struct platform_device *pdev;
	struct npu_binary binary;
	struct npu_memory memory;
	struct npu_clocks clks;
	struct wakeup_source *ws;
	volatile struct mailbox_hdr *mbox_hdr;
	unsigned long resume_steps;
	unsigned long resume_soc_steps;
	bool fw_cold_boot;
};

struct npu_device {
	struct npu_system system;
	struct device *dev;
	void *sched;
	unsigned long err_state;
};

/* ACTUAL_NPU_RESUME_ENUMS */

static struct npu_device g_device;
static struct platform_device g_platform;
static struct wakeup_source g_wake_source;
static struct mailbox_hdr g_mailbox;

static int g_permission_error;
static int g_callback_error;
static int g_notify_error;
static unsigned int g_permission_calls;
static unsigned int g_callback_calls;
static unsigned int g_notify_calls;
static unsigned int g_interface_close_calls;
static unsigned int g_stm_disable_calls;
static unsigned int g_cpu_off_calls;
static unsigned int g_clock_disable_calls;
static unsigned int g_fwbuf_free_calls;
static unsigned int g_wake_unlock_calls;
static unsigned int g_memory_open_calls;
static unsigned int g_qos_open_calls;
static unsigned int g_scheduler_open_calls;
static unsigned int g_qos_close_calls;
static unsigned int g_scheduler_close_calls;
static unsigned int g_memory_close_calls;
static unsigned int g_imgloader_release_calls;
static unsigned int g_provider_callback_calls;
static unsigned int g_provider_notify_calls;
static unsigned int g_provider_cpu_off_calls;
static unsigned int g_provider_stm_disable_calls;
static unsigned int g_provider_interface_close_calls;
static unsigned int g_provider_clock_disable_calls;
static unsigned int g_provider_wake_unlock_calls;
static bool g_reenter_suspend;
static int g_reenter_suspend_ret;
static char g_events[64];
static size_t g_event_count;

int npu_system_suspend(struct npu_system *system);

static void record_event(char event)
{
	REQUIRE(g_event_count < sizeof(g_events) - 1, "event buffer bound");
	g_events[g_event_count++] = event;
	g_events[g_event_count] = '\0';
}

static int exynos_s2mpu_release_fw_stage2_ap(const char *name,
		unsigned int fw_id)
{
	(void)name;
	(void)fw_id;
	++g_permission_calls;
	record_event('P');
	if (g_reenter_suspend) {
		g_reenter_suspend = false;
		g_reenter_suspend_ret = npu_system_suspend(&g_device.system);
	}
	return g_permission_error;
}

static int imgloader_notify(struct imgloader_desc *desc, char *status)
{
	(void)status;
	++g_notify_calls;
	++g_provider_notify_calls;
	record_event('N');
	if (!desc->notify_signal)
		return 0;
	return g_notify_error;
}

static int injected_shutdown(struct imgloader_desc *desc)
{
	(void)desc;
	++g_callback_calls;
	++g_provider_callback_calls;
	record_event('C');
	return g_callback_error;
}

/* ACTUAL_IMGLOADER_RELEASE_PERMISSION */
/* ACTUAL_IMGLOADER_SHUTDOWN_API */

static struct imgloader_ops g_npu_imgloader_ops = {
	.shutdown = NULL,
};

static struct imgloader_ops g_injected_imgloader_ops = {
	.shutdown = injected_shutdown,
};

static void *g_event_marker(void)
{
	/* Function pointer type checks guard the existing provider API signature. */
	static void (*legacy_signature)(struct imgloader_desc *)
		__attribute__((unused)) = imgloader_shutdown;
#ifndef EXPECT_BASELINE
	static int (*status_signature)(struct imgloader_desc *)
		__attribute__((unused)) = imgloader_shutdown_status;
#endif
	return legacy_signature;
}

static int npu_interface_close(struct npu_system *system)
{
	(void)system;
	++g_interface_close_calls;
	++g_provider_interface_close_calls;
	record_event('I');
	return 0;
}

static int npu_stm_disable(struct npu_system *system, int hid)
{
	(void)system;
	(void)hid;
	++g_stm_disable_calls;
	++g_provider_stm_disable_calls;
	record_event('S');
	return 0;
}

static int npu_cpu_off(struct npu_system *system)
{
	(void)system;
	++g_cpu_off_calls;
	++g_provider_cpu_off_calls;
	record_event('D');
	return 0;
}

static void npu_clk_disable_unprepare(struct npu_clocks *clks)
{
	(void)clks;
	++g_clock_disable_calls;
	++g_provider_clock_disable_calls;
	record_event('K');
}

static int npu_system_free_fw_dram_log_buf(void)
{
	++g_fwbuf_free_calls;
	record_event('B');
	return 0;
}

static int npu_wake_lock_active(struct wakeup_source *ws)
{
	return ws->active;
}

static void npu_wake_unlock(struct wakeup_source *ws)
{
	ws->active = 0;
	++g_wake_unlock_calls;
	++g_provider_wake_unlock_calls;
	record_event('W');
}

static int npu_memory_open(struct npu_memory *memory)
{
	(void)memory;
	++g_memory_open_calls;
	return 0;
}

static int npu_util_memdump_open(struct npu_system *system)
{
	(void)system;
	return 0;
}

static int npu_scheduler_open(struct npu_device *device)
{
	(void)device;
	++g_scheduler_open_calls;
	return 0;
}

static void npu_scheduler_boost_on(void *sched)
{
	(void)sched;
}

static int npu_qos_open(struct npu_system *system)
{
	(void)system;
	++g_qos_open_calls;
	return 0;
}

static int npu_qos_close(struct npu_system *system)
{
	(void)system;
	++g_qos_close_calls;
	return 0;
}

static int npu_scheduler_close(struct npu_device *device)
{
	(void)device;
	++g_scheduler_close_calls;
	return 0;
}

static int npu_memory_close(struct npu_memory *memory)
{
	(void)memory;
	++g_memory_close_calls;
	return 0;
}

static void npu_llc_close(void *sched)
{
	(void)sched;
}

/* ACTUAL_NPU_IMGLOADER_WRAPPERS */
/* ACTUAL_NPU_OPEN_CLOSE */
/* ACTUAL_NPU_SOC_SUSPEND_AND_SUSPEND */

static unsigned long stage(enum npu_system_resume_steps bit)
{
	return 1UL << bit;
}

static unsigned long soc_stage(enum npu_system_resume_soc_steps bit)
{
	return 1UL << bit;
}

static void fixture_reset(bool s2mpu_support, bool callback, bool notify)
{
	memset(&g_device, 0, sizeof(g_device));
	memset(&g_platform, 0, sizeof(g_platform));
	memset(&g_wake_source, 0, sizeof(g_wake_source));
	memset(&g_mailbox, 0, sizeof(g_mailbox));
	g_permission_error = 0;
	g_callback_error = 0;
	g_notify_error = 0;
	g_permission_calls = 0;
	g_callback_calls = 0;
	g_notify_calls = 0;
	g_interface_close_calls = 0;
	g_stm_disable_calls = 0;
	g_cpu_off_calls = 0;
	g_clock_disable_calls = 0;
	g_fwbuf_free_calls = 0;
	g_wake_unlock_calls = 0;
	g_memory_open_calls = 0;
	g_qos_open_calls = 0;
	g_scheduler_open_calls = 0;
	g_qos_close_calls = 0;
	g_scheduler_close_calls = 0;
	g_memory_close_calls = 0;
	g_imgloader_release_calls = 0;
	g_provider_callback_calls = 0;
	g_provider_notify_calls = 0;
	g_provider_cpu_off_calls = 0;
	g_provider_stm_disable_calls = 0;
	g_provider_interface_close_calls = 0;
	g_provider_clock_disable_calls = 0;
	g_provider_wake_unlock_calls = 0;
	g_reenter_suspend = false;
	g_reenter_suspend_ret = 0;
	memset(g_events, 0, sizeof(g_events));
	g_event_count = 0;
	g_device.dev = &g_platform.dev;
	g_platform.dev.driver_data = &g_device;
	g_device.system.pdev = &g_platform;
	g_device.system.ws = &g_wake_source;
	g_device.system.mbox_hdr = &g_mailbox;
	g_device.system.fw_cold_boot = true;
	g_device.system.binary.imgloader.name = "npu";
	g_device.system.binary.imgloader.fw_id = 9;
	g_device.system.binary.imgloader.s2mpu_support = s2mpu_support;
	g_device.system.binary.imgloader.notify_signal = notify;
	g_device.system.binary.imgloader.ops = callback ?
		&g_injected_imgloader_ops : &g_npu_imgloader_ops;
	g_wake_source.active = 1;
	/* Model a completed resume on the known-on SoC route. */
	g_device.system.resume_steps =
		stage(NPU_SYS_RESUME_SETUP_WAKELOCK) |
		stage(NPU_SYS_RESUME_INIT_FWBUF) |
		stage(NPU_SYS_RESUME_FW_LOAD) |
		stage(NPU_SYS_RESUME_CLK_PREPARE) |
		stage(NPU_SYS_RESUME_FW_VERIFY) |
		stage(NPU_SYS_RESUME_SOC) |
		stage(NPU_SYS_RESUME_OPEN_INTERFACE) |
		stage(NPU_SYS_RESUME_COMPLETED);
	g_device.system.resume_soc_steps =
		soc_stage(NPU_SYS_RESUME_SOC_CPU_ON) |
		soc_stage(NPU_SYS_RESUME_SOC_COMPLETED);
#ifndef CONFIG_NPU_USE_BOOT_IOCTL
	g_device.system.resume_soc_steps |= soc_stage(NPU_SYS_RESUME_SOC_STM);
#endif
}

#ifndef EXPECT_BASELINE
static void require_quarantine_and_no_retry(int expected_error,
		unsigned int expected_callback_calls,
		unsigned int expected_notify_calls)
{
	struct npu_system *system = &g_device.system;
	unsigned long resume_steps = system->resume_steps;
	unsigned long soc_steps = system->resume_soc_steps;
	unsigned int permissions = g_permission_calls;
	unsigned int callbacks = g_callback_calls;
	unsigned int notifies = g_notify_calls;
	unsigned int closes = g_interface_close_calls;
	unsigned int cpu_off = g_cpu_off_calls;
	unsigned int stm_off = g_stm_disable_calls;
	unsigned int clocks = g_clock_disable_calls;
	unsigned int frees = g_fwbuf_free_calls;
	unsigned int unlocks = g_wake_unlock_calls;
	unsigned int opens = g_memory_open_calls;
	unsigned int qos_closes = g_qos_close_calls;
	unsigned int scheduler_closes = g_scheduler_close_calls;
	unsigned int memory_closes = g_memory_close_calls;

	REQUIRE(expected_error != 0, "failure fixture uses a nonzero provider error");
	REQUIRE(test_bit(NPU_SYS_RESUME_FW_LOAD, &system->resume_steps),
		"provider failure preserves firmware-load ownership");
	REQUIRE(test_bit(NPU_SYS_RESUME_FW_SHUTDOWN_UNCERTAIN,
		&system->resume_steps), "provider failure latches one-shot uncertainty");
	REQUIRE(test_bit(NPU_SYS_RESUME_SOC, &system->resume_steps) &&
		system->resume_soc_steps != 0 &&
		test_bit(NPU_SYS_RESUME_CLK_PREPARE, &system->resume_steps) &&
		test_bit(NPU_SYS_RESUME_FW_VERIFY, &system->resume_steps) &&
		test_bit(NPU_SYS_RESUME_INIT_FWBUF, &system->resume_steps) &&
		test_bit(NPU_SYS_RESUME_SETUP_WAKELOCK, &system->resume_steps),
		"provider failure preserves all later-stage owner markers");
	REQUIRE(g_wake_source.active && g_cpu_off_calls == 0 &&
		g_stm_disable_calls == 0 && g_clock_disable_calls == 0 &&
		g_fwbuf_free_calls == 0 && g_wake_unlock_calls == 0,
		"provider failure stops before SoC, clock, buffer, and wake teardown");
	REQUIRE(g_permission_calls == 1 &&
		g_callback_calls == expected_callback_calls &&
		g_notify_calls == expected_notify_calls &&
		g_interface_close_calls == 1,
		"provider substeps and earlier interface close each run once");

	REQUIRE(npu_system_suspend(system) == -EUCLEAN,
		"second suspend refuses ambiguous provider shutdown");
	REQUIRE(npu_system_open(system) == -EBUSY,
		"open refuses to reopen while provider status is uncertain");
	REQUIRE(npu_system_close(system) == -EBUSY,
		"close refuses to tear down provider-owned state");
	REQUIRE(system->resume_steps == resume_steps &&
		system->resume_soc_steps == soc_steps &&
		g_permission_calls == permissions &&
		g_callback_calls == callbacks && g_notify_calls == notifies &&
		g_interface_close_calls == closes && g_cpu_off_calls == cpu_off &&
		g_stm_disable_calls == stm_off && g_clock_disable_calls == clocks &&
		g_fwbuf_free_calls == frees && g_wake_unlock_calls == unlocks,
		"rejected repeat cleanup makes no irreversible call or owner change");
	REQUIRE(g_memory_open_calls == opens && g_qos_open_calls == 0 &&
		g_scheduler_open_calls == 0 && g_qos_close_calls == qos_closes &&
		g_scheduler_close_calls == scheduler_closes &&
		g_memory_close_calls == memory_closes,
		"rejected reopen and close do not release dependent resources");
}
#endif

#ifdef EXPECT_BASELINE
static void test_baseline_false_success(void)
{
	int ret;
	fixture_reset(true, false, true);
	g_permission_error = -EACCES;
	ret = npu_system_suspend(&g_device.system);
	REQUIRE(ret == 0,
		"baseline NPU14 reports success after provider permission error");
	REQUIRE(g_permission_calls == 1 && g_callback_calls == 0 &&
		g_notify_calls == 0 && g_cpu_off_calls == 1,
		"baseline continues to CPU_OFF after void provider early return");
	REQUIRE(!test_bit(NPU_SYS_RESUME_FW_LOAD,
		&g_device.system.resume_steps) &&
		!g_device.system.resume_steps && !g_device.system.resume_soc_steps,
		"baseline erases firmware and lower-stage ownership");
	printf("REPRO: provider permission-release errno is swallowed; CPU_OFF follows\n");

	fixture_reset(true, true, true);
	g_callback_error = -EIO;
	ret = npu_system_suspend(&g_device.system);
	REQUIRE(ret == 0 && g_permission_calls == 1 && g_callback_calls == 1 &&
		g_notify_calls == 1 && g_cpu_off_calls == 1 &&
		g_device.system.binary.imgloader.shutdown_fail,
		"baseline swallows callback failure and still requests CPU_OFF");
	printf("REPRO: provider callback failure is swallowed; CPU_OFF follows\n");

	fixture_reset(true, false, true);
	g_notify_error = -ECOMM;
	ret = npu_system_suspend(&g_device.system);
	REQUIRE(ret == 0 && g_permission_calls == 1 && g_notify_calls == 1 &&
		g_cpu_off_calls == 1,
		"baseline swallows notify failure and still requests CPU_OFF");
	printf("REPRO: provider notify failure is swallowed; CPU_OFF follows\n");
}
#else
static void test_provider_errors_quarantine(void)
{
	int ret;

	fixture_reset(true, false, true);
	g_permission_error = -EACCES;
	ret = npu_system_suspend(&g_device.system);
	REQUIRE(ret == -EACCES,
		"permission-release errno reaches NPU suspend caller unchanged");
	REQUIRE(g_notify_calls == 0 && g_callback_calls == 0 &&
		g_cpu_off_calls == 0,
		"permission-release failure stops provider and CPU_OFF sequence");
	require_quarantine_and_no_retry(-EACCES, 0, 0);
	printf("PASS: permission-release failure preserves ownership and is one-shot\n");

	fixture_reset(true, false, true);
	g_permission_error = 1;
	ret = npu_system_suspend(&g_device.system);
	REQUIRE(ret == -EIO && g_permission_calls == 1 &&
		g_notify_calls == 0 && g_cpu_off_calls == 0,
		"positive permission helper result maps to negative errno");
	require_quarantine_and_no_retry(-EIO, 0, 0);
	printf("PASS: unexpected positive permission status maps to -EIO and quarantines\n");

	fixture_reset(true, true, true);
	g_callback_error = -EIO;
	ret = npu_system_suspend(&g_device.system);
	REQUIRE(ret == -EIO && g_permission_calls == 1 &&
		g_callback_calls == 1 && g_notify_calls == 1 &&
		g_device.system.binary.imgloader.shutdown_fail,
		"callback errno reaches NPU while existing notify continuation is retained");
	require_quarantine_and_no_retry(-EIO, 1, 1);
	printf("PASS: callback failure after permission release is quarantined once\n");

	fixture_reset(true, true, true);
	g_callback_error = 1;
	ret = npu_system_suspend(&g_device.system);
	REQUIRE(ret == -EIO && g_permission_calls == 1 &&
		g_callback_calls == 1 && g_notify_calls == 1,
		"positive callback result maps to negative errno");
	require_quarantine_and_no_retry(-EIO, 1, 1);
	printf("PASS: unexpected positive callback status maps to -EIO and quarantines\n");

	fixture_reset(true, false, true);
	g_notify_error = -ECOMM;
	ret = npu_system_suspend(&g_device.system);
	REQUIRE(ret == -ECOMM && g_permission_calls == 1 &&
		g_callback_calls == 0 && g_notify_calls == 1,
		"notify errno reaches NPU after provider permission release");
	require_quarantine_and_no_retry(-ECOMM, 0, 1);
	printf("PASS: notify failure after permission release is quarantined once\n");

	fixture_reset(true, false, true);
	g_notify_error = 1;
	ret = npu_system_suspend(&g_device.system);
	REQUIRE(ret == -EIO && g_permission_calls == 1 &&
		g_callback_calls == 0 && g_notify_calls == 1,
		"positive notify result maps to negative errno");
	require_quarantine_and_no_retry(-EIO, 0, 1);
	printf("PASS: unexpected positive notify status maps to -EIO and quarantines\n");
}
#endif

static void test_clean_route_and_physical_order(bool s2mpu_support)
{
	int ret;
	char expected[16];
	size_t used = 0;

	fixture_reset(s2mpu_support, false, false);
	ret = npu_system_suspend(&g_device.system);
	REQUIRE(ret == 0, "clean provider status allows the existing SoC unwind");
	REQUIRE(!g_device.system.resume_steps &&
		!g_device.system.resume_soc_steps && !g_wake_source.active,
		"clean known-on suspend completes all modeled cleanup stages");
	REQUIRE(g_cpu_off_calls == 1,
		"provider success is followed by a distinct CPU_OFF request");
	if (TEST_IMGLOADER_ENABLED && s2mpu_support && TEST_S2MPU_CONFIG) {
		REQUIRE(g_permission_calls == 1,
			"S2MPU-enabled, supporting descriptor requests permission release");
		expected[used++] = 'P';
	} else {
		REQUIRE(g_permission_calls == 0,
			"no-S2MPU route does not call the permission-release helper");
	}
	if (TEST_IMGLOADER_ENABLED) {
		REQUIRE(g_notify_calls == 1,
			"enabled provider keeps its existing notify-helper call");
		expected[used++] = 'N';
	} else {
		REQUIRE(g_notify_calls == 0,
			"config-disabled image-loader stub has no provider side effects");
	}
#ifndef CONFIG_NPU_USE_BOOT_IOCTL
	expected[used++] = 'S';
#endif
	expected[used++] = 'D';
#ifndef CONFIG_NPU_USE_BOOT_IOCTL
	expected[used++] = 'K';
#endif
	expected[used++] = 'B';
	expected[used++] = 'W';
	expected[used] = '\0';
	{
		REQUIRE(g_events[0] == 'I',
			"interface close remains before firmware shutdown");
		REQUIRE(strstr(g_events, expected) != NULL,
			"provider result precedes existing STM/CPU/clock/buffer/wake order");
		if (s2mpu_support && TEST_S2MPU_CONFIG && TEST_IMGLOADER_ENABLED) {
#ifdef CONFIG_NPU_USE_BOOT_IOCTL
			REQUIRE(strstr(g_events, "IPND") == g_events,
#else
			REQUIRE(strstr(g_events, "IPNSD") == g_events,
#endif
				"permission release and notify precede the separate CPU_OFF request");
		}
	}
	printf("PASS: clean %s route preserves interface/provider/SoC teardown order\n",
		s2mpu_support ? "S2MPU-supported" : "non-S2MPU");
}

#ifdef CONFIG_NPU_SECURE_MODE
static void test_secure_warm_boot_skips_provider_shutdown(void)
{
	int ret;
	fixture_reset(true, false, true);
	g_mailbox.warm_boot_enable = 1;
	ret = npu_system_suspend(&g_device.system);
	REQUIRE(ret == 0 && g_permission_calls == 0 && g_callback_calls == 0 &&
		g_notify_calls == 0,
		"secure warm-boot path retains existing image-loader skip condition");
	REQUIRE(g_cpu_off_calls == 1 && !g_device.system.resume_steps &&
		!g_device.system.resume_soc_steps,
		"secure warm-boot skip still proceeds through existing teardown");
	printf("PASS: secure warm-boot skip preserves existing provider behavior\n");
}
#endif

static void test_cpu_uncertainty_refuses_before_provider(void)
{
	struct npu_system *system;
	fixture_reset(true, false, true);
	system = &g_device.system;
	system->resume_soc_steps =
		soc_stage(NPU_SYS_RESUME_SOC_CPU_ON_UNCERTAIN);
	REQUIRE(npu_system_suspend(system) == -EUCLEAN,
		"NPU14 CPU uncertainty guard still refuses cleanup");
	REQUIRE(g_interface_close_calls == 0 && g_permission_calls == 0 &&
		g_notify_calls == 0 && g_cpu_off_calls == 0,
		"CPU uncertainty guard precedes provider release and CPU_OFF");
	REQUIRE(test_bit(NPU_SYS_RESUME_FW_LOAD, &system->resume_steps) &&
		g_wake_source.active,
		"CPU uncertainty retains firmware ownership and wake dependency");
	printf("PASS: CPU_ON uncertainty still refuses provider and guessed inverse\n");
}

#ifndef EXPECT_BASELINE
static void test_reentrant_suspend_during_provider_call(void)
{
	int ret;
	fixture_reset(true, false, true);
	g_reenter_suspend = true;
	ret = npu_system_suspend(&g_device.system);
	REQUIRE(ret == 0, "outer status-owned provider shutdown completes normally");
	REQUIRE(g_reenter_suspend_ret == -EUCLEAN,
		"reentrant suspend sees the atomic provider ownership claim");
	REQUIRE(g_permission_calls == 1 && g_notify_calls == 1 &&
		g_cpu_off_calls == 1,
		"reentrant attempt cannot repeat provider release or later teardown");
	printf("PASS: reentrant teardown cannot repeat an outstanding provider call\n");
}
#endif

static void test_header_compatibility_and_legacy_wrapper(void)
{
	(void)g_event_marker();
#ifndef EXPECT_BASELINE
#if TEST_IMGLOADER_ENABLED
	fixture_reset(false, true, true);
	g_callback_error = -EIO;
	imgloader_shutdown(&g_device.system.binary.imgloader);
	REQUIRE(g_callback_calls == 1 && g_notify_calls == 1 &&
		g_device.system.binary.imgloader.shutdown_fail,
		"legacy void API retains callback, failure flag, and notify behavior");
#endif
#endif
	printf("PASS: legacy exported void shutdown signature remains callable\n");
}

int main(void)
{
	test_header_compatibility_and_legacy_wrapper();
#ifdef EXPECT_BASELINE
#if TEST_IMGLOADER_ENABLED && TEST_S2MPU_CONFIG
	test_baseline_false_success();
#endif
#else
#if TEST_IMGLOADER_ENABLED && TEST_S2MPU_CONFIG
	test_provider_errors_quarantine();
	test_reentrant_suspend_during_provider_call();
#endif
#endif
	test_clean_route_and_physical_order(true);
	test_clean_route_and_physical_order(false);
	test_cpu_uncertainty_refuses_before_provider();
#ifdef CONFIG_NPU_SECURE_MODE
	test_secure_warm_boot_skips_provider_shutdown();
#endif
	return 0;
}
