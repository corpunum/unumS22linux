/*
 * nnapi-conv-test [device-substring] [iters]
 * Our own int8 (QUANT8_ASYMM) model on the Exynos 2200 NPU through Samsung's
 * NNAPI *support library* (vendor neuralnetworks_npu_sl_driver_prebuilt.so),
 * loaded in-process via dlopen; no HAL service, no binder.
 * Model: input [1,32,32,8] u8 -> CONV_2D 3x3x8->16, SAME, stride 1, fused RELU
 * Weights/input are a deterministic LCG; a C integer reference (NNAPI
 * semantics, double-rounded requant) is computed and compared bytewise.
 * Writes /data/local/tmp/nnconv.{in,npu,ref}.bin for host checking.
 * Build: NDK r27c aarch64-linux-android30-clang -O2. Run inside enn-rt chroot.
 */
#include <android/NeuralNetworks.h>
#include <dlfcn.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#define SL "/vendor/lib64/neuralnetworks_npu_sl_driver_prebuilt.so"
static void *h;
#define F(name) static __typeof__(&name) p_##name;
F(ANeuralNetworks_getDeviceCount) F(ANeuralNetworks_getDevice) F(ANeuralNetworksDevice_getName)
F(ANeuralNetworksDevice_getType) F(ANeuralNetworksDevice_getVersion) F(ANeuralNetworksDevice_getFeatureLevel)
F(ANeuralNetworksModel_create) F(ANeuralNetworksModel_addOperand) F(ANeuralNetworksModel_setOperandValue)
F(ANeuralNetworksModel_addOperation) F(ANeuralNetworksModel_identifyInputsAndOutputs) F(ANeuralNetworksModel_finish)
F(ANeuralNetworksModel_getSupportedOperationsForDevices)
F(ANeuralNetworksCompilation_createForDevices) F(ANeuralNetworksCompilation_finish) F(ANeuralNetworksCompilation_setPreference)
F(ANeuralNetworksExecution_create) F(ANeuralNetworksExecution_setInput) F(ANeuralNetworksExecution_setOutput)
F(ANeuralNetworksExecution_compute) F(ANeuralNetworksExecution_free) F(ANeuralNetworksExecution_setMeasureTiming)
F(ANeuralNetworksExecution_getDuration)
#define L(name) do { p_##name = dlsym(h, #name); if (!p_##name) { printf("missing %s\n", #name); _exit(3);} } while (0)

static double now(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t); return t.tv_sec + t.tv_nsec / 1e9; }
static uint32_t lcg = 12345; static uint32_t rnd(void) { lcg = lcg * 1103515245u + 12345u; return lcg >> 8; }
#define CK(x) do { int _r = (x); if (_r) { printf("FAIL %s rc=%d\n", #x, _r); fflush(stdout); _exit(4);} } while (0)

static int N = 1, H = 32, W = 32, CI = 8, CO = 16, K = 3;
static int envi(const char *n, int d) { const char *v = getenv(n); return v ? atoi(v) : d; }
static const float IS = 0.02f, WS = 0.01f, OS = 0.05f; static const int IZ = 128, WZ = 128, OZ = 0;

