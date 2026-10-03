/* Host harness for the exact extracted sensor-clock functions proposed in the
 * camera-sensor-clock-unwind.patch. The Python test inserts function bodies
 * from the pinned baseline or its temporary patched copy at the marker below.
 */
#include <errno.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>

typedef unsigned int u32;

struct device {
	int unused;
};

struct platform_device {
	struct device dev;
};

struct exynos_platform_is_sensor {
	int (*iclk_cfg)(struct device *dev, u32 scenario, u32 channel);
	int (*iclk_on)(struct device *dev, u32 scenario, u32 channel);
	int (*iclk_off)(struct device *dev, u32 scenario, u32 channel);
	u32 scenario;
	u32 csi_ch;
};

struct is_core {
	struct platform_device *pdev;
};

struct is_device_sensor {
	struct platform_device *pdev;
	struct exynos_platform_is_sensor *pdata;
	void *private_data;
	unsigned long state;
	int device_id;
};

int exynos_is_sensor_iclk_cfg(struct device *dev, u32 scenario, u32 channel);
int exynos_is_sensor_iclk_on(struct device *dev, u32 scenario, u32 channel);
int exynos_is_sensor_iclk_off(struct device *dev, u32 scenario, u32 channel);
static int is_sensor_iclk_on(struct is_device_sensor *device);
int is_sensor_iclk_off(struct is_device_sensor *device);

static int clock_votes[8];
static int physical_on[8];
static int fail_enable_id = -1;
static int fail_enable_remaining;
static int fail_enable_skip;
static int fail_enable_leaves_on;
/* A modeled is_disable lookup failure: no vote decrement occurs. */
static int fail_disable_id = -1;
static int fail_disable_remaining;
static int enable_calls;
static int injected_enable_failures;
static int disable_calls;
static int disable_order[128];
static char diagnostics[4096];

static const char * const clock_names[8] = {
	"GATE_MIPI_PHY_LINK_WRAP_QCH_CSIS0",
	"GATE_MIPI_PHY_LINK_WRAP_QCH_CSIS1",
	"GATE_MIPI_PHY_LINK_WRAP_QCH_CSIS2",
	"GATE_MIPI_PHY_LINK_WRAP_QCH_CSIS3",
	"GATE_MIPI_PHY_LINK_WRAP_QCH_CSIS4",
	"GATE_MIPI_PHY_LINK_WRAP_QCH_CSIS5",
	"GATE_MIPI_PHY_LINK_WRAP_QCH_CSIS6",
	"GATE_CSIS_PDP_QCH_DMA",
};

static int clock_id(const char *name)
{
	int i;

	for (i = 0; i < 8; i++)
		if (!strcmp(name, clock_names[i]))
			return i;
	return -1;
}

static int fake_is_enable(struct device *dev, const char *name)
{
	int id = clock_id(name);

	(void)dev;
	enable_calls++;
	if (id < 0)
		return -EINVAL;
	if (id == fail_enable_id && fail_enable_remaining > 0) {
		if (fail_enable_skip > 0) {
			fail_enable_skip--;
		} else {
			fail_enable_remaining--;
			injected_enable_failures++;
			if (fail_enable_leaves_on)
				physical_on[id] = 1;
			return -EIO;
		}
	}
	clock_votes[id]++;
	physical_on[id] = 1;
	return 0;
}

static int fake_is_disable(struct device *dev, const char *name)
{
	int id = clock_id(name);

	(void)dev;
	if (disable_calls < (int)(sizeof(disable_order) / sizeof(disable_order[0])))
		disable_order[disable_calls] = id;
	disable_calls++;
	if (id < 0)
		return -EINVAL;
	if (id == fail_disable_id && fail_disable_remaining > 0) {
		fail_disable_remaining--;
		return -EINVAL;
	}
	if (clock_votes[id] <= 0)
		return -EINVAL;
	clock_votes[id]--;
	if (!clock_votes[id])
		physical_on[id] = 0;
	return 0;
}

static void fake_pr_err(const char *format, ...)
{
	va_list args;
	char line[256];
	size_t used = strlen(diagnostics);

	va_start(args, format);
	vsnprintf(line, sizeof(line), format, args);
	va_end(args);
	if (used < sizeof(diagnostics) - 1)
		snprintf(diagnostics + used, sizeof(diagnostics) - used, "%s", line);
}

