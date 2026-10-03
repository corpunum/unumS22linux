/* Host harness for the exact extracted sensor-clock functions proposed in the
 * camera-sensor-clock-unwind.patch. The Python test inserts function bodies
 * from the pinned baseline or its temporary patched copy at the marker below.
 */
#include <errno.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef unsigned int u32;

struct device {
	int unused;
};

struct platform_device {
	struct device dev;
};

struct v4l2_subdev { int unused; };
struct vb2_queue { void *drv_priv; void *owner; };
struct video_device { struct device dev; };
struct is_video {
	int device_type;
	int video_type;
	int id;
	struct video_device vd;
};
struct is_video_ctx {
	struct {
		struct vb2_queue *vbq;
	} queue;
	void *device;
	void *group;
	struct is_video *video;
	unsigned long state;
	bool iclk_quarantine_retained;
};
struct file {
	void *private_data;
	struct is_video *video;
};
struct is_device_sensor;
struct is_device_ischain { int instance; int open_cnt; struct is_device_sensor *sensor; };
struct is_resourcemgr { int qos_refcount; };
struct is_module_enum;

struct exynos_platform_is_sensor {
	int (*iclk_cfg)(struct device *dev, u32 scenario, u32 channel);
	int (*iclk_on)(struct device *dev, u32 scenario, u32 channel);
	int (*iclk_off)(struct device *dev, u32 scenario, u32 channel);
	u32 scenario;
	u32 csi_ch;
	int (*mclk_force_off)(struct device *dev, u32 scenario);
};

struct is_core {
	struct platform_device *pdev;
	struct is_resourcemgr resourcemgr;
};

struct is_device_sensor {
	struct platform_device *pdev;
	struct exynos_platform_is_sensor *pdata;
	void *private_data;
	unsigned long state;
	int device_id;
	int instance;
	struct is_video_ctx *vctx;
	struct v4l2_subdev *subdev_csi;
	struct v4l2_subdev *subdev_module;
	struct is_resourcemgr *resourcemgr;
};

int exynos_is_sensor_iclk_cfg(struct device *dev, u32 scenario, u32 channel);
int exynos_is_sensor_iclk_on(struct device *dev, u32 scenario, u32 channel);
int exynos_is_sensor_iclk_off(struct device *dev, u32 scenario, u32 channel);
static int is_sensor_iclk_on(struct is_device_sensor *device);
int is_sensor_iclk_off(struct is_device_sensor *device);
static int is_sensor_suspend(struct device *dev);
int is_sensor_runtime_suspend(struct device *dev);
int is_sensor_runtime_resume(struct device *dev);
int is_video_close(struct file *file);

static struct is_device_sensor *active_sensor;
static int runtime_suspend_pre_calls;
static int runtime_resume_pre_calls;
static int module_lookup_calls;
static int gpio_off_calls;
static int unregister_calls;
static int vendor_suspend_calls;
static int mclk_force_off_calls;
static int secure_cleanup_calls;
static int qos_cleanup_calls;
static int sensor_close_calls;
static int queue_release_calls;
static int vctx_close_calls;
static int module_pin_calls;
static int video_device_pin_calls;
static int configured_runtime_suspend_pre_ret;
static int configured_runtime_resume_pre_ret;
static int configured_gpio_off_ret;
static int configured_module_lookup_ret;

static const int THIS_MODULE_TOKEN;

static struct platform_device *to_platform_device(struct device *dev);
static int is_sensor_g_device(struct platform_device *pdev,
		struct is_device_sensor **device);
static int is_sensor_runtime_suspend_pre(struct device *dev);
static int is_sensor_runtime_resume_pre(struct device *dev);
static int is_sensor_g_module(struct is_device_sensor *device,
		struct is_module_enum **module);
static int is_sensor_gpio_off(struct is_device_sensor *device);
static int is_secure_func(void *a, struct is_device_sensor *device,
		int b, u32 c, int d);
