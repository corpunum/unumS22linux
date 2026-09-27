/*
 * SPDX-License-Identifier: GPL-2.0
 * Verbatim function bodies from the Samsung NPU driver at pinned source
 * commit 4e5c5ad7d950e4de0688b5663965f2075654b2ad. These excerpts are
 * retained as GPL-2.0 source fixtures for a host-only callback regression.
 * They are not a kernel patch and are compiled only against explicit shims.
 */

/* drivers/vision/npu/core/npu-hw-device.h: npu_hw_ref_get */
static inline int npu_hw_ref_get(
		struct npu_device *device, struct npu_hw_refcount *hw_ref)
{
	if (!hw_ref->first)
		return 0;

	return (atomic_inc_return(&hw_ref->refcount) == 1) ?
		hw_ref->first(device, hw_ref->hdev) : 0;
}

/* drivers/vision/npu/core/npu-hw-device.h: npu_hw_ref_put */
static inline int npu_hw_ref_put(
		struct npu_device *device, struct npu_hw_refcount *hw_ref)
{
	if (!hw_ref->final)
		return 0;

	return (atomic_dec_return(&hw_ref->refcount) == 0) ?
		hw_ref->final(device, hw_ref->hdev) : 0;
}

/* drivers/vision/npu/core/npu-hw-device.h: npu_hw_ref_init */
static inline int npu_hw_ref_init(
		struct npu_device *device, struct npu_hw_device *hdev)
{
	int ret = 0;
	struct npu_hw_device *phdev;

	if (hdev->parent) {
		phdev = npu_get_hdev(hdev->parent);
		if (!phdev) {
			npu_err("no hw-device as %s\n", hdev->parent);
			ret = -ENODEV;
			goto error_hwref;
		}
		npu_hw_ref_get(device, &phdev->init_cnt);
	}

	if (hdev->ops.init)
		ret = (hdev->ops.init(hdev, true));
error_hwref:
	return ret;
}

/* drivers/vision/npu/core/npu-hw-device.h: npu_hw_ref_deinit */
static inline int npu_hw_ref_deinit(
		struct npu_device *device, struct npu_hw_device *hdev)
{
	int ret = 0;
	struct npu_hw_device *phdev;

	if (hdev->ops.init)
		ret = (hdev->ops.init(hdev, false));

	if (ret)
		goto error_hwref;

	if (hdev->parent) {
		phdev = npu_get_hdev(hdev->parent);
		if (!phdev) {
			npu_err("no hw-device as %s\n", hdev->parent);
			ret = -ENODEV;
			goto error_hwref;
		}
		npu_hw_ref_put(device, &phdev->init_cnt);
	}
error_hwref:
	return ret;
}

/* drivers/vision/npu/core/npu-hw-device.c: npu_hwdev_npu_init */
static int npu_hwdev_npu_init(struct npu_hw_device *hdev, bool on)
{
	int ret = 0;

	if (on) {
	} else {
		ret = npu_stm_disable(&hdev->device->system, hdev->id);
		if (ret)
			npu_err("fail(%d) in npu_stm_disable(%u)\n", ret, hdev->id);
	}

	npu_info("%s %s (%d)\n", __func__, on ? "on" : "off", ret);
	return ret;
}

/* drivers/vision/npu/core/npu-hw-device.c: npu_hwdev_dnc_init */
static int npu_hwdev_dnc_init(struct npu_hw_device *hdev, bool on)
{
	int ret = 0;

	if (on) {
	} else {
		ret = npu_stm_disable(&hdev->device->system, hdev->id);
		if (ret)
			npu_err("fail(%d) in npu_stm_disable(%u)\n", ret, hdev->id);
	}

	npu_info("%s %s (%d)\n", __func__, on ? "on" : "off", ret);
	return ret;
}