static void reset_clocks(int shared_vote)
{
	int i;

	for (i = 0; i < 8; i++) {
		clock_votes[i] = shared_vote;
		physical_on[i] = !!shared_vote;
	}
	fail_enable_id = -1;
	fail_enable_remaining = 0;
	fail_enable_skip = 0;
	fail_enable_leaves_on = 0;
	fail_disable_id = -1;
	fail_disable_remaining = 0;
	enable_calls = 0;
	injected_enable_failures = 0;
	disable_calls = 0;
	diagnostics[0] = '\0';
	memset(disable_order, 0xff, sizeof(disable_order));
}

static int count_votes(void)
{
	int i, count = 0;

	for (i = 0; i < 8; i++)
		count += clock_votes[i];
	return count;
}

static int check(int condition, const char *message)
{
	if (!condition) {
		fprintf(stderr, "FAIL: %s\n", message);
		return 1;
	}
	return 0;
}

static int fake_test_bit(int bit, unsigned long *state)
{
	return !!(*state & (1UL << bit));
}

static void fake_set_bit(int bit, unsigned long *state)
{
	*state |= 1UL << bit;
}

static void fake_clear_bit(int bit, unsigned long *state)
{
	*state &= ~(1UL << bit);
}

static void setup_sensor(struct is_device_sensor *sensor,
		struct exynos_platform_is_sensor *pdata,
		struct is_core *core, struct platform_device *pdev)
{
	memset(sensor, 0, sizeof(*sensor));
	memset(pdata, 0, sizeof(*pdata));
	memset(core, 0, sizeof(*core));
	memset(pdev, 0, sizeof(*pdev));
	pdata->iclk_cfg = exynos_is_sensor_iclk_cfg;
	pdata->iclk_on = exynos_is_sensor_iclk_on;
	pdata->iclk_off = exynos_is_sensor_iclk_off;
	pdata->scenario = 0;
	pdata->csi_ch = 3;
	core->pdev = pdev;
	sensor->pdev = pdev;
	sensor->pdata = pdata;
	sensor->private_data = core;
}

#define ARRAY_SIZE(array) (sizeof(array) / sizeof((array)[0]))
#define IS_SENSOR_ICLK_ON 3
#define FIMC_BUG(condition) do { if (condition) return -EINVAL; } while (0)
#define test_bit(bit, state) fake_test_bit((bit), (state))
#define set_bit(bit, state) fake_set_bit((bit), (state))
#define clear_bit(bit, state) fake_clear_bit((bit), (state))
#define merr(...) do { } while (0)
#define is_enable fake_is_enable
#define is_disable fake_is_disable
#define pr_debug(...) do { } while (0)
#define pr_err(...) fake_pr_err(__VA_ARGS__)

/* CAMERA_SENSOR_CLOCK_FUNCTIONS */