static int atomic_dec_return(int *value);
static void is_remove_dvfs(struct is_core *core, int level);
static void is_vendor_sensor_suspend(struct platform_device *pdev);
static void v4l2_device_unregister_subdev(struct v4l2_subdev *subdev);
static void __module_get(const void *module);
static struct device *get_device(struct device *dev);
static struct is_video *video_drvdata(struct file *file);
static int is_sensor_close(struct is_device_sensor *device);
static int is_ischain_group_close(struct is_device_ischain *device,
		struct is_video_ctx *ivc, void *group);
static int is_ischain_subdev_close(struct is_device_ischain *device,
		struct is_video_ctx *ivc);
static int is_sensor_subdev_close(struct is_device_sensor *device,
		struct is_video_ctx *ivc);
static int __is_video_close(struct is_video_ctx *ivc);
static int is_vctx_close(struct file *file, struct is_video *video,
		struct is_video_ctx *vctx);

static int fake_mclk_force_off(struct device *dev, u32 scenario)
{
	(void)dev;
	(void)scenario;
	mclk_force_off_calls++;
	return 0;
}

static struct platform_device *to_platform_device(struct device *dev)
{
	(void)dev;
	return active_sensor ? active_sensor->pdev : NULL;
}

static int is_sensor_g_device(struct platform_device *pdev,
		struct is_device_sensor **device)
{
	(void)pdev;
	*device = active_sensor;
	return active_sensor ? 0 : -EINVAL;
}

static int is_sensor_runtime_suspend_pre(struct device *dev)
{
	(void)dev;
	runtime_suspend_pre_calls++;
	return configured_runtime_suspend_pre_ret;
}

static int is_sensor_runtime_resume_pre(struct device *dev)
{
	(void)dev;
	runtime_resume_pre_calls++;
	return configured_runtime_resume_pre_ret;
}

static int is_sensor_g_module(struct is_device_sensor *device,
		struct is_module_enum **module)
{
	(void)device;
	module_lookup_calls++;
	*module = NULL;
	return configured_module_lookup_ret;
}

static int is_sensor_gpio_off(struct is_device_sensor *device)
{
	(void)device;
	gpio_off_calls++;
	return configured_gpio_off_ret;
}

static int is_secure_func(void *a, struct is_device_sensor *device,
		int b, u32 c, int d)
{
	(void)a; (void)device; (void)b; (void)c; (void)d;
	secure_cleanup_calls++;
	return 0;
}

static int atomic_dec_return(int *value)
{
	return --*value;
}

static void is_remove_dvfs(struct is_core *core, int level)
{
	(void)core; (void)level;
	qos_cleanup_calls++;
}

static void is_vendor_sensor_suspend(struct platform_device *pdev)
{
	(void)pdev;
	vendor_suspend_calls++;
}

static void v4l2_device_unregister_subdev(struct v4l2_subdev *subdev)
{
	(void)subdev;
	unregister_calls++;
}

static void __module_get(const void *module)
{
	if (module == &THIS_MODULE_TOKEN)
		module_pin_calls++;
}

static struct device *get_device(struct device *dev)
{
	video_device_pin_calls++;
	return dev;
}

static struct is_video *video_drvdata(struct file *file)
{
	return file->video;
}

static int is_sensor_close(struct is_device_sensor *device)
{
	(void)device;
	sensor_close_calls++;
	return 0;
}

static int is_ischain_group_close(struct is_device_ischain *device,
		struct is_video_ctx *ivc, void *group)
{
	(void)device; (void)ivc; (void)group;
	return 0;
}

static int is_ischain_subdev_close(struct is_device_ischain *device,
		struct is_video_ctx *ivc)
{
	(void)device; (void)ivc;
	return 0;
}

static int is_sensor_subdev_close(struct is_device_sensor *device,
		struct is_video_ctx *ivc)
{
	(void)device; (void)ivc;
	return 0;
}

static int __is_video_close(struct is_video_ctx *ivc)
{
	(void)ivc;
	queue_release_calls++;
	return 0;
}

