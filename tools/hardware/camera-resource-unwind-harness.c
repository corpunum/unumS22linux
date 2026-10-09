/* Template: test-camera-resource-unwind.py inserts the rollback helper from
 * camera-resource-unwind.patch at the marker below, so failure injection
 * exercises the exact C helper proposed for the kernel patch.
 */
#include <errno.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define ENABLE_DYNAMIC_MEM

struct regulator {
	int id;
	int enabled;
};

struct is_resourcemgr {
	struct regulator **phy_ldos;
	int num_phy_ldos;
};

struct fake_device {
	int unused;
};

struct fake_pdev {
	struct fake_device dev;
};

struct fake_core {
	struct fake_pdev *pdev;
	int resource_count;
	int core_count;
};

static int fail_enable_at = -1;
static int fail_disable_at = -1;
static int enable_count;
static int disable_count;
static int disable_order[8];
static int fail_enable_leaves_on;
static char log_lines[16][192];
static int log_count;
static int dynamic_mem_deinit_calls;
static int dynamic_mem_deinit_result;
static int resource_clear_calls;
static int pm_relax_calls;

static void reset_fake_state(struct regulator *regs, int count)
{
	int i;

	fail_enable_at = -1;
	fail_disable_at = -1;
	fail_enable_leaves_on = 0;
	enable_count = 0;
	disable_count = 0;
	log_count = 0;
	for (i = 0; i < count; i++)
		regs[i].enabled = 0;
}

static int fake_regulator_enable(struct regulator *regulator)
{
	enable_count++;
	if (regulator->id == fail_enable_at) {
		if (fail_enable_leaves_on)
			regulator->enabled = 1;
		return -EIO;
	}
	regulator->enabled = 1;
	return 0;
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

static int count_enabled(struct regulator *regs, int count)
{
	int i, enabled = 0;

	for (i = 0; i < count; i++)
		if (regs[i].enabled)
			enabled++;
	return enabled;
}

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

#define regulator_enable fake_regulator_enable
#define regulator_disable fake_regulator_disable
#define err(...) fake_err(__VA_ARGS__)
#define usleep_range fake_usleep_range

/* CAMERA_RESOURCE_UNWIND_HELPER */

static int has_log_fragment(const char *fragment)
{
	int i;

	for (i = 0; i < log_count; i++)
		if (strstr(log_lines[i], fragment))
			return 1;
	return 0;
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

static void pm_relax(struct fake_device *device)
{
	(void)device;
	pm_relax_calls++;
}

static int exercise_first_resource_failure(struct is_resourcemgr *resourcemgr,
		struct fake_core *core)
{
	int ret = -EIO;
	int cleanup_ret = 0;

	goto err_first_resource_phy_ldo;

/* CAMERA_RESOURCE_FAILURE_LABEL */

rsc_err:
	return ret;
}

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
	int i, j, ret, failures = 0;

	for (i = 0; i < 4; i++) {
		regs[i].id = i;
		regs[i].enabled = 0;
		ldos[i] = &regs[i];
	}
	resourcemgr.phy_ldos = ldos;
	resourcemgr.num_phy_ldos = 4;

	/* Every enable position fails after precisely the earlier votes succeed. */
	for (i = 0; i < 4; i++) {
		reset_fake_state(regs, 4);
		fail_enable_at = i;
		ret = is_resource_enable_phy_ldos(&resourcemgr);
		failures += check(ret == -EIO, "original enable error is preserved");
		failures += check(enable_count == i + 1, "enable stops at injected failure");
		failures += check(disable_count == i, "only successful enables are rolled back");
		failures += check(count_enabled(regs, 4) == 0, "successful rollback leaves no votes");
		for (j = 0; j < i; j++)
			failures += check(disable_order[j] == i - j - 1,
				"rollback is reverse acquisition order");
	}

	reset_fake_state(regs, 4);
	ret = is_resource_enable_phy_ldos(&resourcemgr);
	failures += check(ret == 0, "all LDO enables succeed");
	failures += check(enable_count == 4 && disable_count == 0,
		"success path does not run rollback");
	failures += check(count_enabled(regs, 4) == 4, "success retains all acquired votes");

	/* Failed rollback is visible and later regulators are still attempted. */
	reset_fake_state(regs, 4);
	fail_enable_at = 3;
	fail_disable_at = 1;
	ret = is_resource_enable_phy_ldos(&resourcemgr);
	failures += check(ret == -EIO, "rollback failure preserves original enable error");
	failures += check(disable_count == 3, "rollback continues after a disable error");
	failures += check(disable_order[0] == 2 && disable_order[1] == 1 &&
		disable_order[2] == 0, "failed rollback still attempts every prior vote");
	failures += check(count_enabled(regs, 4) == 1,
		"failed disable remains explicitly observable as an outstanding vote");
	failures += check(has_log_fragment("failed to enable PHY LDO[3]"),
		"rollback diagnostic identifies the failed enable index");
	failures += check(has_log_fragment("hardware state unknown"),
		"rollback failure reports hardware-state uncertainty");

	/* An enable error may still leave hardware on; do not claim physical cleanup. */
	reset_fake_state(regs, 4);
	fail_enable_at = 2;
	fail_enable_leaves_on = 1;
	ret = is_resource_enable_phy_ldos(&resourcemgr);
	failures += check(ret == -EIO, "physical uncertainty preserves provider error");
	failures += check(disable_count == 2 && count_enabled(regs, 4) == 1,
		"only earlier successful votes are reversed; failed LDO is not decremented");
	failures += check(regs[2].enabled,
		"injected provider error can retain unknown physical state");
	failures += check(has_log_fragment("failing LDO[2] hardware state after error"),
		"error path records which failing LDO remains physically uncertain");

	/* Exercise the exact first-resource cleanup branch extracted from the patch. */
	{
		struct fake_pdev pdev;
		struct fake_core core;

		core.pdev = &pdev;
		core.resource_count = 0;
		core.core_count = 0;
		dynamic_mem_deinit_calls = 0;
		dynamic_mem_deinit_result = -ENOMEM;
		resource_clear_calls = 0;
		pm_relax_calls = 0;
		ret = exercise_first_resource_failure(&resourcemgr, &core);
		failures += check(ret == -EIO,
			"dynamic-memory cleanup error does not replace enable error");
		failures += check(dynamic_mem_deinit_calls == 1 && resource_clear_calls == 1 &&
			pm_relax_calls == 1, "failure branch attempts dynamic-memory, resource, and wake cleanup");
		failures += check(core.resource_count == 0 && core.core_count == 0,
			"failed first acquire does not increment resource/core counts");
	}

	if (failures)
		return 1;
	puts("camera PHY-LDO unwind injection: PASS");
	return 0;
}