#ifdef CAMERA_EXPECT_BASELINE
int main(void)
{
	struct device dev = { 0 };
	struct is_device_sensor sensor;
	struct exynos_platform_is_sensor pdata;
	struct is_core core;
	struct platform_device pdev;
	int i, ret, failures = 0;

	/* The actual generic caller sees false success and sets its ON bit. */
	for (i = 0; i < 8; i++) {
		reset_clocks(0);
		setup_sensor(&sensor, &pdata, &core, &pdev);
		fail_enable_id = i;
		fail_enable_remaining = 1;
		ret = is_sensor_iclk_on(&sensor);
		failures += check(ret == 0 && fake_test_bit(IS_SENSOR_ICLK_ON, &sensor.state),
			"baseline caller sets ICLK_ON after hidden cfg acquisition failure");
		failures += check(injected_enable_failures == 1,
			"baseline case injects exactly one real helper failure");
	}

	/* Baseline accepts an invalid channel after touching clocks and leaks DMA. */
	reset_clocks(0);
	setup_sensor(&sensor, &pdata, &core, &pdev);
	pdata.csi_ch = 7;
	ret = is_sensor_iclk_on(&sensor);
	failures += check(ret == 0 && fake_test_bit(IS_SENSOR_ICLK_ON, &sensor.state),
		"baseline caller hides invalid-channel failure");
	failures += check(count_votes() == 1 && clock_votes[7] == 1,
		"baseline invalid-channel path leaves the cfg DMA vote behind");

	/* Baseline loses a selected-clock acquisition error after dropping all gates. */
	reset_clocks(0);
	setup_sensor(&sensor, &pdata, &core, &pdev);
	fail_enable_id = 3;
	fail_enable_remaining = 1;
	fail_enable_skip = 1; /* cfg's CSIS3 acquire succeeds; the later on acquire fails. */
	ret = is_sensor_iclk_on(&sensor);
	failures += check(ret == 0 && fake_test_bit(IS_SENSOR_ICLK_ON, &sensor.state),
		"baseline caller sets ICLK_ON after hidden selected-clock error");
	failures += check(count_votes() == 1 && clock_votes[7] == 1,
		"baseline selected-clock failure retains only the DMA cfg vote");

	/* Baseline off wrapper hides a failed gate disable and partially tears down. */
	reset_clocks(0);
	setup_sensor(&sensor, &pdata, &core, &pdev);
	ret = is_sensor_iclk_on(&sensor);
	fail_disable_id = 3;
	fail_disable_remaining = 1;
	ret = is_sensor_iclk_off(&sensor);
	failures += check(ret == 0 && !fake_test_bit(IS_SENSOR_ICLK_ON, &sensor.state),
		"baseline caller clears ICLK_ON after hidden gate-disable error");
	failures += check(clock_votes[3] == 1 && clock_votes[7] == 0,
		"baseline off error is hidden after DMA was already disabled");

	/* Baseline off accepts channel 7 and drops the DMA vote without ownership. */
	reset_clocks(1);
	ret = exynos_is_sensor_iclk_off(&dev, 0, 7);
	failures += check(ret == 0 && clock_votes[7] == 0,
		"baseline accepts unsupported off channel and releases a shared DMA vote");

	if (failures)
		return 1;
	puts("BASELINE_REPRODUCED: clock acquisition errors are hidden and votes leak");
	return 0;
}
#else
int main(void)
{
	struct device dev = { 0 };
	struct is_device_sensor sensor;
	struct exynos_platform_is_sensor pdata;
	struct is_core core;
	struct platform_device pdev;
	int i, j, ret, failures = 0;

	/* Every cfg acquisition failure returns its own error and reverses only
	 * this invocation's successful earlier acquisitions in reverse order.
	 */
	for (i = 0; i < 8; i++) {
		reset_clocks(1);
		fail_enable_id = i;
		fail_enable_remaining = 1;
		setup_sensor(&sensor, &pdata, &core, &pdev);
		ret = is_sensor_iclk_on(&sensor);
		failures += check(ret == -EIO, "actual caller preserves cfg acquisition error");
		failures += check(enable_calls == i + 1, "cfg stops at the failed acquisition");
		failures += check(!fake_test_bit(IS_SENSOR_ICLK_ON, &sensor.state),
			"actual caller does not set ICLK_ON after cfg failure");
		failures += check(count_votes() == 8, "cfg rollback preserves preexisting shared votes");
		failures += check(disable_calls == i, "cfg rolls back exactly earlier successful acquisitions");
		for (j = 0; j < 8; j++)
			failures += check(clock_votes[j] == 1,
				"cfg failure leaves one unrelated shared vote per clock");
		for (j = 0; j < i; j++)
			failures += check(disable_order[j] == i - j - 1,
				"cfg rollback follows reverse acquisition order");
	}

	/* A provider can change physical state before reporting an enable error;
	 * the harness makes that uncertainty visible despite successful vote unwind.
	 */
	reset_clocks(0);
	setup_sensor(&sensor, &pdata, &core, &pdev);
	fail_enable_id = 0;
	fail_enable_remaining = 1;
	fail_enable_leaves_on = 1;
	ret = is_sensor_iclk_on(&sensor);
	failures += check(ret == -EIO && count_votes() == 0 && physical_on[0] == 1,
		"failed provider enable may leave hardware state despite no owned vote");
	failures += check(!fake_test_bit(IS_SENSOR_ICLK_ON, &sensor.state),
		"actual caller rejects first provider enable failure");

	/* Failed rollback is visible, best effort continues, and the first error wins. */
	reset_clocks(1);
	setup_sensor(&sensor, &pdata, &core, &pdev);
	fail_enable_id = 7;
	fail_enable_remaining = 1;
	fail_disable_id = 3;
	fail_disable_remaining = 1;
	ret = is_sensor_iclk_on(&sensor);
	failures += check(ret == -EIO, "cfg rollback failure does not replace acquire error");
	failures += check(disable_calls == 7 && count_votes() == 9 && clock_votes[3] == 2,
		"cfg cleanup continues after one failed disable and preserves both votes");
	failures += check(strstr(diagnostics, "clock state unknown") != NULL,
		"incomplete cfg rollback reports uncertainty rather than physical success");

	/* Invalid channels are rejected before any clock is acquired. */
	reset_clocks(0);
	setup_sensor(&sensor, &pdata, &core, &pdev);
	pdata.csi_ch = 7;
	ret = is_sensor_iclk_on(&sensor);
	failures += check(ret == -EINVAL && enable_calls == 0 && count_votes() == 0,
		"actual caller rejects an invalid channel before acquisition");
	failures += check(!fake_test_bit(IS_SENSOR_ICLK_ON, &sensor.state),
		"actual caller leaves ICLK_ON clear for invalid channel");
	reset_clocks(1);
	ret = exynos_is_sensor_iclk_off(&dev, 0, 7);
	failures += check(ret == -EINVAL && count_votes() == 8 && disable_calls == 0,
		"off rejects channel 7 without releasing any shared vote");

	/* Successful cfg/on/off preserves one caller vote, including shared votes. */
	reset_clocks(1);
	setup_sensor(&sensor, &pdata, &core, &pdev);
	ret = is_sensor_iclk_on(&sensor);
	failures += check(ret == 0 && count_votes() == 10,
		"actual caller's cfg/on adds only its shared votes");
	failures += check(fake_test_bit(IS_SENSOR_ICLK_ON, &sensor.state),
		"actual caller sets ICLK_ON after both callbacks succeed");
	for (i = 0; i < 7; i++)
		failures += check(clock_votes[i] == (i == 3 ? 2 : 1),
			"on converts only this caller's gate votes and preserves shared votes");
	failures += check(clock_votes[7] == 2, "on retains the shared DMA votes");
	ret = is_sensor_iclk_off(&sensor);
	failures += check(ret == 0, "successful off callback propagates success");
	failures += check(!fake_test_bit(IS_SENSOR_ICLK_ON, &sensor.state),
		"actual caller clears ICLK_ON only after successful off");
	for (i = 0; i < 8; i++)
		failures += check(clock_votes[i] == 1,
			"off releases only this caller's votes and preserves shared votes");

	/* Inject a disable failure at each channel position while converting cfg votes.
	 * The on callback drains only the votes left by its own cfg after failure.
	 */
	for (i = 0; i < 7; i++) {
		reset_clocks(1);
		setup_sensor(&sensor, &pdata, &core, &pdev);
		fail_disable_id = i;
		fail_disable_remaining = 1;
		ret = is_sensor_iclk_on(&sensor);
		failures += check(ret == -EINVAL, "actual caller propagates each modeled gate lookup error");
		failures += check(!fake_test_bit(IS_SENSOR_ICLK_ON, &sensor.state),
			"actual caller leaves ICLK_ON clear after on failure");
		failures += check(count_votes() == 8, "on failure drains only cfg-owned votes");
		for (j = 0; j < 8; j++)
			failures += check(clock_votes[j] == 1,
				"on failure preserves unrelated shared votes");
		failures += check(disable_calls == 9 && disable_order[i + 1] == 7,
			"on failure releases DMA first during reverse cleanup");
		for (j = 0; j <= i; j++)
			failures += check(disable_order[j] == j,
				"on transition reaches the injected gate in channel order");
		for (j = 0; j < 7 - i; j++)
			failures += check(disable_order[i + 2 + j] == 6 - j,
				"on error releases remaining setup votes in reverse order");
	}

	/* Every selected CSIS acquisition can fail after cfg succeeds. */
	for (i = 0; i < 7; i++) {
		reset_clocks(1);
		setup_sensor(&sensor, &pdata, &core, &pdev);
		pdata.csi_ch = i;
		fail_enable_id = i;
		fail_enable_remaining = 1;
		fail_enable_skip = 1;
		ret = is_sensor_iclk_on(&sensor);
		failures += check(ret == -EIO && injected_enable_failures == 1,
			"on preserves each selected-clock acquisition failure");
		failures += check(!fake_test_bit(IS_SENSOR_ICLK_ON, &sensor.state),
			"actual caller leaves ICLK_ON clear after selected-clock failure");
		failures += check(count_votes() == 8 && disable_calls == 8 && disable_order[7] == 7,
			"selected-clock failure releases this caller's votes only");
		for (j = 0; j < 8; j++)
			failures += check(clock_votes[j] == 1,
				"selected-clock failure preserves unrelated shared votes");
		for (j = 0; j < 7; j++)
			failures += check(disable_order[j] == j,
				"selected-clock failure follows successful cfg gate transitions");
	}

	/* If cleanup itself fails, keep the original error and state the uncertainty. */
	reset_clocks(1);
	setup_sensor(&sensor, &pdata, &core, &pdev);
	fail_disable_id = 5;
	fail_disable_remaining = 2;
	ret = is_sensor_iclk_on(&sensor);
	failures += check(ret == -EINVAL, "on cleanup error cannot replace first gate error");
	failures += check(count_votes() == 9 && clock_votes[5] == 2,
		"persistently failed cleanup leaves its vote while preserving shared votes");
	failures += check(strstr(diagnostics, "clock state unknown") != NULL,
		"incomplete on cleanup is explicitly uncertain");

	/* Off failure on the channel has no mutation; DMA failure restores channel. */
	reset_clocks(1);
	setup_sensor(&sensor, &pdata, &core, &pdev);
	ret = is_sensor_iclk_on(&sensor);
	fail_disable_id = 3;
	fail_disable_remaining = 1;
	ret = is_sensor_iclk_off(&sensor);
	failures += check(ret == -EINVAL && clock_votes[3] == 2 && clock_votes[7] == 2,
		"channel-off failure returns before releasing the DMA vote");
	failures += check(fake_test_bit(IS_SENSOR_ICLK_ON, &sensor.state),
		"actual caller retains ICLK_ON after off failure");

	reset_clocks(1);
	setup_sensor(&sensor, &pdata, &core, &pdev);
	ret = is_sensor_iclk_on(&sensor);
	fail_disable_id = 7;
	fail_disable_remaining = 1;
	ret = is_sensor_iclk_off(&sensor);
	failures += check(ret == -EINVAL && clock_votes[3] == 2 && clock_votes[7] == 2,
		"DMA-off failure restores the selected channel vote");
	failures += check(fake_test_bit(IS_SENSOR_ICLK_ON, &sensor.state),
		"actual caller retains ICLK_ON after DMA-off failure");

	/* Failed restoration is reported without replacing the original DMA error. */
	reset_clocks(1);
	setup_sensor(&sensor, &pdata, &core, &pdev);
	ret = is_sensor_iclk_on(&sensor);
	fail_disable_id = 7;
	fail_disable_remaining = 1;
	fail_enable_id = 3;
	fail_enable_remaining = 1;
	ret = is_sensor_iclk_off(&sensor);
	failures += check(ret == -EINVAL, "off restore failure preserves the DMA error");
	failures += check(clock_votes[3] == 1 && clock_votes[7] == 2 && count_votes() == 9,
		"failed restore leaves partial ownership while preserving shared references");
	failures += check(strstr(diagnostics, "clock state unknown") != NULL,
		"failed off restoration reports uncertainty");
	failures += check(fake_test_bit(IS_SENSOR_ICLK_ON, &sensor.state),
		"actual caller retains logical ON state after failed physical unwind");

	if (failures)
		return 1;
	puts("PASS: extracted sensor clock callbacks propagate and unwind owned votes");
	return 0;
}
#endif
