/*
 * enn-run MODEL.nnc [iterations] [in0.bin [in1.bin ...]]
 *
 * Run a vendor ENN (.nnc) model on the Exynos 2200 NPU through the public ENN
 * C++ API (in-process engine, libenn_public_api_cpp_lib.so), via dlsym.
 * - Prints every buffer's shape/label (EnnGetBufferInfoByIndex).
 * - Inputs: loaded from the given files (size must match, else padded/truncated
 *   with a warning); without files every input byte is 0x80 (deterministic).
 * - ENN_OUT=/path/prefix dumps output i to prefix.out<i>.bin after the last run.
 * - Prints per-iteration latency and an output checksum.
 * Build: NDK r27c, aarch64-linux-android30-clang -O2 (bionic). Run inside the
 * isolated vendor runtime (see tools/hardware/npu/enn-chroot-run) after the
 * NPU firmware AIE.bin is staged in PID 1's /vendor/firmware.
 */
#include <dlfcn.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

typedef struct { void *va; uint32_t size; uint32_t offset; } EnnBuffer;
typedef struct { uint32_t n_in_buf; uint32_t n_out_buf; } NumberOfBuffersInfo;
/* ENN SDK enn_api-type.h layout (bool first, then u32 n,w,h,c,size,type, label ptr). */
typedef struct {
	_Bool is_able_to_update; uint32_t n, width, height, channel, size, buffer_type;
	const char *label; uint8_t pad[64];
} EnnBufferInfo;
typedef int (*init_fn)(void);
typedef int (*open_fn)(const char *, uint64_t *);
typedef int (*alloc_fn)(uint64_t, EnnBuffer ***, NumberOfBuffersInfo *, int, int);
typedef int (*exec_fn)(uint64_t, int);
typedef int (*release_fn)(EnnBuffer **, int);
typedef int (*close_fn)(uint64_t);
typedef int (*binfo_fn)(EnnBufferInfo *, uint64_t, int, uint32_t);

static double now(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t); return t.tv_sec + t.tv_nsec / 1e9; }
static void *sym(void *h, const char *n, int required) {
	void *p = dlsym(h, n);
	if (!p && required) { printf("stage=dlsym missing=%s\n", n); fflush(stdout); _exit(3); }
	return p;
}
static int cmpd(const void *a, const void *b) { double x = *(const double *)a, y = *(const double *)b; return x < y ? -1 : x > y; }

