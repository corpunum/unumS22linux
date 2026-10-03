/* Actual NPU resume/suspend/caller bodies are injected by the Python runner. */
#include <errno.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef uint32_t u32;

#ifndef ELIBACC
#define ELIBACC 79
#endif

#define CONFIG_NPU_HARDWARE 1
#define CONFIG_PM_SLEEP 1
#define CONFIG_EXYNOS_NPU_DRAM_FW_LOG_BUF 1
#define CONFIG_NPU_USE_HW_DEVICE 1
#define CONFIG_NPU_MAILBOX_VERSION 9
#ifdef TEST_BOOT_IOCTL
#define CONFIG_NPU_USE_BOOT_IOCTL 1
#endif

#define NPU_DEVICE_ERR_STATE_EMERGENCY 3
#define NPU_DEVICE_ERR_STATE_SHUTDOWN_UNCERTAIN 4
#define NPU_HWDEV_TYPE_PWRCTRL 1U
#define NPU_HWDEV_TYPE_CLKCTRL 2U
#define NPU_HWDEV_STATUS_PWR_CLK_OFF 0U
#define NPU_HWDEV_STATUS_PWR_CLK_ON 1U
#define NPU_HWDEV_STATUS_ACTIVE 2U
#define NPU_HWDEV_STATUS_ERROR 4U
#define likely(value) (value)
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
#define npu_dbg(...) ((void)0)
#define BIT_CHECK_AND_EXECUTE(BIT, VAR, DESC, CODE) do { \
	(void)(DESC); \
	if (test_bit((BIT), (VAR))) { CODE } \
	clear_bit((BIT), (VAR)); \
} while (0)

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

/* ACTUAL_RESUME_ENUMS */

struct device {
	void *driver_data;
};

struct platform_device {
	struct device dev;
};

struct mailbox_hdr {
	uint32_t signature1;
	uint32_t signature2;
	uint32_t hw_info;
	uint32_t warm_boot_enable;
};

struct npu_memory_buffer {
	uint64_t paddr;
	void *vaddr;
	uint64_t daddr;
	unsigned long size;
};

struct npu_memory {
	unsigned int token;
};

struct npu_memory_v_buf {
	char name[32];
	size_t size;
	char *v_buf;
	unsigned int linked;
};

struct npu_clocks {
	unsigned int token;
};

struct wakeup_source {
	int active;
};

struct npu_system {
	struct platform_device *pdev;
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
	unsigned int mode;
};

struct npu_hw_device {
	struct device *dev;
	struct npu_clocks clks;
	char *name;
	unsigned int id;
	unsigned int status;
	unsigned int type;
};

static int npu_system_resume(struct npu_system *system, unsigned int mode);
static int npu_system_suspend(struct npu_system *system);
static int npu_system_soc_suspend(struct npu_system *system);
static int npu_device_runtime_resume(struct device *dev);

static struct npu_device g_device;
static struct npu_hw_device g_hwdev;
static struct device g_hwdev_device;
static struct platform_device g_platform;
static struct wakeup_source g_wake_source;
static struct mailbox_hdr g_mailbox;
static struct npu_memory_buffer g_fwmbox;
static struct npu_memory_v_buf fw_report_buf = { .size = 4096 };
static struct npu_memory_v_buf fw_profile_buf = { .size = 4096 };
static char g_valloc_storage[4][8];

static unsigned int g_valloc_calls;
static unsigned int g_valloc_fail_at;
static unsigned int g_valloc_live;
static struct npu_memory *g_valloc_owner;
static unsigned int g_report_init_calls;
static unsigned int g_profile_init_calls;
static unsigned int g_fw_test_init_calls;
static unsigned int g_log_buffer_free_calls;
static int g_log_buffer_free_result;
static int g_firmware_result;
static unsigned int g_firmware_calls;
static int g_cpu_on_result;
static int g_cpu_on_partial;
static int g_cpu_live;
static unsigned int g_cpu_on_calls;
static int g_cpu_off_result;
static unsigned int g_cpu_off_calls;
static int g_stm_on_result;
static int g_stm_on_partial;
static int g_stm_live;
static unsigned int g_stm_on_calls;
static int g_stm_off_result;
static unsigned int g_stm_off_calls;
static int g_interface_open_result;
static int g_interface_open_partial;
static int g_interface_live;
static unsigned int g_interface_open_calls;
static int g_interface_close_result;
static unsigned int g_interface_close_calls;
static unsigned int g_imgloader_shutdown_calls;
static unsigned int g_clock_prepare_calls;
static unsigned int g_clock_disable_calls;
static int g_clock_prepare_result;
static int g_clock_live;
static unsigned int g_wake_lock_calls;
static unsigned int g_wake_unlock_calls;
static unsigned int g_memory_close_calls;
static unsigned int g_open_resource_calls;
static unsigned int g_close_resource_calls;
static unsigned int g_core_clock_off_calls;
static unsigned int g_core_clock_on_calls;
static unsigned int g_pm_usage;
static unsigned int g_pm_put_calls;
static int g_hwdev_pm_result;

static int npu_wake_lock_active(struct wakeup_source *ws)
{
	return ws->active;
}

