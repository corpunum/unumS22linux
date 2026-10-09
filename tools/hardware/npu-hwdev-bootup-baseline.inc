/* Verbatim host fixture from pinned 4e5c5ad7 drivers/vision/npu/core/npu-hw-device.c. */
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