int main(int argc, char **argv)
{
	if (argc < 2) { fprintf(stderr, "usage: %s MODEL.nnc [iterations] [inputs...]\n", argv[0]); return 2; }
	int iters = argc > 2 ? atoi(argv[2]) : 5;
	if (iters < 1) iters = 1;
	void *h = dlopen("/vendor/lib64/libenn_public_api_cpp_lib.so", RTLD_NOW | RTLD_LOCAL);
	if (!h) { printf("stage=dlopen error=%s\n", dlerror()); return 2; }
	init_fn init = (init_fn)sym(h, "_ZN3enn3api13EnnInitializeEv", 1);
	open_fn open_model = (open_fn)sym(h, "_ZN3enn3api12EnnOpenModelEPKcPm", 1);
	alloc_fn alloc = (alloc_fn)sym(h, "_ZN3enn3api21EnnAllocateAllBuffersEmPPP10_ennBufferP20_NumberOfBuffersInfoib", 1);
	exec_fn exec = (exec_fn)sym(h, "_ZN3enn3api15EnnExecuteModelEmi", 1);
	release_fn release = (release_fn)sym(h, "_ZN3enn3api17EnnReleaseBuffersEPP10_ennBufferi", 1);
	close_fn close_model = (close_fn)sym(h, "_ZN3enn3api13EnnCloseModelEm", 1);
	init_fn deinit = (init_fn)sym(h, "_ZN3enn3api15EnnDeinitializeEv", 1);
	binfo_fn binfo = (binfo_fn)sym(h, "_ZN3enn3api23EnnGetBufferInfoByIndexEP14_ennBufferInfoy14_enn_buf_dir_ej", 0);
	if (!binfo) binfo = (binfo_fn)sym(h, "_ZN3enn3api23EnnGetBufferInfoByIndexEP14_ennBufferInfom14_enn_buf_dir_ej", 0);

	double t0 = now();
	int r = init();
	printf("stage=initialize rc=%d t=%.3f\n", r, now() - t0); fflush(stdout);
	if (r) _exit(4);
	uint64_t model = 0;
	r = open_model(argv[1], &model);
	printf("stage=open_model rc=%d t=%.3f\n", r, now() - t0); fflush(stdout);
	if (r) _exit(5);
	EnnBuffer **bufs = NULL;
	NumberOfBuffersInfo info = {0, 0};
	r = alloc(model, &bufs, &info, 0, 1);
	printf("stage=allocate rc=%d inputs=%u outputs=%u t=%.3f\n", r, info.n_in_buf, info.n_out_buf, now() - t0); fflush(stdout);
	if (r || !bufs) _exit(6);
	unsigned n = info.n_in_buf + info.n_out_buf;
	for (unsigned i = 0; i < n; i++) {
		int dir = i < info.n_in_buf ? 0 : 1;
		unsigned idx = dir ? i - info.n_in_buf : i;
		printf("buffer[%u] %s%u size=%u", i, dir ? "out" : "in", idx, bufs[i] ? bufs[i]->size : 0);
		EnnBufferInfo bi; memset(&bi, 0, sizeof bi);
		if (binfo && binfo(&bi, model, dir, idx) == 0)
			printf(" nwhc=%ux%ux%ux%u bytes=%u type=%u label=%s", bi.n, bi.width, bi.height, bi.channel,
			       bi.size, bi.buffer_type, bi.label ? bi.label : "-");
		printf("\n");
	}
	for (unsigned i = 0; i < info.n_in_buf; i++) {
		if (!bufs[i] || !bufs[i]->va) continue;
		if ((int)(3 + i) < argc) {
			FILE *f = fopen(argv[3 + i], "rb");
			if (!f) { printf("input[%u] open failed %s\n", i, argv[3 + i]); _exit(8); }
			size_t got = fread(bufs[i]->va, 1, bufs[i]->size, f);
			fclose(f);
			if (got < bufs[i]->size) memset((char *)bufs[i]->va + got, 0, bufs[i]->size - got);
			printf("input[%u] file=%s bytes=%zu%s\n", i, argv[3 + i], got, got != bufs[i]->size ? " (SIZE MISMATCH, zero-padded)" : "");
		} else
			memset(bufs[i]->va, 0x80, bufs[i]->size);
	}
	fflush(stdout);
	double *lat = calloc(iters, sizeof *lat);
	int done = 0;
	double tr = now();
	for (int it = 0; it < iters; it++) {
		double ts = now();
		r = exec(model, 0);
		lat[it] = (now() - ts) * 1000;
		if (r) { printf("stage=execute iter=%d rc=%d\n", it, r); break; }
		done++;
	}
	double wall = now() - tr;
	if (done) {
		double first = lat[0], s = 0;
		for (int i = 0; i < done; i++) s += lat[i];
		qsort(lat, done, sizeof *lat, cmpd);
		printf("stage=execute n=%d first_ms=%.2f mean_ms=%.3f p50_ms=%.2f p99_ms=%.2f min_ms=%.2f max_ms=%.2f per_s=%.1f\n",
		       done, first, s / done, lat[done / 2], lat[(int)(done * 0.99)], lat[0], lat[done - 1], done / wall);
	}
	const char *outp = getenv("ENN_OUT");
	for (unsigned i = info.n_in_buf; i < n && !r; i++) {
		uint64_t sum = 0, nonzero = 0;
		const uint8_t *p = bufs[i] ? bufs[i]->va : NULL;
		for (uint32_t k = 0; p && k < bufs[i]->size; k++) { sum = sum * 31 + p[k]; nonzero += p[k] != 0; }
		printf("output[%u] checksum=%016llx nonzero_bytes=%llu\n", i - info.n_in_buf,
		       (unsigned long long)sum, (unsigned long long)nonzero);
		if (outp && p) {
			char path[512];
			snprintf(path, sizeof path, "%s.out%u.bin", outp, i - info.n_in_buf);
			FILE *f = fopen(path, "wb");
			if (f) { fwrite(p, 1, bufs[i]->size, f); fclose(f); }
		}
	}
	fflush(stdout);
	printf("stage=release rc=%d\n", release(bufs, (int)n));
	printf("stage=close rc=%d\n", close_model(model));
	printf("stage=deinit rc=%d\n", deinit());
	fflush(stdout);
	_exit(r ? 7 : 0);
}