static int is_vctx_close(struct file *file, struct is_video *video,
		struct is_video_ctx *vctx)
{
	(void)video; (void)vctx;
	vctx_close_calls++;
	file->private_data = NULL;
	return 0;
}

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
	runtime_suspend_pre_calls = 0;
	runtime_resume_pre_calls = 0;
	module_lookup_calls = 0;
	gpio_off_calls = 0;
	unregister_calls = 0;
	vendor_suspend_calls = 0;
	mclk_force_off_calls = 0;
	secure_cleanup_calls = 0;
	qos_cleanup_calls = 0;
	sensor_close_calls = 0;
	queue_release_calls = 0;
	vctx_close_calls = 0;
	module_pin_calls = 0;
	video_device_pin_calls = 0;
	configured_runtime_suspend_pre_ret = 0;
	configured_runtime_resume_pre_ret = 0;
	configured_gpio_off_ret = 0;
	configured_module_lookup_ret = 0;
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
	pdata->mclk_force_off = fake_mclk_force_off;
	core->pdev = pdev;
	sensor->pdev = pdev;
	sensor->pdata = pdata;
	sensor->private_data = core;
	sensor->instance = 0;
	sensor->subdev_csi = (struct v4l2_subdev *)1;
	sensor->subdev_module = (struct v4l2_subdev *)2;
	sensor->resourcemgr = &core->resourcemgr;
	active_sensor = sensor;
}

#define ARRAY_SIZE(array) (sizeof(array) / sizeof((array)[0]))
#define IS_DEVICE_ISCHAIN 1
#define IS_VIDEO_TYPE_LEADER 1
#define USE_OFFLINE_PROCESSING 1
#define SECURE_CAMERA_IRIS 0
#define IS_SECURE_CAMERA_IRIS 1
#define SMC_SECCAM_UNPREPARE 2
#define START_DVFS_LEVEL 0
#define THIS_MODULE (&THIS_MODULE_TOKEN)
#define IS_ENABLED(option) 0
#define FIMC_BUG(condition) do { if (condition) return -EINVAL; } while (0)
#define test_bit(bit, state) fake_test_bit((bit), (state))
#define set_bit(bit, state) fake_set_bit((bit), (state))
#define clear_bit(bit, state) fake_clear_bit((bit), (state))
#define is_enable fake_is_enable
#define is_disable fake_is_disable
#define pr_debug(...) do { } while (0)
#define pr_err(...) fake_pr_err(__VA_ARGS__)
#define merr(...) do { } while (0)
#define mwarn(...) do { } while (0)
#define err(...) do { } while (0)
#define info(...) do { } while (0)
#define minfo(...) do { } while (0)
#define mierr(...) do { } while (0)