/* drivers/vision/npu/core/npu-hw-device.c: npu_hwdev_dsp_init */
static int npu_hwdev_dsp_init(struct npu_hw_device *hdev, bool on)
{
	int ret = 0;
	struct npu_system *system;

	system = &hdev->device->system;

	/* kernel loading for init */
	if (on) {
#ifdef CONFIG_DSP_USE_VS4L
		ret = dsp_system_load_binary(system);
		if (ret) {
			probe_err("fail(%d) in dsp_system_load_binary\n", ret);
			goto err_kernel;
		}

		ret = dsp_kernel_manager_open(system, &hdev->device->kmgr);
		if (ret) {
			npu_err("fail(%d) in dsp_kernel_manager_open\n", ret);
			goto err_kernel;
		}
#endif
	}
	else {
#ifdef CONFIG_DSP_USE_VS4L
		dsp_kernel_manager_close(&hdev->device->kmgr, 1);

		ret = npu_stm_disable(&hdev->device->system, hdev->id);
		if (ret)
			npu_err("fail(%d) in npu_stm_disable(%u)\n", ret, hdev->id);
#endif
	}

	npu_info("%s %s (%d)\n", __func__, on ? "on" : "off", ret);
	return ret;
#ifdef CONFIG_DSP_USE_VS4L
err_kernel:
#endif
	return ret;
}

/* drivers/vision/npu/core/npu-stm.c: __enable_npu_stm_sfr */
static int __enable_npu_stm_sfr(struct npu_system *system, u32 stm_sel, int hid)
{
	int ret = 0;

	npu_info("hid : %d\n", hid);

	if (!npu_stm_data.enable_cnt) {
		npu_stm_data.enable_cnt++;
		if (stm_sel != 0xCAFE)
			npu_stm_set_stm_sel(system, stm_sel);
	}

#ifdef CONFIG_NPU_USE_BOOT_IOCTL
	if (hid & NPU_HWDEV_ID_DNC) {
		npu_info("enable stm for dnc\n");
	} else if (hid & NPU_HWDEV_ID_DSP) {
		npu_info("enable stm for dsp\n");
		ret = npu_cmd_map(system, "enstmdsp");
	} else if (hid & NPU_HWDEV_ID_NPU) {
		npu_info("enable stm for npu\n");
		ret = npu_cmd_map(system, "enstmnpu");
	}
#else
	/* STM_ENABLE(NPUS) */
	ret = npu_cmd_map(system, "enablestm");
#endif
	if (ret) {
		npu_err("Failed to write registers on enable_npu_stm_regs array (%d)\n", ret);
		goto err_exit;
	}

	npu_info("STM enabled(%d)\n", hid);
err_exit:
	return ret;
}

/* drivers/vision/npu/core/npu-stm.c: __disable_npu_stm_sfr */
static int __disable_npu_stm_sfr(struct npu_system *system, int hid)
{
	int ret = 0;

	npu_info("hid : %d\n", hid);

#ifdef CONFIG_NPU_USE_BOOT_IOCTL
	if (hid & NPU_HWDEV_ID_DNC) {
		npu_info("disable stm for dnc\n");
		ret = npu_cmd_map(system, "disstmdnc");
	} else if (hid & NPU_HWDEV_ID_DSP) {
		npu_info("disable stm for dsp\n");
	//	ret = npu_cmd_map(system, "disstmdsp");
	} else if (hid & NPU_HWDEV_ID_NPU) {
		npu_info("disable stm for npu\n");
		ret = npu_cmd_map(system, "disstmnpu");
	}
#else
	/* STM_ENABLE(NPUS) */
	ret = npu_cmd_map(system, "disablestm");
#endif
	if (ret) {
		npu_err("Failed to write registers on disable_npu_stm_regs array (%d)\n", ret);
		goto err_exit;
	}

	npu_info("STM disabled(%d)\n", hid);
err_exit:
	return ret;
}

/* drivers/vision/npu/core/npu-stm.c: npu_stm_enable */
int npu_stm_enable(struct npu_system *system, int hid)
{
	int ret = 0;

	BUG_ON(!system);

	if (atomic_read(&npu_stm_data.enabled) != NPU_STM_ENABLED) {
		/* No need to be enabled */
		npu_info("Now, stm disabled\n");
		return ret;
	}

	if (npu_stm_data.enable_cnt)
		goto enable_more;

	/* Adjust BUSP divider if guaranteed STM is activated */
	if (atomic_read(&npu_stm_data.guarantee)) {
		ret = __busp_div_1to1(system, NPU_STM_NEED_BUSP_DIV);
		if (ret) {
			npu_err("__busp_div_1to1 failed : %d\n", ret);
			goto err_exit;
		}
		atomic_set(&npu_stm_data.guarantee_working, 1);
	}

	/* Send sync signal */
	ret = __npu_stm_sync(system);
	if (ret) {
		npu_err("__stm_sync failed : %d\n", ret);
		goto err_exit;
	}

	/* Enable STM with trace set defined in SYSFS */
	npu_info("STM enabled with stm_trace_set 0x%08x\n", get_stm_trace_set());

enable_more:
	ret = npu_enable_stm_sfr(system, hid);
	if (ret) {
		npu_err("npu_enable_stm_sfr failed : %d\n", ret);
		goto err_exit;
	}

	npu_update_stm_ts_stat();

	npu_stm_data.enable_cnt++;

	npu_info("complete %s, cnt (%d)\n", __func__, npu_stm_data.enable_cnt);

err_exit:
	return ret;
}

