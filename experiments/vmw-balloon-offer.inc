/* Temporary lab offer. Expiry restores the normal host-directed decision. */
static unsigned long cram_offer_until;
static atomic_long_t cram_notneeded = ATOMIC_LONG_INIT(0);
static atomic_long_t cram_refusals = ATOMIC_LONG_INIT(0);

static int cram_offer_set(const char *value, const struct kernel_param *kp)
{
	unsigned int enable;
	int ret = kstrtouint(value, 0, &enable);

	if (ret || enable > 1)
		return -EINVAL;
	WRITE_ONCE(cram_offer_until, enable ? jiffies + 60 * HZ : 0);
	return 0;
}

static const struct kernel_param_ops cram_offer_ops = {
	.set = cram_offer_set,
};
module_param_cb(cram_offer_32m, &cram_offer_ops, NULL, 0200);