/* CAMERA_SENSOR_STATE_ENUM */

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

	reset_clocks(0);
	setup_sensor(&sensor, &pdata, &core, &pdev);
	ret = is_sensor_suspend(&pdev.dev);
	failures += check(ret == 0 && vendor_suspend_calls == 1 &&
		mclk_force_off_calls == 1,
		"baseline system suspend retains its existing vendor/MCLK path");

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
	failures += check(ret == -EUCLEAN, "cfg rollback failure quarantines unknown ownership");
	failures += check(disable_calls == 7 && count_votes() == 9 && clock_votes[3] == 2,
		"cfg cleanup continues after one failed disable and preserves both votes");
	failures += check(strstr(diagnostics, "ownership unknown") != NULL &&
		strstr(diagnostics, "original error -5 (cleanup -22)") != NULL,
		"incomplete cfg rollback reports uncertainty rather than physical success");
	failures += check(fake_test_bit(IS_SENSOR_ICLK_UNKNOWN, &sensor.state) &&
		!fake_test_bit(IS_SENSOR_ICLK_ON, &sensor.state),
		"cfg cleanup failure quarantines while leaving logical ON clear");
	{
		int enables = enable_calls;
		int disables = disable_calls;
		int votes = count_votes();
		ret = is_sensor_iclk_on(&sensor);
		failures += check(ret == -EUCLEAN && enable_calls == enables &&
			disable_calls == disables && count_votes() == votes,
			"cfg quarantine prevents an unsafe repeated acquisition");
	}

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
	{
		int enables = enable_calls;
		int disables = disable_calls;
		int votes = count_votes();
		ret = is_sensor_iclk_on(&sensor);
		failures += check(ret == 0 && enable_calls == enables &&
			disable_calls == disables && count_votes() == votes,
			"repeated on is idempotent and preserves shared votes");
	}
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
	{
		int enables = enable_calls;
		int disables = disable_calls;
		int votes = count_votes();
		ret = is_sensor_iclk_off(&sensor);
		failures += check(ret == 0 && enable_calls == enables &&
			disable_calls == disables && count_votes() == votes,
			"repeated off is idempotent and preserves shared votes");
	}

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
	failures += check(ret == -EUCLEAN, "on cleanup failure quarantines unknown ownership");
	failures += check(count_votes() == 9 && clock_votes[5] == 2,
		"persistently failed cleanup leaves its vote while preserving shared votes");
	failures += check(strstr(diagnostics, "ownership unknown") != NULL &&
		strstr(diagnostics, "original error -22 (cleanup -22)") != NULL,
		"incomplete on cleanup records initiating and cleanup errors separately");
	failures += check(fake_test_bit(IS_SENSOR_ICLK_UNKNOWN, &sensor.state) &&
		!fake_test_bit(IS_SENSOR_ICLK_ON, &sensor.state),
		"on cleanup failure quarantines before logical ON is set");

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
	failures += check(ret == -EUCLEAN, "off restore failure latches unknown ownership");
	failures += check(clock_votes[3] == 1 && clock_votes[7] == 2 && count_votes() == 9,
		"failed restore leaves partial ownership while preserving shared references");
	failures += check(strstr(diagnostics, "ownership unknown") != NULL &&
		strstr(diagnostics, "original DMA error -22") != NULL &&
		strstr(diagnostics, "selected-clock restore error -5") != NULL,
		"failed off restoration reports both initiating and cleanup errors");
	failures += check(fake_test_bit(IS_SENSOR_ICLK_ON, &sensor.state),
		"actual caller retains logical ON state after failed physical unwind");
	failures += check(fake_test_bit(IS_SENSOR_ICLK_UNKNOWN, &sensor.state),
		"actual caller latches ICLK quarantine after incomplete restoration");

	/* A quarantine is terminal for this sensor object: no retry can consume
	 * another caller's shared gate vote or run PM/close teardown past it.
	 */
	{
		int enables = enable_calls;
		int disables = disable_calls;
		int votes = count_votes();
		struct is_video video;
		struct is_device_ischain ischain;
		struct is_video_ctx ivc;
		struct vb2_queue queue;
		struct file file;

		ret = is_sensor_iclk_off(&sensor);
		failures += check(ret == -EUCLEAN && enable_calls == enables &&
			disable_calls == disables && count_votes() == votes,
			"quarantined off retry is refused before clock operations");
		ret = is_sensor_iclk_on(&sensor);
		failures += check(ret == -EUCLEAN && enable_calls == enables &&
			disable_calls == disables && count_votes() == votes,
			"quarantined on retry is refused before clock operations");
		ret = is_sensor_runtime_suspend(&pdev.dev);
		failures += check(ret == -EUCLEAN && runtime_suspend_pre_calls == 0 &&
			module_lookup_calls == 0 && gpio_off_calls == 0 &&
			unregister_calls == 0 && secure_cleanup_calls == 0 &&
			qos_cleanup_calls == 0,
			"runtime suspend refuses before other sensor teardown");
		ret = is_sensor_runtime_resume(&pdev.dev);
		failures += check(ret == -EUCLEAN && runtime_resume_pre_calls == 0 &&
			enable_calls == enables && disable_calls == disables,
			"runtime resume refuses before ICLK reacquisition");
		ret = is_sensor_suspend(&pdev.dev);
		failures += check(ret == -EUCLEAN && vendor_suspend_calls == 0 &&
			mclk_force_off_calls == 0,
			"system suspend refuses before vendor/MCLK side effects");

		memset(&video, 0, sizeof(video));
		memset(&ivc, 0, sizeof(ivc));
		memset(&queue, 0, sizeof(queue));
		memset(&file, 0, sizeof(file));
		video.device_type = 0;
		video.video_type = 2; /* sensor capture node, not the leader node */
		ivc.device = &sensor;
		ivc.video = &video;
		ivc.queue.vbq = &queue;
		file.private_data = &ivc;
		file.video = &video;
		ret = is_video_close(&file);
		failures += check(ret == -EUCLEAN && module_pin_calls == 1 &&
			video_device_pin_calls == 1,
			"outer release pins exactly one module and V4L2-node reference");
		ret = is_video_close(&file);
		failures += check(ret == -EUCLEAN && module_pin_calls == 1 &&
			video_device_pin_calls == 1,
			"duplicate direct release does not accumulate lifetime pins");
		failures += check(sensor_close_calls == 0 && queue_release_calls == 0 &&
			vctx_close_calls == 0 && file.private_data == &ivc &&
			ivc.queue.vbq == &queue && queue.drv_priv == NULL,
			"sensor capture close retains context and queue instead of freeing live state");

		memset(&video, 0, sizeof(video));
		memset(&ischain, 0, sizeof(ischain));
		memset(&ivc, 0, sizeof(ivc));
		memset(&queue, 0, sizeof(queue));
		memset(&file, 0, sizeof(file));
		video.device_type = IS_DEVICE_ISCHAIN;
		video.video_type = 2; /* pipeline capture context tied to the quarantined sensor */
		ischain.sensor = &sensor;
		ivc.device = &ischain;
		ivc.video = &video;
		ivc.queue.vbq = &queue;
		file.private_data = &ivc;
		file.video = &video;
		ret = is_video_close(&file);
		failures += check(ret == -EUCLEAN && module_pin_calls == 2 &&
			video_device_pin_calls == 2,
			"ischain context follows its sensor and pins module plus V4L2 node");
		failures += check(queue_release_calls == 0 && vctx_close_calls == 0 &&
			file.private_data == &ivc && ivc.queue.vbq == &queue,
			"ischain capture context also retains its queue and V4L2 context");
	}

	/* A cleanly restored ordinary lookup failure remains retryable, but PM must
	 * propagate it rather than unregistering the subdevice or reporting success.
	 */
	reset_clocks(1);
	setup_sensor(&sensor, &pdata, &core, &pdev);
	ret = is_sensor_iclk_on(&sensor);
	fail_disable_id = 7;
	fail_disable_remaining = 1;
	ret = is_sensor_runtime_suspend(&pdev.dev);
	failures += check(ret == -EINVAL && fake_test_bit(IS_SENSOR_ICLK_ON, &sensor.state),
		"ordinary restored DMA-off error remains visible and retains ON bit");
	failures += check(unregister_calls == 0,
		"runtime suspend does not unregister after an ICLK-off error");
	failures += check(!fake_test_bit(IS_SENSOR_ICLK_UNKNOWN, &sensor.state),
		"successful selected-clock restoration does not latch quarantine");

	/* Success path still performs ordinary suspend/resume and close operations. */
	reset_clocks(1);
	setup_sensor(&sensor, &pdata, &core, &pdev);
	ret = is_sensor_iclk_on(&sensor);
	ret = is_sensor_runtime_suspend(&pdev.dev);
	failures += check(ret == 0 && unregister_calls == 1 &&
		!fake_test_bit(IS_SENSOR_ICLK_ON, &sensor.state),
		"successful runtime suspend unregisters only after clock release");
	ret = is_sensor_runtime_resume(&pdev.dev);
	failures += check(ret == 0 && fake_test_bit(IS_SENSOR_ICLK_ON, &sensor.state),
		"successful runtime resume reacquires clocks");

	if (failures)
		return 1;
	puts("PASS: extracted clock quarantine lifecycle propagates and retains owned state");
	return 0;
}
#endif