/* drivers/vision/npu/core/npu-stm.c: npu_stm_disable */
int npu_stm_disable(struct npu_system *system, int hid)
{
	int ret = 0;

	BUG_ON(!system);

	if (system->fw_load_success != NPU_FW_LOAD_SUCCESS) {
		npu_err("FW was not load(%u)\n", system->fw_load_success);
		goto err_exit;
	}

	npu_stm_data.enable_cnt--;
	/*
	 * Disabling will not check npu_stm->enabled flag,
	 * to make sure the STM disabled before shutdown.
	 * But please keep in mind that the npu_stm_disable() can be
	 * more than one time when stm_unlink() is called while the
	 * NPU is turned on.
	 */
	ret = __disable_npu_stm_sfr(system, hid);
	if (ret) {
		npu_err("__disable_npu_stm_sfr failed : %d\n", ret);
		goto err_exit;
	}

	if (npu_stm_data.enable_cnt)
		goto skip_gaurantee;

	/* Restores BUSP divider if guarantee flag was asserted */
	if (atomic_cmpxchg(&npu_stm_data.guarantee_working, 1, 0) == 1) {
		ret = __busp_div_1to4(system, NPU_STM_NEED_BUSP_DIV);
		if (ret) {
			npu_err("__busp_div_1to4 failed : %d\n", ret);
			goto err_exit;
		}
	}

skip_gaurantee:
	npu_info("complete %s, cnt(%d) \n", __func__, npu_stm_data.enable_cnt);
err_exit:
	return ret;
}

/* drivers/vision/npu/core/dsp-kernel.c: dsp_kernel_manager_close */
void dsp_kernel_manager_close(struct dsp_kernel_manager *kmgr,
		unsigned int count)
{
	dsp_enter();
	mutex_lock(&kmgr->lock);
	if (!kmgr->dl_init) {
		dsp_warn("dl was not initilized");
		mutex_unlock(&kmgr->lock);
		return;
	}

	if (kmgr->dl_init > count) {
		kmgr->dl_init -= count;
		mutex_unlock(&kmgr->lock);
		return;
	}

	if (kmgr->dl_init < count)
		dsp_warn("dl_init is unstable(%u/%u)", kmgr->dl_init, count);

	__dsp_kernel_manager_dl_deinit(kmgr);
	kmgr->dl_init = 0;
	mutex_unlock(&kmgr->lock);
	dsp_leave();
}

/* drivers/vision/npu/core/npu-hw-device.c: npu_hwdev_bootup */
int npu_hwdev_bootup(struct npu_device *device, __u32 hids)
{
	int ret = 0;
	int hi;
	struct npu_hw_device *hdev;

	BUG_ON(!device);

	/* hw-device-wide bootup : for each network */
	for (hi = 0; hi < g_hwdev_num; hi++) {
		hdev = g_hwdev_list[hi];
		if (hdev && (hids & hdev->id)) {
			npu_hw_ref_get(device, &hdev->boot_cnt);
			npu_info("boot %s (%d)\n", hdev->name, hdev->id);
		}
	}

#ifdef CONFIG_NPU_SECURE_MODE
	if (!device->is_secure)
#endif
	{
		for (hi = 0; hi < g_hwdev_num; hi++) {
			hdev = g_hwdev_list[hi];
			if (hdev && (hids & hdev->id)) {
				npu_hw_ref_get(device, &hdev->init_cnt);
				npu_info("init %s (%d)\n", hdev->name, hdev->id);
			}
		}
	}

	npu_info("%s (%d)\n", __func__, ret);
	return ret;
}
