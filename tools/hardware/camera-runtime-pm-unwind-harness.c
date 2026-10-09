/* Host harness for the exact helpers proposed in camera-runtime-pm-unwind.patch.
 * test-camera-runtime-pm-unwind.py inserts the patched helpers and, with
 * CONFIG_PM, the pinned pm_runtime_resume_and_get() implementation.
 */
#include <errno.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>

#define ENABLE_DYNAMIC_MEM
#define RPM_GET_PUT 1
#define IS_RM_SS0_POWER_ON 4

typedef unsigned int u32;

struct device {
	int usage_count;
	int resume_result;
	int resume_calls;
	int put_calls;
	int last_flags;
};

struct fake_pdev {
	struct device dev;
};

struct regulator {
	int id;
	int enabled;
};

struct is_resource {
	struct fake_pdev *pdev;
};

struct is_resourcemgr {
	struct regulator **phy_ldos;
	int num_phy_ldos;
	unsigned long state;
};

struct is_core {
	struct fake_pdev *pdev;
	int resource_count;
	int core_count;
};

static int fail_disable_at = -1;
static int disable_count;
static int disable_order[8];
static int dynamic_mem_deinit_calls;
static int dynamic_mem_deinit_result;
static int resource_clear_calls;
static int pm_relax_calls;
#ifndef CONFIG_PM
static int direct_resume_calls;
static int direct_resume_result;
#endif
static char log_lines[32][192];
static int log_count;

static void fake_err(const char *format, ...)
{
	va_list args;

	if (log_count >= (int)(sizeof(log_lines) / sizeof(log_lines[0])))
		return;
	va_start(args, format);
	vsnprintf(log_lines[log_count], sizeof(log_lines[log_count]), format, args);
	va_end(args);
	log_count++;
}

static void fake_usleep_range(unsigned int minimum, unsigned int maximum)
{
	(void)minimum;
	(void)maximum;
}

static int fake_regulator_disable(struct regulator *regulator)
{
	disable_order[disable_count++] = regulator->id;
	if (regulator->id == fail_disable_at)
		return -EIO;
	if (!regulator->enabled)
		return -EINVAL;
	regulator->enabled = 0;
	return 0;
}

static int fake_count_enabled(struct regulator *regs, int count)
{
	int i, enabled = 0;

	for (i = 0; i < count; i++)
		if (regs[i].enabled)
			enabled++;
	return enabled;
}

static int fake_has_log_fragment(const char *fragment)
{
	int i;

	for (i = 0; i < log_count; i++)
		if (strstr(log_lines[i], fragment))
			return 1;
	return 0;
}

#ifdef CONFIG_PM
static int __pm_runtime_resume(struct device *dev, int flags)
{
	dev->usage_count++;
	dev->resume_calls++;
	dev->last_flags = flags;
	return dev->resume_result;
}

static void pm_runtime_put_noidle(struct device *dev)
{
	dev->usage_count--;
	dev->put_calls++;
}

/* CAMERA_PM_RUNTIME_HELPER */
#else
static int is_sensor_runtime_resume(struct device *dev)
{
	(void)dev;
	direct_resume_calls++;
	return direct_resume_result;
}
#endif

#define regulator_disable fake_regulator_disable
#define err(...) fake_err(__VA_ARGS__)
#define usleep_range fake_usleep_range

static void set_bit(int bit, unsigned long *state)
{
	*state |= 1UL << bit;
}

static int is_resourcemgr_deinit_dynamic_mem(struct is_resourcemgr *resourcemgr)
{
	(void)resourcemgr;
	dynamic_mem_deinit_calls++;
	return dynamic_mem_deinit_result;
}

static void is_resource_clear(struct is_resourcemgr *resourcemgr)
{
	(void)resourcemgr;
	resource_clear_calls++;
}

static void pm_relax(struct device *dev)
{
	(void)dev;
	pm_relax_calls++;
}

/* CAMERA_SENSOR_RUNTIME_HELPERS */

static int check(int condition, const char *message)
{
	if (!condition) {
		fprintf(stderr, "FAIL: %s\n", message);
		return 1;
	}
	return 0;
}

