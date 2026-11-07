/* SPDX-License-Identifier: GPL-2.0-only */
/* Lab-only, one-page synchronous zero/release. Caller owns a balloon page. */
int cxl_region_cram_lab_trim(struct cxl_region *cxlr, unsigned long pfn)
{
#ifdef CONFIG_X86
	struct cxl_region_params *p = &cxlr->params;
	struct cxl_endpoint_decoder *target;
	struct cxl_memdev *memdev;
	struct pci_dev *pdev;
	u64 address, offset, dpa;
	int rc;
	struct {
		u8 class, subclass;
		__le16 reserved;
		__le32 count;
		__le64 dpa, length;
	} __packed input = { .class = 1, .subclass = 1,
		.count = cpu_to_le32(1), .length = cpu_to_le64(PAGE_SIZE) };
	struct cxl_mbox_cmd cmd = { .opcode = 0x4402,
		.size_in = sizeof(input), .payload_in = &input };

	if (!IS_ENABLED(CONFIG_CXL_REGION_INVALIDATION_TEST) ||
	    !dmi_match(DMI_SYS_VENDOR, "QEMU") || PAGE_SIZE != 4096 ||
	    pfn > (U64_MAX >> PAGE_SHIFT))
		return -EOPNOTSUPP;
	address = (u64)pfn << PAGE_SHIFT;
	guard(rwsem_read)(&cxl_rwsem.region);
	guard(rwsem_read)(&cxl_rwsem.dpa);
	if (p->state != CXL_CONFIG_COMMIT || !p->res ||
	    p->interleave_ways != 1 || p->nr_targets != 1 || p->cache_size ||
	    test_bit(CXL_REGION_F_NORMALIZED_ADDRESSING, &cxlr->flags))
		return -EOPNOTSUPP;
	if (address < p->res->start || address > p->res->end ||
	    PAGE_SIZE - 1 > p->res->end - address)
		return -ERANGE;
	target = p->targets[0];
	if (!target || !target->dpa_res)
		return -ENODEV;
	offset = address - p->res->start;
	if (offset >= resource_size(target->dpa_res) ||
	    PAGE_SIZE > resource_size(target->dpa_res) - offset)
		return -ERANGE;
	dpa = target->dpa_res->start + offset;
	if (dpa < target->dpa_res->start || !IS_ALIGNED(dpa, PAGE_SIZE))
		return -ERANGE;
	memdev = cxled_to_memdev(target);
	if (!memdev->cxlds || !dev_is_pci(memdev->cxlds->dev))
		return -ENODEV;
	pdev = to_pci_dev(memdev->cxlds->dev);
	/* QEMU's current emulated type-3 identity, plus the DMI gate above. */
	if (pdev->vendor != PCI_VENDOR_ID_INTEL || pdev->device != 0x0d93)
		return -EOPNOTSUPP;
	input.dpa = cpu_to_le64(dpa);
	/* Evict stale CPU copies before asking the device to discard backing. */
	clflush_cache_range(page_address(pfn_to_page(pfn)), PAGE_SIZE);
	rc = cxl_internal_send_cmd(&memdev->cxlds->cxl_mbox, &cmd);
	if (rc)
		return rc;
	/* The generic helper also accepts background completion; we cannot. */
	return cmd.return_code == CXL_MBOX_CMD_RC_SUCCESS ? 0 : -EBUSY;
#else
	return -EOPNOTSUPP;
#endif
}
EXPORT_SYMBOL_NS_GPL(cxl_region_cram_lab_trim, "CXL");
