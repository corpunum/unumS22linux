/* SPDX-License-Identifier: GPL-2.0 */
/*
 * Extracted verbatim from include/linux/pm_runtime.h in reviewed source
 * 4e5c5ad7d950e4de0688b5663965f2075654b2ad, lines 409-420.
 */
static inline int pm_runtime_resume_and_get(struct device *dev)
{
	int ret;

	ret = __pm_runtime_resume(dev, RPM_GET_PUT);
	if (ret < 0) {
		pm_runtime_put_noidle(dev);
		return ret;
	}

	return 0;
}