int main(int argc, char **argv)
{
	const char *want = argc > 1 ? argv[1] : "";
	int iters = argc > 2 ? atoi(argv[2]) : 100;
	double t0 = now();
	{ /* route Android liblog to stderr (no logd in the chroot) */
		void *lg = dlopen("liblog.so", RTLD_NOW);
		void (*setl)(void *) = lg ? dlsym(lg, "__android_log_set_logger") : NULL;
		void *errl = lg ? dlsym(lg, "__android_log_stderr_logger") : NULL;
		int (*setp)(int) = lg ? dlsym(lg, "__android_log_set_minimum_priority") : NULL;
		if (setl && errl && getenv("NN_LOG")) { setl(errl); if (setp) setp(atoi(getenv("NN_LOG"))); }
	}
	h = dlopen(SL, RTLD_NOW | RTLD_LOCAL);
	if (!h) { printf("dlopen: %s\n", dlerror()); return 2; }
	printf("stage=dlopen t=%.3f\n", now() - t0);
	L(ANeuralNetworks_getDeviceCount); L(ANeuralNetworks_getDevice); L(ANeuralNetworksDevice_getName);
	L(ANeuralNetworksDevice_getType); L(ANeuralNetworksDevice_getVersion); L(ANeuralNetworksDevice_getFeatureLevel);
	L(ANeuralNetworksModel_create); L(ANeuralNetworksModel_addOperand); L(ANeuralNetworksModel_setOperandValue);
	L(ANeuralNetworksModel_addOperation); L(ANeuralNetworksModel_identifyInputsAndOutputs); L(ANeuralNetworksModel_finish);
	L(ANeuralNetworksModel_getSupportedOperationsForDevices);
	L(ANeuralNetworksCompilation_createForDevices); L(ANeuralNetworksCompilation_finish); L(ANeuralNetworksCompilation_setPreference);
	L(ANeuralNetworksExecution_create); L(ANeuralNetworksExecution_setInput); L(ANeuralNetworksExecution_setOutput);
	L(ANeuralNetworksExecution_compute); L(ANeuralNetworksExecution_free); L(ANeuralNetworksExecution_setMeasureTiming);
	L(ANeuralNetworksExecution_getDuration);

	uint32_t nd = 0; CK(p_ANeuralNetworks_getDeviceCount(&nd));
	ANeuralNetworksDevice *dev = NULL;
	for (uint32_t i = 0; i < nd; i++) {
		ANeuralNetworksDevice *d; const char *nm = "?", *ver = "?"; int32_t ty = 0; int64_t fl = 0;
		p_ANeuralNetworks_getDevice(i, &d); p_ANeuralNetworksDevice_getName(d, &nm);
		p_ANeuralNetworksDevice_getType(d, &ty); p_ANeuralNetworksDevice_getVersion(d, &ver);
		p_ANeuralNetworksDevice_getFeatureLevel(d, &fl);
		printf("device[%u] name=%s type=%d version=%s feature_level=%lld\n", i, nm, ty, ver, (long long)fl);
		if (!dev && strstr(nm, want)) dev = d;
	}
	if (!dev) { printf("no device matching '%s'\n", want); return 5; }
	fflush(stdout);

	H = envi("NN_H", H); W = envi("NN_W", W); CI = envi("NN_CI", CI); CO = envi("NN_CO", CO); K = envi("NN_K", K);
	printf("shape in=[1,%d,%d,%d] k=%dx%d out_ch=%d\n", H, W, CI, K, K, CO);
	size_t nin = (size_t)N * H * W * CI, nwt = (size_t)CO * K * K * CI, nout = (size_t)N * H * W * CO;
	uint8_t *in = malloc(nin), *wt = malloc(nwt), *out = malloc(nout), *ref = malloc(nout);
	int32_t *bias = malloc(CO * 4);
	for (size_t i = 0; i < nin; i++) in[i] = rnd() & 0xff;
	for (size_t i = 0; i < nwt; i++) wt[i] = rnd() & 0xff;
	for (int i = 0; i < CO; i++) bias[i] = (int32_t)(rnd() % 2001) - 1000; /* bias scale = IS*WS */

	/* NN_SIGNED=1: same math in QUANT8_ASYMM_SIGNED (TFLite int8 style): bytes ^ 0x80, zero points - 128 */
	int sgn = getenv("NN_SIGNED") != NULL;
	int qt = sgn ? ANEURALNETWORKS_TENSOR_QUANT8_ASYMM_SIGNED : ANEURALNETWORKS_TENSOR_QUANT8_ASYMM;
	int zo = sgn ? -128 : 0;
	uint8_t *in_dev = malloc(nin), *wt_dev = malloc(nwt);
	for (size_t i = 0; i < nin; i++) in_dev[i] = sgn ? in[i] ^ 0x80 : in[i];
	for (size_t i = 0; i < nwt; i++) wt_dev[i] = sgn ? wt[i] ^ 0x80 : wt[i];
	ANeuralNetworksModel *m; CK(p_ANeuralNetworksModel_create(&m));
	uint32_t dIn[4] = {N, H, W, CI}, dW[4] = {CO, K, K, CI}, dB[1] = {CO}, dOut[4] = {N, H, W, CO};
	ANeuralNetworksOperandType tIn = {qt, 4, dIn, IS, IZ + zo};
	ANeuralNetworksOperandType tW = {qt, 4, dW, WS, WZ + zo};
	ANeuralNetworksOperandType tB = {ANEURALNETWORKS_TENSOR_INT32, 1, dB, IS * WS, 0};
	ANeuralNetworksOperandType tOut = {qt, 4, dOut, OS, OZ + zo};
	ANeuralNetworksOperandType tI32 = {ANEURALNETWORKS_INT32, 0, NULL, 0, 0};
	CK(p_ANeuralNetworksModel_addOperand(m, &tIn));   /* 0 */
	CK(p_ANeuralNetworksModel_addOperand(m, &tW));    /* 1 */
	CK(p_ANeuralNetworksModel_addOperand(m, &tB));    /* 2 */
	for (int i = 0; i < 4; i++) CK(p_ANeuralNetworksModel_addOperand(m, &tI32)); /* 3 pad,4 sw,5 sh,6 act */
	CK(p_ANeuralNetworksModel_addOperand(m, &tOut));  /* 7 */
	int32_t padSame = ANEURALNETWORKS_PADDING_SAME, one = 1, relu = ANEURALNETWORKS_FUSED_RELU;
	CK(p_ANeuralNetworksModel_setOperandValue(m, 1, wt_dev, nwt));
	CK(p_ANeuralNetworksModel_setOperandValue(m, 2, bias, CO * 4));
	CK(p_ANeuralNetworksModel_setOperandValue(m, 3, &padSame, 4));
	CK(p_ANeuralNetworksModel_setOperandValue(m, 4, &one, 4));
	CK(p_ANeuralNetworksModel_setOperandValue(m, 5, &one, 4));
	CK(p_ANeuralNetworksModel_setOperandValue(m, 6, &relu, 4));
	uint32_t opIn[7] = {0, 1, 2, 3, 4, 5, 6}, opOut[1] = {7}, mIn[1] = {0}, mOut[1] = {7};
	CK(p_ANeuralNetworksModel_addOperation(m, ANEURALNETWORKS_CONV_2D, 7, opIn, 1, opOut));
	/* NN_PRELU=n (probe only, use with NN_COMPILE_ONLY): append PRELU with an alpha
	 * tensor of n elements (1 = scalar-like, CO = per-channel) after the conv. */
	int np_ = envi("NN_PRELU", 0), nops = 1;
	if (np_) {
		static uint32_t dA[4]; dA[0] = dA[1] = dA[2] = 1; dA[3] = np_;
		ANeuralNetworksOperandType tA = {qt, 4, dA, 0.01f, zo + 128 - 128};
		CK(p_ANeuralNetworksModel_addOperand(m, &tA));   /* 8 alpha */
		CK(p_ANeuralNetworksModel_addOperand(m, &tOut)); /* 9 out */
		uint8_t *al = malloc(np_); memset(al, sgn ? 20 : 20, np_);
		CK(p_ANeuralNetworksModel_setOperandValue(m, 8, al, np_));
		uint32_t pi[2] = {7, 8}, po[1] = {9};
		CK(p_ANeuralNetworksModel_addOperation(m, ANEURALNETWORKS_PRELU, 2, pi, 1, po));
		mOut[0] = 9; nops = 2;
	}
	CK(p_ANeuralNetworksModel_identifyInputsAndOutputs(m, 1, mIn, 1, mOut));
	CK(p_ANeuralNetworksModel_finish(m));
	bool sup[2] = {0, 0};
	CK(p_ANeuralNetworksModel_getSupportedOperationsForDevices(m, (const ANeuralNetworksDevice *const *)&dev, 1, sup));
	printf("stage=model_finish supported_on_device=%d", sup[0]); if (nops > 1) printf(",%d", sup[1]);
	printf(" t=%.3f\n", now() - t0); fflush(stdout);

	ANeuralNetworksCompilation *c;
	double tc = now();
	CK(p_ANeuralNetworksCompilation_createForDevices(m, (const ANeuralNetworksDevice *const *)&dev, 1, &c));
	p_ANeuralNetworksCompilation_setPreference(c, ANEURALNETWORKS_PREFER_SUSTAINED_SPEED);
	CK(p_ANeuralNetworksCompilation_finish(c));
	printf("stage=compile ms=%.1f\n", (now() - tc) * 1000); fflush(stdout);

	/* CPU integer reference (NNAPI semantics) */
	double tr = now();
	double mult = (double)IS * WS / OS;
	for (int y = 0; y < H; y++) for (int x = 0; x < W; x++) for (int o = 0; o < CO; o++) {
		int64_t acc = bias[o];
		for (int ky = 0; ky < K; ky++) for (int kx = 0; kx < K; kx++) {
			int iy = y + ky - (K - 1) / 2, ix = x + kx - (K - 1) / 2;
			if (iy < 0 || iy >= H || ix < 0 || ix >= W) continue;
			const uint8_t *ip = &in[(iy * W + ix) * CI], *wp = &wt[((o * K + ky) * K + kx) * CI];
			for (int ci = 0; ci < CI; ci++) acc += (int32_t)(ip[ci] - IZ) * (int32_t)(wp[ci] - WZ);
		}
		long v = lround(acc * mult) + OZ; if (v < OZ) v = OZ; if (v > 255) v = 255;
		ref[(y * W + x) * CO + o] = (uint8_t)v;
	}
	double trm = (now() - tr) * 1000;
	{ FILE *f;
	if ((f = fopen("/data/local/tmp/nnconv.in.bin", "wb"))) { fwrite(in_dev, 1, nin, f); fclose(f); }
	if ((f = fopen("/data/local/tmp/nnconv.ref.bin", "wb"))) { fwrite(ref, 1, nout, f); fclose(f); } }
	if (getenv("NN_COMPILE_ONLY")) { printf("compile-only: done\n"); fflush(stdout); _exit(0); }
	double *lat = calloc(iters, sizeof *lat); uint64_t hwns = 0;
	for (int it = 0; it < iters; it++) {
		ANeuralNetworksExecution *e; CK(p_ANeuralNetworksExecution_create(c, &e));
		if (it == iters - 1) p_ANeuralNetworksExecution_setMeasureTiming(e, true);
		CK(p_ANeuralNetworksExecution_setInput(e, 0, NULL, in_dev, nin));
		CK(p_ANeuralNetworksExecution_setOutput(e, 0, NULL, out, nout));
		double ts = now();
		CK(p_ANeuralNetworksExecution_compute(e));
		lat[it] = (now() - ts) * 1000;
		if (it == iters - 1) p_ANeuralNetworksExecution_getDuration(e, ANEURALNETWORKS_DURATION_ON_HARDWARE, &hwns);
		p_ANeuralNetworksExecution_free(e);
	}
	double s = 0, mn = 1e9; for (int i = 0; i < iters; i++) { s += lat[i]; if (lat[i] < mn) mn = lat[i]; }
	printf("stage=execute n=%d first_ms=%.3f mean_ms=%.3f min_ms=%.3f hw_ns=%llu\n", iters, lat[0], s / iters, mn,
	       (unsigned long long)hwns);

	int exact = 0, off1 = 0, worse = 0, maxd = 0;
	for (size_t i = 0; i < nout; i++) {
		int d = abs((int)out[i] - (int)ref[i]); if (d > maxd) maxd = d;
		if (!d) exact++; else if (d == 1) off1++; else worse++;
	}
	printf("compare n=%zu exact=%d off_by_1=%d worse=%d max_abs_diff=%d cpu_ref_ms=%.3f\n", nout, exact, off1, worse, maxd, trm);
	FILE *f;
	if ((f = fopen("/data/local/tmp/nnconv.npu.bin", "wb"))) { fwrite(out, 1, nout, f); fclose(f); }
	fflush(stdout);
	_exit(worse ? 7 : 0);
}