int main(void)
{
	struct regulator regs[4];
	struct regulator *ldos[4];
	struct is_resourcemgr resourcemgr;
	struct fake_pdev pdev;
	struct is_resource resource;
	struct is_core core;
	unsigned long initial_state = 1UL << 20;
	int i, ret, failures = 0;

	for (i = 0; i < 4; i++) {
		regs[i].id = i;
		regs[i].enabled = 1;
		ldos[i] = &regs[i];
	}
	resourcemgr.phy_ldos = ldos;
	resourcemgr.num_phy_ldos = 4;
	resourcemgr.state = initial_state;
	pdev.dev.usage_count = 0;
	pdev.dev.resume_result = 0;
	pdev.dev.resume_calls = 0;
	pdev.dev.put_calls = 0;
	pdev.dev.last_flags = 0;
	resource.pdev = &pdev;
	core.pdev = &pdev;
	core.resource_count = 3;
	core.core_count = 5;

#ifdef CONFIG_PM
	/* The pinned helper balances the usage count on negative resume returns. */
	pdev.dev.resume_result = -EIO;
	ret = is_resource_sensor_runtime_get(&resource);
	failures += check(ret == -EIO, "negative PM resume error is preserved");
	failures += check(pdev.dev.usage_count == 0 && pdev.dev.put_calls == 1,
		"negative PM resume balances exactly one usage reference");
	failures += check(pdev.dev.resume_calls == 1 &&
		pdev.dev.last_flags == RPM_GET_PUT, "PM resume is called once synchronously");

	/* Positive pm_runtime_resume() results normalize to success and retain one get. */
	pdev.dev.usage_count = 0;
	pdev.dev.resume_result = 1;
	pdev.dev.resume_calls = 0;
	pdev.dev.put_calls = 0;
	ret = is_resource_sensor_runtime_get(&resource);
	failures += check(ret == 0 && pdev.dev.usage_count == 1 &&
		pdev.dev.put_calls == 0, "positive PM resume is success with one retained get");
	pdev.dev.usage_count = 0;
	pdev.dev.resume_calls = 0;
	pdev.dev.put_calls = 0;
#else
	direct_resume_result = 1;
	direct_resume_calls = 0;
	ret = is_resource_sensor_runtime_get(&resource);
	failures += check(ret == 0 && direct_resume_calls == 1,
		"non-PM positive resume result is normalized to success");
#endif
	resourcemgr.state = initial_state;
	ret = is_resource_sensor_power_on(&resourcemgr, &resource, &core, 2, 0);
	failures += check(ret == 0 &&
		resourcemgr.state == (initial_state | (1UL << (IS_RM_SS0_POWER_ON + 2))),
		"successful resume sets only the requested sensor power bit");
	failures += check(disable_count == 0 && dynamic_mem_deinit_calls == 0 &&
		resource_clear_calls == 0 && pm_relax_calls == 0,
		"successful resume preserves the normal resource setup");
	resourcemgr.state = initial_state;

	/* Shared-core failure must not tear down first-resource state. */
	pdev.dev.resume_result = -EIO;
#ifndef CONFIG_PM
	direct_resume_result = -EIO;
	direct_resume_calls = 0;
#else
	pdev.dev.usage_count = 0;
	pdev.dev.resume_calls = 0;
	pdev.dev.put_calls = 0;
#endif
	ret = is_resource_sensor_power_on(&resourcemgr, &resource, &core, 2, 1);
	failures += check(ret == -EIO, "shared-resource resume error is preserved");
	failures += check(resourcemgr.state == initial_state,
		"shared-resource failure does not set a sensor power bit");
	failures += check(disable_count == 0 && dynamic_mem_deinit_calls == 0 &&
		resource_clear_calls == 0 && pm_relax_calls == 0,
		"shared-resource failure preserves common LDO, memory, and wake state");
	failures += check(core.resource_count == 3 && core.core_count == 5,
		"resume failure leaves resource/core counts unchanged");
#ifdef CONFIG_PM
	failures += check(pdev.dev.usage_count == 0 && pdev.dev.put_calls == 1,
		"shared PM failure leaves no leaked device usage reference");
#else
	failures += check(direct_resume_calls == 1,
		"shared non-PM failure calls direct runtime resume once");
#endif

	/* First-resource failure reverses acquired votes and common setup. */
	disable_count = 0;
	dynamic_mem_deinit_calls = 0;
	resource_clear_calls = 0;
	pm_relax_calls = 0;
	ret = is_resource_sensor_power_on(&resourcemgr, &resource, &core, 2, 0);
	failures += check(ret == -EIO, "first-resource PM error is preserved");
	failures += check(disable_count == 4 && disable_order[0] == 3 &&
		disable_order[1] == 2 && disable_order[2] == 1 && disable_order[3] == 0,
		"first-resource cleanup disables LDO votes in reverse order");
	failures += check(fake_count_enabled(regs, 4) == 0,
		"successful LDO cleanup releases every acquired vote");
	failures += check(dynamic_mem_deinit_calls == 1 && resource_clear_calls == 1 &&
		pm_relax_calls == 1, "first-resource cleanup attempts memory, state, and wake release");
	failures += check(resourcemgr.state == initial_state,
		"first-resource failure does not set a sensor power bit");
	failures += check(core.resource_count == 3 && core.core_count == 5,
		"first-resource failure leaves resource/core counts unchanged");

	/* Cleanup errors remain secondary; every vote and common cleanup is attempted. */
	for (i = 0; i < 4; i++)
		regs[i].enabled = 1;
	disable_count = 0;
	dynamic_mem_deinit_calls = 0;
	resource_clear_calls = 0;
	pm_relax_calls = 0;
	fail_disable_at = 1;
	dynamic_mem_deinit_result = -ENOMEM;
	log_count = 0;
	ret = is_resource_sensor_power_on(&resourcemgr, &resource, &core, 2, 0);
	failures += check(ret == -EIO, "cleanup errors do not replace resume error");
	failures += check(disable_count == 4 && regs[1].enabled == 1,
		"failed LDO disable is visible while later votes are still attempted");
	failures += check(dynamic_mem_deinit_calls == 1 && resource_clear_calls == 1 &&
		pm_relax_calls == 1, "cleanup continues after LDO/deinit errors");
	failures += check(fake_has_log_fragment("hardware state unknown"),
		"LDO cleanup failure reports hardware-state uncertainty");
	failures += check(resourcemgr.state == initial_state,
		"cleanup errors still do not set the sensor power bit");

#ifdef CONFIG_PM
	failures += check(pdev.dev.usage_count == 0 && pdev.dev.put_calls == 3,
		"all failed PM attempts are balanced exactly once");
#endif

	if (failures)
		return 1;
	puts("camera runtime-PM unwind injection: PASS");
	return 0;
}