static void npu_wake_lock(struct wakeup_source *ws)
{
	ws->active = 1;
	++g_wake_lock_calls;
}

static void npu_wake_unlock(struct wakeup_source *ws)
{
	ws->active = 0;
	++g_wake_unlock_calls;
}

static struct npu_memory_buffer *npu_get_mem_area(struct npu_system *system,
		const char *name)
{
	(void)system;
	(void)name;
	return &g_fwmbox;
}

static unsigned int npu_get_hw_info(void)
{
	return 0x1234;
}

static void print_ufw_signature(struct npu_memory_buffer *buffer)
{
	(void)buffer;
}

static void print_all_iomem_area(const struct npu_system *system)
{
	(void)system;
}

static void npu_clk_init(struct npu_system *system)
{
	(void)system;
}

static int npu_memory_v_alloc(struct npu_memory *memory,
		struct npu_memory_v_buf *buffer)
{
	++g_valloc_calls;
	if (g_valloc_fail_at == g_valloc_calls) {
		buffer->v_buf = NULL;
		return -ENOMEM;
	}
	REQUIRE(g_valloc_calls <= 4, "fixture virtual-allocation bound");
	buffer->v_buf = g_valloc_storage[g_valloc_calls - 1];
	buffer->linked = 1;
	g_valloc_owner = memory;
	++g_valloc_live;
	return 0;
}

static void npu_fw_report_init(char *buffer, const size_t size)
{
	(void)buffer;
	(void)size;
	++g_report_init_calls;
}

static void npu_fw_profile_init(char *buffer, const size_t size)
{
	(void)buffer;
	(void)size;
	++g_profile_init_calls;
}

static int npu_fw_test_initialize(struct npu_system *system)
{
	(void)system;
	++g_fw_test_init_calls;
	return 0;
}

/* ACTUAL_FW_LOG_ALLOCATOR */

static int npu_system_free_fw_dram_log_buf(void)
{
	++g_log_buffer_free_calls;
	return g_log_buffer_free_result;
}

static int npu_firmware_load(struct npu_system *system, int mode)
{
	(void)system;
	(void)mode;
	++g_firmware_calls;
	return g_firmware_result;
}

static int npu_cpu_on(struct npu_system *system)
{
	(void)system;
	++g_cpu_on_calls;
	if (!g_cpu_on_result || g_cpu_on_partial)
		g_cpu_live = 1;
	return g_cpu_on_result;
}

static int npu_cpu_off(struct npu_system *system)
{
	(void)system;
	++g_cpu_off_calls;
	if (!g_cpu_off_result)
		g_cpu_live = 0;
	return g_cpu_off_result;
}

static int npu_stm_enable(struct npu_system *system, int hid)
{
	(void)system;
	(void)hid;
	++g_stm_on_calls;
	if (!g_stm_on_result || g_stm_on_partial)
		g_stm_live = 1;
	return g_stm_on_result;
}

static int npu_stm_disable(struct npu_system *system, int hid)
{
	(void)system;
	(void)hid;
	++g_stm_off_calls;
	if (!g_stm_off_result)
		g_stm_live = 0;
	return g_stm_off_result;
}

static int npu_interface_open(struct npu_system *system)
{
	(void)system;
	++g_interface_open_calls;
	if (!g_interface_open_result)
		g_interface_live = 1;
	else if (g_interface_open_partial)
		g_interface_live = 0; /* NPU13's helper-level partial-open unwind. */
	return g_interface_open_result;
}

static int npu_interface_close(struct npu_system *system)
{
	(void)system;
	++g_interface_close_calls;
	if (!g_interface_close_result)
		g_interface_live = 0;
	return g_interface_close_result;
}

static void npu_imgloader_shutdown(struct npu_system *system)
{
	(void)system;
	++g_imgloader_shutdown_calls;
}

static int npu_clk_prepare_enable(struct npu_clocks *clks)
{
	(void)clks;
	++g_clock_prepare_calls;
	if (!g_clock_prepare_result)
		g_clock_live = 1;
	return g_clock_prepare_result;
}

static void npu_clk_disable_unprepare(struct npu_clocks *clks)
{
	(void)clks;
	++g_clock_disable_calls;
	g_clock_live = 0;
}

static void npu_log_hwdev_set_data(int id)
{
	(void)id;
}

static int npu_qos_close(struct npu_system *system)
{
	(void)system;
	++g_close_resource_calls;
	return 0;
}

