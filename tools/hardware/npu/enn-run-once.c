/*
 * enn-run-once MODEL.nnc [iterations]: run one vendor ENN model through the
 * public ENN C++ API (in-process engine, libenn_public_api_cpp_lib.so), with
 * deterministic input (all bytes 0x80). Prints buffer sizes, timing and an
 * output checksum. Build with the Android NDK (bionic); run inside the staged
 * vendor runtime chroot under a supervisor deadline.
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
typedef int (*init_fn)(void);
typedef int (*open_fn)(const char *, uint64_t *);
typedef int (*alloc_fn)(uint64_t, EnnBuffer ***, NumberOfBuffersInfo *, int, int);
typedef int (*exec_fn)(uint64_t, int);
typedef int (*release_fn)(EnnBuffer **, int);
typedef int (*close_fn)(uint64_t);

static double now(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t); return t.tv_sec + t.tv_nsec / 1e9; }
static void *sym(void *h, const char *n) {
	void *p = dlsym(h, n);
	if (!p) { printf("stage=dlsym missing=%s\n", n); fflush(stdout); _exit(3); }
	return p;
}

int main(int argc, char **argv)
{
	if (argc < 2) { fprintf(stderr, "usage: %s MODEL.nnc [iterations]\n", argv[0]); return 2; }
	int iters = argc > 2 ? atoi(argv[2]) : 5;
	void *h = dlopen("/vendor/lib64/libenn_public_api_cpp_lib.so", RTLD_NOW | RTLD_LOCAL);
	if (!h) { printf("stage=dlopen error=%s\n", dlerror()); return 2; }
	init_fn init = (init_fn)sym(h, "_ZN3enn3api13EnnInitializeEv");
	open_fn open_model = (open_fn)sym(h, "_ZN3enn3api12EnnOpenModelEPKcPm");
	alloc_fn alloc = (alloc_fn)sym(h, "_ZN3enn3api21EnnAllocateAllBuffersEmPPP10_ennBufferP20_NumberOfBuffersInfoib");
	exec_fn exec = (exec_fn)sym(h, "_ZN3enn3api15EnnExecuteModelEmi");
	release_fn release = (release_fn)sym(h, "_ZN3enn3api17EnnReleaseBuffersEPP10_ennBufferi");
	close_fn close_model = (close_fn)sym(h, "_ZN3enn3api13EnnCloseModelEm");
	init_fn deinit = (init_fn)sym(h, "_ZN3enn3api15EnnDeinitializeEv");

	double t0 = now();
	int r = init();
	printf("stage=initialize rc=%d t=%.3f\n", r, now() - t0); fflush(stdout);
	if (r) _exit(4);
	uint64_t model = 0;
	r = open_model(argv[1], &model);
	printf("stage=open_model rc=%d model=0x%llx t=%.3f\n", r, (unsigned long long)model, now() - t0); fflush(stdout);
	if (r) _exit(5);
	EnnBuffer **bufs = NULL;
	NumberOfBuffersInfo info = {0, 0};
	r = alloc(model, &bufs, &info, 0, 1);
	printf("stage=allocate rc=%d inputs=%u outputs=%u\n", r, info.n_in_buf, info.n_out_buf); fflush(stdout);
	if (r || !bufs) _exit(6);
	unsigned n = info.n_in_buf + info.n_out_buf;
	for (unsigned i = 0; i < n; i++)
		printf("buffer[%u] %s size=%u\n", i, i < info.n_in_buf ? "in" : "out", bufs[i] ? bufs[i]->size : 0);
	for (unsigned i = 0; i < info.n_in_buf; i++)
		if (bufs[i] && bufs[i]->va) memset(bufs[i]->va, 0x80, bufs[i]->size);
	fflush(stdout);
	for (int it = 0; it < iters; it++) {
		double ts = now();
		r = exec(model, 0);
		printf("stage=execute iter=%d rc=%d ms=%.2f\n", it, r, (now() - ts) * 1000); fflush(stdout);
		if (r) break;
	}
	for (unsigned i = info.n_in_buf; i < n && !r; i++) {
		uint64_t sum = 0, nonzero = 0;
		const uint8_t *p = bufs[i] ? bufs[i]->va : NULL;
		for (uint32_t k = 0; p && k < bufs[i]->size; k++) { sum = sum * 31 + p[k]; nonzero += p[k] != 0; }
		printf("output[%u] checksum=%016llx nonzero_bytes=%llu\n", i - info.n_in_buf,
		       (unsigned long long)sum, (unsigned long long)nonzero);
	}
	fflush(stdout);
	printf("stage=release rc=%d\n", release(bufs, (int)n));
	printf("stage=close rc=%d\n", close_model(model));
	printf("stage=deinit rc=%d\n", deinit());
	fflush(stdout);
	_exit(r ? 7 : 0);
}