static int npu_scheduler_close(struct npu_device *device)
{
	(void)device;
	++g_close_resource_calls;
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

static int npu_memory_open(struct npu_memory *memory)
{
	(void)memory;
	++g_open_resource_calls;
	return 0;
}

static int npu_util_memdump_open(struct npu_system *system)
{
	(void)system;
	++g_open_resource_calls;
	return 0;
}

static int npu_scheduler_open(struct npu_device *device)
{
	(void)device;
	++g_open_resource_calls;
	return 0;
}

static void npu_scheduler_boost_on(void *sched)
{
	(void)sched;
}

static int npu_qos_open(struct npu_system *system)
{
	(void)system;
	++g_open_resource_calls;
	return 0;
}

static int dsp_dhcp_init(struct npu_device *device)
{
	(void)device;
	return 0;
}

static int proto_drv_open(struct npu_device *device)
{
	(void)device;
	return 0;
}

static int proto_drv_close(struct npu_device *device)
{
	(void)device;
	return 0;
}

static int __npu_device_late_open(struct npu_device *device)
{
	(void)device;
	return 0;
}

static struct npu_device *dev_get_drvdata(struct device *dev)
{
	return (struct npu_device *)dev->driver_data;
}

static int npu_core_clock_off(struct npu_system *system)
{
	(void)system;
	++g_core_clock_off_calls;
	return 0;
}

static int npu_core_clock_on(struct npu_system *system)
{
	(void)system;
	++g_core_clock_on_calls;
	return 0;
}

static int pm_runtime_get_sync(struct device *dev)
{
	int ret;
	++g_pm_usage;
	ret = npu_device_runtime_resume(dev);
	return ret < 0 ? ret : 1;
}

static int pm_runtime_resume_and_get(struct device *dev)
{
	int ret;
	++g_pm_usage;
	ret = dev == &g_hwdev_device ? g_hwdev_pm_result :
		npu_device_runtime_resume(dev);
	if (ret < 0) {
		--g_pm_usage;
		return ret;
	}
	return 0;
}

static int pm_runtime_put_sync(struct device *dev)
{
	(void)dev;
	++g_pm_put_calls;
	if (!g_pm_usage)
		return -EOVERFLOW;
	--g_pm_usage;
	return 0;
}

/* ACTUAL_SYSTEM_OPEN_CLOSE_SOC_RESUME_SUSPEND */

/* ACTUAL_DEVICE_POWER_ON_BOOTUP_RUNTIME_RESUME */

/* ACTUAL_HWDEV_DEFAULT_BOOT */

static void fixture_reset(int cold_boot)
{
	memset(&g_device, 0, sizeof(g_device));
	memset(&g_hwdev, 0, sizeof(g_hwdev));
	memset(&g_hwdev_device, 0, sizeof(g_hwdev_device));
	memset(&g_platform, 0, sizeof(g_platform));
	memset(&g_wake_source, 0, sizeof(g_wake_source));
	memset(&g_mailbox, 0, sizeof(g_mailbox));
	memset(&g_fwmbox, 0, sizeof(g_fwmbox));
	memset(&fw_report_buf, 0, sizeof(fw_report_buf));
	memset(&fw_profile_buf, 0, sizeof(fw_profile_buf));
	fw_report_buf.size = 4096;
	fw_profile_buf.size = 4096;
	memset(g_valloc_storage, 0, sizeof(g_valloc_storage));
	g_valloc_calls = 0;
	g_valloc_fail_at = 0;
	g_valloc_live = 0;
	g_valloc_owner = NULL;
	g_report_init_calls = 0;
	g_profile_init_calls = 0;
	g_fw_test_init_calls = 0;
	g_log_buffer_free_calls = 0;
	g_log_buffer_free_result = 0;
	g_firmware_result = 0;
	g_firmware_calls = 0;
	g_cpu_on_result = 0;
	g_cpu_on_partial = 0;
	g_cpu_live = 0;
	g_cpu_on_calls = 0;
	g_cpu_off_result = 0;
	g_cpu_off_calls = 0;
	g_stm_on_result = 0;
	g_stm_on_partial = 0;
	g_stm_live = 0;
	g_stm_on_calls = 0;
	g_stm_off_result = 0;
	g_stm_off_calls = 0;
	g_interface_open_result = 0;
	g_interface_open_partial = 0;
	g_interface_live = 0;
	g_interface_open_calls = 0;
	g_interface_close_result = 0;
	g_interface_close_calls = 0;
	g_imgloader_shutdown_calls = 0;
	g_clock_prepare_calls = 0;
	g_clock_disable_calls = 0;
	g_clock_prepare_result = 0;
	g_clock_live = 0;
	g_wake_lock_calls = 0;
	g_wake_unlock_calls = 0;
	g_memory_close_calls = 0;
	g_open_resource_calls = 0;
	g_close_resource_calls = 0;
	g_core_clock_off_calls = 0;
	g_core_clock_on_calls = 0;
	g_pm_usage = 0;
	g_pm_put_calls = 0;
	g_hwdev_pm_result = 0;
	g_fwmbox.vaddr = &g_mailbox;
	g_fwmbox.size = sizeof(g_mailbox);
	g_device.dev = &g_platform.dev;
	g_platform.dev.driver_data = &g_device;
	g_device.system.pdev = &g_platform;
	g_device.system.ws = &g_wake_source;
	g_device.system.fw_cold_boot = cold_boot;
	g_device.mode = 0;
	g_hwdev.dev = &g_hwdev_device;
	g_hwdev.name = "fixture";
	g_hwdev.id = 1;
	g_hwdev.type = NPU_HWDEV_TYPE_PWRCTRL | NPU_HWDEV_TYPE_CLKCTRL;
}

static void test_resume_failure_through_real_runtime_caller(void)
{
	int ret;
	fixture_reset(1);
	g_firmware_result = -EIO;
	ret = npu_device_runtime_resume(&g_platform.dev);
#ifdef EXPECT_BASELINE
	REQUIRE(ret == 0, "baseline runtime caller must reproduce swallowed firmware error");
	REQUIRE(g_core_clock_off_calls == 1,
		"baseline caller continues as if resume succeeded");
	REQUIRE(g_device.system.resume_steps != 0 && g_wake_source.active,
		"baseline leaves failed-resume stage ownership active");
	printf("REPRO: firmware error is reported as runtime-resume success\n");
#else
	REQUIRE(ret == -EIO, "runtime caller propagates original firmware failure");
	REQUIRE(g_core_clock_off_calls == 0,
		"runtime caller must not run success-only clock transition on failure");
	REQUIRE(g_device.system.resume_steps == 0 && !g_wake_source.active,
		"successful rollback releases confirmed acquired state");
	REQUIRE(g_device.err_state & (1UL << NPU_DEVICE_ERR_STATE_EMERGENCY),
		"resume error remains emergency-classified");
	printf("PASS: runtime caller gets original firmware error after owned rollback\n");
#endif
}

static void test_interface_failure_and_direct_bootup_close(void)
{
	int ret;
	fixture_reset(1);
	g_interface_open_result = -EIO;
	g_interface_open_partial = 1;
	ret = npu_device_bootup(&g_device);
#ifdef EXPECT_BASELINE
	REQUIRE(ret == -ELIBACC,
		"baseline bootup only sees generic emergency after swallowed interface error");
	REQUIRE(g_cpu_live && g_memory_close_calls == 1,
		"baseline direct close destroys memory while successfully-started CPU remains on");
	REQUIRE(g_device.system.resume_steps != 0,
		"baseline direct close leaves stale resume bits");
	printf("REPRO: bootup error path directly closes with the NPU CPU still on\n");
#else
	REQUIRE(ret == -EIO, "bootup preserves original interface-open error");
	REQUIRE(!g_cpu_live && g_memory_close_calls == 1,
		"known-acquired SoC state is stopped before memory close");
	REQUIRE(g_device.system.resume_steps == 0 &&
		g_device.system.resume_soc_steps == 0,
		"successful direct-caller rollback leaves no stale ownership");
	REQUIRE(!g_interface_live, "interface helper failure path is internally unwound");
	printf("PASS: bootup error caller closes only after completed resume rollback\n");
#endif
}

static void test_inverse_failure_quarantines_and_retry_is_blocked(void)
{
	int ret;
	fixture_reset(1);
	g_interface_open_result = -EIO;
	g_interface_open_partial = 1;
	g_cpu_off_result = -EREMOTEIO;
	ret = npu_device_bootup(&g_device);
#ifdef EXPECT_BASELINE
	REQUIRE(ret == -ELIBACC,
		"baseline reports emergency, not original resume failure");
	REQUIRE(g_cpu_live && g_memory_close_calls == 1,
		"baseline closes memory after failed CPU inverse");
	printf("REPRO: failed CPU inverse is masked and its ownership is erased\n");
#else
	REQUIRE(ret == -EIO, "resume errno wins over CPU-off cleanup errno");
	REQUIRE(g_cpu_live && g_cpu_off_calls == 1,
		"failed inverse keeps the physical CPU state and is not retried blindly");
	REQUIRE(g_device.system.resume_soc_steps != 0 &&
		g_device.system.resume_steps != 0,
		"failed inverse retains cleanup ownership bits");
	REQUIRE(g_memory_close_calls == 0,
		"direct bootup close is quarantined before memory teardown");
	{
		unsigned int prior_cpu_off = g_cpu_off_calls;
		unsigned long prior_soc_steps = g_device.system.resume_soc_steps;
		REQUIRE(npu_system_resume(&g_device.system, 0) == -EBUSY,
			"retry while residual ownership exists is rejected");
		REQUIRE(npu_system_open(&g_device.system) == -EBUSY,
			"late open cannot clear/quash residual ownership");
		REQUIRE(npu_system_close(&g_device.system) == -EBUSY,
			"late close cannot free memory still owned by uncertain hardware");
		REQUIRE(g_cpu_off_calls == prior_cpu_off &&
			g_device.system.resume_soc_steps == prior_soc_steps,
			"blocked retry/close does not repeat inverse or erase state");
	}
	printf("PASS: failed inverse retains ownership and blocks retry/open/close\n");
#endif
}

static void test_interface_close_failure_preserves_lower_resources(void)
{
	int ret;
	fixture_reset(1);
	REQUIRE(npu_system_resume(&g_device.system, 0) == 0,
		"setup: actual resume body succeeds");
	g_interface_close_result = -EIO;
	ret = npu_system_suspend(&g_device.system);
#ifdef EXPECT_BASELINE
	REQUIRE(ret == 0, "baseline suspend masks interface close failure");
	REQUIRE(g_device.system.resume_steps == 0,
		"baseline clears interface and lower-stage ownership after failure");
	REQUIRE(g_cpu_off_calls == 1,
		"baseline proceeds to SoC teardown after interface close failure");
#ifndef CONFIG_NPU_USE_BOOT_IOCTL
	REQUIRE(g_clock_disable_calls == 1,
		"baseline proceeds to clock teardown after interface close failure");
#else
	REQUIRE(g_clock_disable_calls == 0,
		"boot-ioctl build has no system clock-disable operation");
#endif
	REQUIRE(npu_system_close(&g_device.system) == 0 && g_memory_close_calls == 1,
		"baseline close is not blocked after failed interface inverse");
	printf("REPRO: suspend returns success after interface-close failure\n");
#else
	REQUIRE(ret == -EIO, "interface close failure reaches suspend caller");
	REQUIRE(test_bit(NPU_SYS_RESUME_OPEN_INTERFACE,
		&g_device.system.resume_steps),
		"failed interface inverse retains its ownership bit");
	REQUIRE(g_cpu_off_calls == 0 && g_clock_disable_calls == 0,
		"dependent lower resources remain live after interface-close error");
	{
		unsigned int close_calls = g_interface_close_calls;
		REQUIRE(npu_system_suspend(&g_device.system) == -EUCLEAN,
			"second suspend quarantines an ambiguous interface close");
		REQUIRE(g_interface_close_calls == close_calls && g_cpu_off_calls == 0,
			"ambiguous interface close is never retried or followed by lower teardown");
	}
	REQUIRE(npu_system_close(&g_device.system) == -EBUSY &&
		g_memory_close_calls == 0,
		"close refuses memory teardown while interface ownership remains");
	printf("PASS: interface inverse failure preserves lower-layer ownership\n");
#endif
}

static void test_cpu_inverse_failure_retains_ownership(void)
{
	int ret;
	fixture_reset(1);
	REQUIRE(npu_system_resume(&g_device.system, 0) == 0,
		"setup: actual resume body succeeds");
	g_cpu_off_result = -EIO;
	ret = npu_system_suspend(&g_device.system);
#ifdef EXPECT_BASELINE
	REQUIRE(ret == 0 && !g_device.system.resume_steps &&
		!g_device.system.resume_soc_steps,
		"baseline masks CPU inverse failure and clears step ownership");
	REQUIRE(g_cpu_live,
		"baseline leaves CPU on after CPU-off failure");
#ifndef CONFIG_NPU_USE_BOOT_IOCTL
	REQUIRE(g_clock_disable_calls == 1,
		"baseline disables clocks while CPU-off failure leaves CPU on");
#else
	REQUIRE(g_clock_disable_calls == 0,
		"boot-ioctl build has no system clock-disable operation");
#endif
	printf("REPRO: failed CPU-off is masked and later clock teardown proceeds\n");
#else
	REQUIRE(ret == -EIO, "CPU-off error propagates to suspend caller");
	REQUIRE(test_bit(NPU_SYS_RESUME_SOC_CPU_ON,
		&g_device.system.resume_soc_steps) &&
		test_bit(NPU_SYS_RESUME_SOC, &g_device.system.resume_steps),
		"failed CPU-off keeps both SoC ownership and parent stage");
	REQUIRE(g_cpu_live && g_clock_disable_calls == 0 && g_wake_source.active,
		"lower resources remain live and wake lock is retained");
	{
		unsigned int cpu_off_calls = g_cpu_off_calls;
		REQUIRE(npu_system_suspend(&g_device.system) == -EUCLEAN,
			"second suspend quarantines an ambiguous CPU-off");
		REQUIRE(g_cpu_off_calls == cpu_off_calls && g_clock_disable_calls == 0,
			"ambiguous CPU-off is not retried and clocks remain owned");
	}
	REQUIRE(npu_system_resume(&g_device.system, 0) == -EBUSY,
		"resume cannot erase failed-off ownership");
	printf("PASS: CPU inverse failure retains its state and lower resources\n");
#endif
}

static void test_firmware_allocation_error_and_unknown_global_owner(void)
{
	int ret;
	fixture_reset(1);
	g_valloc_fail_at = 1;
	ret = npu_system_resume(&g_device.system, 0);
#ifdef EXPECT_BASELINE
	REQUIRE(ret == 0 && g_device.system.resume_steps != 0 &&
		!fw_report_buf.v_buf && !fw_profile_buf.v_buf,
		"baseline swallows failure of first actual virtual-buffer allocation");
	printf("REPRO: first firmware log allocation failure returns success\n");
#else
	REQUIRE(ret == -ENOMEM && g_device.system.resume_steps == 0 &&
		!g_wake_source.active && !fw_report_buf.v_buf && !fw_profile_buf.v_buf,
		"first actual virtual-buffer allocation failure unwinds acquired wake lock only");
	printf("PASS: first actual log allocation failure returns -ENOMEM without buffer ownership\n");
#endif

	fixture_reset(1);
	g_valloc_fail_at = 2;
	ret = npu_system_resume(&g_device.system, 0);
#ifdef EXPECT_BASELINE
	REQUIRE(ret == 0 && g_device.system.resume_steps != 0,
		"baseline swallows firmware-buffer allocation failure");
	REQUIRE(fw_report_buf.v_buf && !fw_profile_buf.v_buf && g_valloc_live == 1,
		"baseline partial allocator state is retained in static log ownership");
	printf("REPRO: firmware-buffer allocation failure returns success\n");
#else
	REQUIRE(ret == -ENOMEM, "allocation error is returned unchanged");
	REQUIRE(test_bit(NPU_SYS_RESUME_FWBUF_ALLOC_UNCERTAIN,
		&g_device.system.resume_steps) &&
		!test_bit(NPU_SYS_RESUME_INIT_FWBUF, &g_device.system.resume_steps) &&
		!g_wake_source.active,
		"partial static allocation remains explicitly quarantined after wake unlock");
	REQUIRE(fw_report_buf.v_buf && !fw_profile_buf.v_buf && g_valloc_live == 1 &&
		g_valloc_owner == &g_device.system.memory && g_log_buffer_free_calls == 0,
		"partial static global buffer stays with its system owner, not over-freed");
	REQUIRE(npu_system_resume(&g_device.system, 0) == -EBUSY &&
		npu_system_open(&g_device.system) == -EBUSY &&
		npu_system_close(&g_device.system) == -EBUSY &&
		npu_system_suspend(&g_device.system) == -EUCLEAN &&
		g_open_resource_calls == 0 && g_memory_close_calls == 0 &&
		g_log_buffer_free_calls == 0,
		"partial static allocation blocks retry/free/open/close without releasing its owner");
	printf("PASS: actual partial global log allocation is quarantined after failed resume\n");
#endif
}

static void test_partial_allocator_error_blocks_bootup_close(void)
{
	int ret;
	fixture_reset(1);
	g_valloc_fail_at = 2;
	ret = npu_device_bootup(&g_device);
#ifdef EXPECT_BASELINE
	REQUIRE(ret == -ELIBACC && g_valloc_live == 1 && g_memory_close_calls == 1,
		"baseline bootup closes memory while the partial static buffer remains allocated");
	printf("REPRO: bootup closes partial firmware-buffer owner after swallowed allocation error\n");
#else
	REQUIRE(ret == -ENOMEM && g_valloc_live == 1 && g_memory_close_calls == 0,
		"bootup returns allocator errno but cannot close the partial-buffer owner");
	REQUIRE(test_bit(NPU_SYS_RESUME_FWBUF_ALLOC_UNCERTAIN,
		&g_device.system.resume_steps) && !g_wake_source.active &&
		g_log_buffer_free_calls == 0,
		"partial allocator owner remains quarantined with only wake lock released");
	printf("PASS: bootup caller cannot close memory with partial global buffers\n");
#endif
}

static void test_success_roundtrip_and_free_error(void)
{
	int ret;
	fixture_reset(1);
	ret = npu_system_resume(&g_device.system, 0);
#ifdef EXPECT_BASELINE
	REQUIRE(ret == 0, "baseline success setup");
#else
	REQUIRE(ret == 0, "patched success setup");
#endif
	ret = npu_system_suspend(&g_device.system);
	REQUIRE(ret == 0, "successful suspend must unwind all tracked resources");
	REQUIRE(g_device.system.resume_steps == 0 &&
		g_device.system.resume_soc_steps == 0 && !g_wake_source.active,
		"successful roundtrip clears only completed ownership steps");
	REQUIRE(npu_system_resume(&g_device.system, 0) == 0,
		"explicit new resume works after fully-clean suspend");

#ifndef EXPECT_BASELINE
	fixture_reset(1);
	REQUIRE(npu_system_resume(&g_device.system, 0) == 0,
		"setup for free error");
	g_log_buffer_free_result = -ENOMEM;
	ret = npu_system_suspend(&g_device.system);
	REQUIRE(ret == -ENOMEM, "buffer inverse error is propagated");
	REQUIRE(test_bit(NPU_SYS_RESUME_INIT_FWBUF,
		&g_device.system.resume_steps),
		"failed log-buffer inverse retains its parent ownership bit");
	REQUIRE(!g_wake_source.active && g_wake_unlock_calls == 1,
		"independent wake-lock cleanup still completes after buffer error");
	REQUIRE(npu_system_close(&g_device.system) == -EBUSY &&
		g_memory_close_calls == 0,
		"close is blocked while log-buffer ownership remains unknown");
	{
		unsigned int free_calls = g_log_buffer_free_calls;
		REQUIRE(npu_system_suspend(&g_device.system) == -EUCLEAN,
			"second suspend quarantines an ambiguous buffer free");
		REQUIRE(g_log_buffer_free_calls == free_calls,
			"ambiguous buffer free is not attempted twice");
	}
	printf("PASS: buffer inverse error retains ownership while independent wake lock releases\n");
#endif
	printf("PASS: resume/suspend success roundtrip supports a clean explicit retry\n");
}

static void test_ambiguous_cpu_acquisition_quarantines(void)
{
	int ret;
	fixture_reset(1);
	g_cpu_on_result = -ETIMEDOUT;
	g_cpu_on_partial = 1;
	ret = npu_system_resume(&g_device.system, 0);
#ifdef EXPECT_BASELINE
	REQUIRE(ret == 0 && g_cpu_live && !g_device.system.resume_soc_steps,
		"baseline hides ambiguous CPU-on failure without ownership state");
	printf("REPRO: failed CPU-on can leave partial CPU state without a resume owner bit\n");
#else
	REQUIRE(ret == -ETIMEDOUT, "original uncertain CPU-on errno survives unwind");
	REQUIRE(g_cpu_live && g_cpu_off_calls == 0,
		"unknown command outcome is quarantined, not blindly inverted");
	REQUIRE(g_device.system.resume_soc_steps != 0 &&
		g_device.system.resume_steps != 0,
		"uncertain acquisition keeps ownership markers and upper stages");
	REQUIRE(npu_system_close(&g_device.system) == -EBUSY &&
		g_memory_close_calls == 0,
		"unknown CPU outcome blocks teardown of firmware memory");
	printf("PASS: uncertain CPU acquisition is quarantined without guessed inverse\n");
#endif
}

static void test_actual_hwdev_callback_pm_transaction(void)
{
	int ret;
	fixture_reset(0);
	g_hwdev_pm_result = -EIO;
	ret = npu_hwdev_default_boot(&g_hwdev, true);
	REQUIRE(ret == -EIO && g_pm_usage == 0 &&
		g_hwdev.status == NPU_HWDEV_STATUS_ERROR,
		"actual default boot callback balances failed PM resume and publishes ERROR");
	REQUIRE(g_clock_prepare_calls == 0,
		"failed power acquisition does not attempt clock enable");

	fixture_reset(0);
	g_clock_prepare_result = -EREMOTEIO;
	ret = npu_hwdev_default_boot(&g_hwdev, true);
	REQUIRE(ret == -EREMOTEIO && g_pm_usage == 0 && g_pm_put_calls == 1 &&
		g_hwdev.status == NPU_HWDEV_STATUS_ERROR && !g_clock_live,
		"actual callback releases PM ref after clock error without claiming ON/OFF");

	fixture_reset(0);
	REQUIRE(npu_hwdev_default_boot(&g_hwdev, true) == 0 &&
		g_pm_usage == 1 && g_hwdev.status ==
		(NPU_HWDEV_STATUS_ACTIVE << 16 | NPU_HWDEV_STATUS_PWR_CLK_ON),
		"actual callback publishes ON only after power and clocks succeed");
	REQUIRE(npu_hwdev_default_boot(&g_hwdev, false) == 0 &&
		g_pm_usage == 0 && g_hwdev.status == NPU_HWDEV_STATUS_PWR_CLK_OFF,
		"actual callback balances the matching normal power-off reference");
	printf("PASS: actual NPU hwdev callback balances PM on failure and success\n");
}

#ifndef CONFIG_NPU_USE_BOOT_IOCTL
static void test_clock_prepare_failure_cleans_only_prior_stages(void)
{
	int ret;
	fixture_reset(1);
	g_clock_prepare_result = -EIO;
	ret = npu_system_resume(&g_device.system, 0);
#ifdef EXPECT_BASELINE
	REQUIRE(ret == 0 && g_clock_prepare_calls == 1 && !g_clock_live,
		"baseline hides clock-prepare failure after helper rollback");
	REQUIRE(g_device.system.resume_steps != 0,
		"baseline leaves earlier firmware/wake stages owned");
	printf("REPRO: clock-prepare failure is reported as resume success\n");
#else
	REQUIRE(ret == -EIO && g_clock_prepare_calls == 1 && !g_clock_live,
		"clock helper failure is propagated without claiming clock ownership");
	REQUIRE(g_clock_disable_calls == 0 && g_device.system.resume_steps == 0 &&
		!g_wake_source.active,
		"rollback releases completed prior stages but does not disable unacquired clocks");
	printf("PASS: clock-prepare failure propagates and rolls back earlier owned stages\n");
#endif
}

static void test_stm_enable_failure_quarantines_unknown_outcome(void)
{
	int ret;
	fixture_reset(1);
	g_stm_on_result = -EIO;
	g_stm_on_partial = 1;
	ret = npu_system_resume(&g_device.system, 0);
#ifdef EXPECT_BASELINE
	REQUIRE(ret == 0 && g_cpu_live && g_stm_live &&
		test_bit(NPU_SYS_RESUME_SOC_CPU_ON,
			&g_device.system.resume_soc_steps),
		"baseline masks partial STM enable after CPU acquired");
	REQUIRE(g_stm_off_calls == 0 && g_cpu_off_calls == 0,
		"baseline did not attempt cleanup when resume error was swallowed");
	printf("REPRO: failed STM-enable can leave live STM and CPU under false success\n");
#else
	REQUIRE(ret == -EIO && g_cpu_live && g_stm_live,
		"original STM-enable error survives conservative unwind");
	REQUIRE(g_stm_off_calls == 0 && g_cpu_off_calls == 0,
		"unknown STM refcount outcome is not inverted or decremented blindly");
	REQUIRE(g_device.system.resume_soc_steps != 0 &&
		g_device.system.resume_steps != 0 && g_clock_live,
		"uncertain STM acquisition quarantines SoC and dependent clocks");
	REQUIRE(npu_system_close(&g_device.system) == -EBUSY &&
		g_memory_close_calls == 0,
		"uncertain STM blocks close and firmware-memory teardown");
	printf("PASS: uncertain STM acquisition is quarantined without guessed inverse\n");
#endif
}

static void test_stm_disable_failure_preserves_ref_ownership(void)
{
	int ret;
	fixture_reset(1);
	REQUIRE(npu_system_resume(&g_device.system, 0) == 0,
		"setup: actual resume body succeeds");
	g_stm_off_result = -EREMOTEIO;
	ret = npu_system_suspend(&g_device.system);
#ifdef EXPECT_BASELINE
	REQUIRE(ret == 0 && !g_device.system.resume_steps &&
		!g_device.system.resume_soc_steps,
		"baseline masks STM-disable error and erases ref ownership");
	REQUIRE(g_stm_live && g_stm_off_calls == 1 && g_cpu_off_calls == 1,
		"baseline decrements STM ref, powers CPU off, and continues lower teardown");
	printf("REPRO: STM-disable error is masked and cleanup continues\n");
#else
	REQUIRE(ret == -EREMOTEIO, "STM-disable error propagates unchanged");
	REQUIRE(g_stm_live && g_stm_off_calls == 1 && g_cpu_off_calls == 0,
		"failed STM disable retains STM/CPU ownership and stops later inverses");
	REQUIRE(test_bit(NPU_SYS_RESUME_SOC_STM,
		&g_device.system.resume_soc_steps) && g_clock_live &&
		g_wake_source.active,
		"parent state and dependent resources remain owned after STM inverse failure");
	{
		unsigned int stm_off_calls = g_stm_off_calls;
		REQUIRE(npu_system_suspend(&g_device.system) == -EUCLEAN,
			"second suspend quarantines an ambiguous STM-disable");
		REQUIRE(g_stm_off_calls == stm_off_calls && g_cpu_off_calls == 0 &&
			g_clock_live,
			"ambiguous STM-disable is not retried and dependent state remains owned");
	}
	printf("PASS: STM inverse failure retains ownership and stops lower teardown\n");
#endif
}

static void test_transactional_runtime_pm_reference(void)
{
	int ret;
	fixture_reset(1);
	g_firmware_result = -EIO;
	ret = __npu_device_power_on(&g_device);
#ifdef EXPECT_BASELINE
	REQUIRE(ret == 1 && g_pm_usage == 1,
		"baseline PM get sees swallowed resume as positive success and retains usage ref");
	REQUIRE(g_core_clock_off_calls == 1,
		"baseline callback also performs success-only post-resume operation");
	printf("REPRO: runtime PM caller sees positive success while resume failed\n");
#else
	REQUIRE(ret == -EIO && g_pm_usage == 0,
		"resume_and_get exposes failure and balances usage reference");
	REQUIRE(g_core_clock_off_calls == 0,
		"failed runtime resume does not run success-only clock operation");
	printf("PASS: runtime PM failed-resume reference remains balanced\n");
#endif
	fixture_reset(0);
	ret = __npu_device_power_on(&g_device);
#ifdef EXPECT_BASELINE
	REQUIRE(ret == 1, "baseline get_sync exposes positive resume result");
#else
	REQUIRE(ret == 0, "resume_and_get normalizes successful resume to zero");
#endif
	REQUIRE(g_pm_usage == 1, "successful runtime resume acquires exactly one PM reference");
}

static void test_actual_runtime_suspend_caller_propagates_cleanup_error(void)
{
	int ret;
	fixture_reset(1);
	REQUIRE(npu_system_resume(&g_device.system, 0) == 0,
		"setup: actual resume body succeeds before runtime suspend callback");
	g_interface_close_result = -EREMOTEIO;
	ret = npu_device_runtime_suspend(&g_platform.dev);
#ifdef EXPECT_BASELINE
	REQUIRE(ret == 0 && g_cpu_off_calls == 1 &&
		g_device.system.resume_steps == 0,
		"baseline runtime-suspend caller sees masked interface close error");
	printf("REPRO: runtime-suspend callback sees false success after interface close error\n");
#else
	REQUIRE(ret == -EREMOTEIO && g_interface_close_calls == 1 &&
		g_cpu_off_calls == 0,
		"actual runtime-suspend caller receives cleanup failure before SoC teardown");
	REQUIRE(g_core_clock_on_calls == 1 && g_clock_live && g_wake_source.active,
		"caller keeps the existing pre-suspend core-clock action and owned stages");
	printf("PASS: actual runtime-suspend caller propagates cleanup error and retains ownership\n");
#endif
}
#endif

int main(void)
{
	test_resume_failure_through_real_runtime_caller();
	test_interface_failure_and_direct_bootup_close();
	test_inverse_failure_quarantines_and_retry_is_blocked();
	test_interface_close_failure_preserves_lower_resources();
	test_cpu_inverse_failure_retains_ownership();
	test_firmware_allocation_error_and_unknown_global_owner();
	test_partial_allocator_error_blocks_bootup_close();
	test_success_roundtrip_and_free_error();
	test_ambiguous_cpu_acquisition_quarantines();
	test_actual_hwdev_callback_pm_transaction();
#ifndef CONFIG_NPU_USE_BOOT_IOCTL
	test_clock_prepare_failure_cleans_only_prior_stages();
	test_stm_enable_failure_quarantines_unknown_outcome();
	test_stm_disable_failure_preserves_ref_ownership();
	test_transactional_runtime_pm_reference();
	test_actual_runtime_suspend_caller_propagates_cleanup_error();
#endif
	return 0;
}
