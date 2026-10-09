/*
 * paradee-tts: self-contained runner for Paradee-8M (int8 ONNX, Kokoro af_heart
 * distillation) for the S22. No Python, no pip.
 *
 *   text -> sentences -> clauses -> eSpeak NG IPA (libespeak-ng, dlopen'ed)
 *        -> misaki spelling (port of paradee web/misaki.js) -> Kokoro vocab ids
 *        -> onnxruntime C API -> 24 kHz float -> 16-bit WAV (and/or raw PCM stream)
 *
 * CLI follows sherpa-onnx-offline-tts (--key=value, text as last argument):
 *   paradee-tts --model=paradee_int8.onnx --output-filename=out.wav "Hello there."
 * Extra:
 *   --raw-stdout          stream s16le 24 kHz mono to stdout, one sentence at a time
 *   --phonemes-only       print the phonemes per chunk and exit (no ONNX run)
 *   --input-phonemes      treat the text as misaki phonemes (skip G2P)
 *   --serve               keep the model loaded; read "OUT.wav<TAB>text" lines on
 *                         stdin, answer "ok OUT.wav audio=..s elapsed=..s" per line
 *   --espeak-lib=PATH     default libespeak-ng.so.1
 *   --espeak-data=DIR     directory that contains espeak-ng-data
 *                         (default $ESPEAK_DATA_PATH, else the library default)
 *   --voice=en-us  --speed=1.0  --num-threads=1  --sid=N (accepted, ignored)
 *   text may also come from stdin when no positional text is given.
 *
 * Apache-2.0 (same as Paradee and Kokoro).
 */
#include <dlfcn.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include "onnxruntime_c_api.h"
#include "vocab.h"

#define SR 24000
#define MAX_PH 510

/* ---------- small utf-8 / codepoint string helpers ---------- */
typedef struct { uint32_t *v; size_t n, cap; } cps;

static void cps_push(cps *s, uint32_t c) {
  if (s->n == s->cap) { s->cap = s->cap ? s->cap * 2 : 256; s->v = realloc(s->v, s->cap * sizeof *s->v); }
  s->v[s->n++] = c;
}
static void utf8_append(cps *s, const char *p) {
  const unsigned char *u = (const unsigned char *)p;
  while (*u) {
    uint32_t c; int k;
    if (*u < 0x80) { c = *u; k = 0; }
    else if ((*u & 0xE0) == 0xC0) { c = *u & 0x1F; k = 1; }
    else if ((*u & 0xF0) == 0xE0) { c = *u & 0x0F; k = 2; }
    else { c = *u & 0x07; k = 3; }
    u++;
    while (k-- > 0 && (*u & 0xC0) == 0x80) c = (c << 6) | (*u++ & 0x3F);
    cps_push(s, c);
  }
}
static void utf8_print(FILE *f, const uint32_t *v, size_t n) {
  for (size_t i = 0; i < n; i++) {
    uint32_t c = v[i];
    if (c < 0x80) fputc(c, f);
    else if (c < 0x800) { fputc(0xC0 | c >> 6, f); fputc(0x80 | (c & 0x3F), f); }
    else if (c < 0x10000) { fputc(0xE0 | c >> 12, f); fputc(0x80 | ((c >> 6) & 0x3F), f); fputc(0x80 | (c & 0x3F), f); }
    else { fputc(0xF0 | c >> 18, f); fputc(0x80 | ((c >> 12) & 0x3F), f); fputc(0x80 | ((c >> 6) & 0x3F), f); fputc(0x80 | (c & 0x3F), f); }
  }
}
/* replace every occurrence of utf-8 `from` by utf-8 `to` */
static void cps_replace(cps *s, const char *from, const char *to) {
  cps a = {0}, b = {0};
  utf8_append(&a, from); utf8_append(&b, to);
  cps o = {0};
  for (size_t i = 0; i < s->n;) {
    if (i + a.n <= s->n && !memcmp(s->v + i, a.v, a.n * sizeof *a.v)) {
      for (size_t j = 0; j < b.n; j++) cps_push(&o, b.v[j]);
      i += a.n;
    } else cps_push(&o, s->v[i++]);
  }
  free(s->v); *s = o; free(a.v); free(b.v);
}
static int is_ws(uint32_t c) { return c == ' ' || c == '\t' || c == '\n' || c == '\r' || c == 0xA0; }
/* the delimiter class used by misaki.js lookaheads: [\s;:,.!?—…"“”()] or end */
static int is_delim(uint32_t c) {
  return is_ws(c) || c == ';' || c == ':' || c == ',' || c == '.' || c == '!' || c == '?' ||
         c == 0x2014 || c == 0x2026 || c == '"' || c == 0x201C || c == 0x201D || c == '(' || c == ')';
}

/* ---------- eSpeak IPA -> misaki spelling (paradee web/misaki.js) ---------- */
static void to_misaki(cps *s) {
  static const char *E2M[][2] = {
    {"ʔˌn\xCC\xA9", "ʔn"}, {"ʔn\xCC\xA9", "ʔn"},
    {"aɪ", "I"}, {"aʊ", "W"},
    {"dʒ", "ʤ"},
    {"eɪ", "A"}, {"e", "A"},
    {"tʃ", "ʧ"},
    {"ɔɪ", "Y"},
    {"ʲo", "jo"}, {"ʲə", "jə"}, {"ʲ", ""},
    {"ɚ", "əɹ"},
    {"r", "ɹ"},
    {"x", "k"}, {"ç", "k"},
    {"ɬ", "l"},
    {"\xCC\x83", ""},
  };
  for (size_t i = 0; i < sizeof E2M / sizeof *E2M; i++) cps_replace(s, E2M[i][0], E2M[i][1]);
  /* (\S)̩ -> ᵊ$1 ; then drop any remaining U+0329 */
  cps o = {0};
  for (size_t i = 0; i < s->n; i++) {
    if (i + 1 < s->n && s->v[i + 1] == 0x0329 && !is_ws(s->v[i])) {
      cps_push(&o, 0x1D4A); cps_push(&o, s->v[i]); i++;
    } else if (s->v[i] != 0x0329) cps_push(&o, s->v[i]);
  }
  free(s->v); *s = o;
  /* word-final əl -> ᵊl ; ɐ not word-final -> ə */
  for (size_t i = 0; i < s->n; i++) {
    int end_next = (i + 2 >= s->n) || is_delim(s->v[i + 2]);
    if (s->v[i] == 0x0259 && i + 1 < s->n && s->v[i + 1] == 'l' && end_next) s->v[i] = 0x1D4A;
    if (s->v[i] == 0x0250 && i + 1 < s->n && !is_delim(s->v[i + 1])) s->v[i] = 0x0259;
  }
  cps_replace(s, "oʊ", "O");
  cps_replace(s, "ɜːɹ", "ɜɹ");
  cps_replace(s, "ɜː", "ɜɹ");
  cps_replace(s, "ɪə", "iə");
  cps_replace(s, "ː", "");
  cps_replace(s, "o", "ɔ");
  cps_replace(s, "ɾ", "T");
  cps_replace(s, "ʔ", "t");
}

/* ---------- eSpeak NG via dlopen ---------- */
typedef int (*espeak_init_fn)(int, int, const char *, int);
typedef int (*espeak_voice_fn)(const char *);
typedef const char *(*espeak_t2p_fn)(const void **, int, int);
static espeak_t2p_fn es_t2p;

static int espeak_load(const char *lib, const char *data, const char *voice) {
  void *h = dlopen(lib, RTLD_LAZY);
  if (!h) { fprintf(stderr, "paradee-tts: dlopen %s: %s\n", lib, dlerror()); return -1; }
  espeak_init_fn init = (espeak_init_fn)dlsym(h, "espeak_Initialize");
  espeak_voice_fn setv = (espeak_voice_fn)dlsym(h, "espeak_SetVoiceByName");
  es_t2p = (espeak_t2p_fn)dlsym(h, "espeak_TextToPhonemes");
  if (!init || !setv || !es_t2p) { fprintf(stderr, "paradee-tts: espeak symbols missing\n"); return -1; }
  if (init(1 /* AUDIO_OUTPUT_RETRIEVAL */, 0, data, 0) < 0) { fprintf(stderr, "paradee-tts: espeak_Initialize failed (data=%s)\n", data ? data : "(default)"); return -1; }
  if (setv(voice) != 0) { fprintf(stderr, "paradee-tts: espeak voice %s not found\n", voice); return -1; }
  return 0;
}

/* phonemize one clause (no clause punctuation inside) and append to out */
static void espeak_clause(cps *out, const char *text) {
  const void *p = text;
  int first = 1;
  while (p) {
    const char *ph = es_t2p(&p, 1 /* espeakCHARS_UTF8 */, 0x02 /* IPA, no ties */);
    if (!ph || !*ph) continue;
    if (!first) cps_push(out, ' ');
    while (*ph == ' ') ph++;
    utf8_append(out, ph);
    while (out->n && is_ws(out->v[out->n - 1])) out->n--;
    first = 0;
  }
}

static int is_clause_punct(uint32_t c) {
  return c == ',' || c == ';' || c == ':' || c == '.' || c == '!' || c == '?' || c == 0x2014 || c == 0x2026;
}

/* sentence text -> misaki-style phonemes, keeping clause punctuation like misaki does */
static void g2p_sentence(cps *out, const uint32_t *t, size_t n) {
  cps clause = {0};
  char *buf = NULL; size_t cap = 0;
  for (size_t i = 0; i <= n; i++) {
    uint32_t c = i < n ? t[i] : 0;
    int boundary = 0;
    if (i == n) boundary = 1;
    else if (is_clause_punct(c) && (c == 0x2014 || c == 0x2026 || i + 1 == n || is_ws(t[i + 1]) || t[i + 1] == '"' || t[i + 1] == 0x201D || t[i + 1] == ')')) boundary = 1;
    if (!boundary) {
      if (c == '"' || c == 0x201C || c == 0x201D || c == '(' || c == ')' || c == '[' || c == ']') c = ' ';
      cps_push(&clause, c);
      continue;
    }
    /* flush clause to espeak */
    size_t need = clause.n * 4 + 1;
    if (need > cap) { cap = need; buf = realloc(buf, cap); }
    FILE *m = fmemopen(buf, cap, "w");
    utf8_print(m, clause.v, clause.n); fputc(0, m); fclose(m);
    int empty = 1;
    for (char *q = buf; *q; q++) if (!is_ws((unsigned char)*q)) { empty = 0; break; }
    if (!empty) {
      if (out->n && !is_ws(out->v[out->n - 1])) cps_push(out, ' ');
      espeak_clause(out, buf);
    }
    if (i < n) {
      /* collapse runs like "?!" or "..." into the last mark */
      size_t j = i;
      while (j + 1 < n && is_clause_punct(t[j + 1])) j++;
      uint32_t pc = (j > i && t[i] == '.') ? 0x2026 : t[j];
      while (out->n && is_ws(out->v[out->n - 1])) out->n--;
      if (out->n) cps_push(out, pc);
      i = j;
    }
    clause.n = 0;
  }
  while (out->n && is_ws(out->v[out->n - 1])) out->n--;
  free(clause.v); free(buf);
}

/* ---------- vocab ---------- */
static int vocab_id(uint32_t c) {
  for (size_t i = 0; i < sizeof VOCAB / sizeof *VOCAB; i++) if (VOCAB[i].cp == c) return VOCAB[i].id;
  return -1;
}

/* ---------- onnxruntime ---------- */
static const OrtApi *ort;
static OrtSession *sess;
static OrtMemoryInfo *meminfo;
#define ORTCHK(x) do { OrtStatus *st_ = (x); if (st_) { fprintf(stderr, "paradee-tts: onnxruntime: %s\n", ort->GetErrorMessage(st_)); exit(1); } } while (0)

static float *synth(const int64_t *ids, size_t n, float speed, size_t *nout) {
  int64_t shp[2] = {1, (int64_t)n}, shs[1] = {1};
  OrtValue *in[2] = {0}, *out = NULL;
  ORTCHK(ort->CreateTensorWithDataAsOrtValue(meminfo, (void *)ids, n * sizeof *ids, shp, 2, ONNX_TENSOR_ELEMENT_DATA_TYPE_INT64, &in[0]));
  ORTCHK(ort->CreateTensorWithDataAsOrtValue(meminfo, &speed, sizeof speed, shs, 1, ONNX_TENSOR_ELEMENT_DATA_TYPE_FLOAT, &in[1]));
  const char *inn[2] = {"input_ids", "speed"}, *outn[1] = {"waveform"};
  ORTCHK(ort->Run(sess, NULL, inn, (const OrtValue *const *)in, 2, outn, 1, &out));
  OrtTensorTypeAndShapeInfo *info;
  ORTCHK(ort->GetTensorTypeAndShape(out, &info));
  size_t cnt; ORTCHK(ort->GetTensorShapeElementCount(info, &cnt));
  ort->ReleaseTensorTypeAndShapeInfo(info);
  float *data; ORTCHK(ort->GetTensorMutableData(out, (void **)&data));
  float *r = malloc(cnt * sizeof *r); memcpy(r, data, cnt * sizeof *r);
  ort->ReleaseValue(out); ort->ReleaseValue(in[0]); ort->ReleaseValue(in[1]);
  *nout = cnt;
  return r;
}

/* ---------- wav ---------- */
static void wav_header(FILE *f, uint32_t nsamp) {
  uint32_t data = nsamp * 2, riff = 36 + data, sr = SR, br = SR * 2, fmt = 16;
  uint16_t pcm = 1, ch = 1, ba = 2, bits = 16;
  fwrite("RIFF", 1, 4, f); fwrite(&riff, 4, 1, f); fwrite("WAVEfmt ", 1, 8, f);
  fwrite(&fmt, 4, 1, f); fwrite(&pcm, 2, 1, f); fwrite(&ch, 2, 1, f); fwrite(&sr, 4, 1, f);
  fwrite(&br, 4, 1, f); fwrite(&ba, 2, 1, f); fwrite(&bits, 2, 1, f); fwrite("data", 1, 4, f); fwrite(&data, 4, 1, f);
}
static void to_s16(const float *a, size_t n, int16_t *o) {
  for (size_t i = 0; i < n; i++) { float x = a[i]; x = x > 1 ? 1 : x < -1 ? -1 : x; o[i] = (int16_t)(x * 32767.0f); }
}

static double now(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t); return t.tv_sec + t.tv_nsec * 1e-9; }

typedef struct { double first, elapsed, onnx, audio; } stats;

/* synthesize `text`; WAV to outfile and/or raw s16le to rawout, sentence by sentence */
static int speak(const char *text, const char *outfile, FILE *rawout, int ph_only, int in_ph, float speed, stats *st) {
  int raw = rawout != NULL;
  FILE *wf = NULL;
  if (outfile) { wf = fopen(outfile, "wb"); if (!wf) { perror(outfile); return 1; } wav_header(wf, 0); }
  cps t = {0}; utf8_append(&t, text);
  size_t total = 0; double t_syn = 0, t_first = -1, t1 = now();
  /* split into sentences: after [.!?…] + whitespace, and at newlines */
  size_t s0 = 0;
  for (size_t i = 0; i <= t.n; i++) {
    int cut = (i == t.n) || t.v[i] == '\n' ||
              (is_ws(t.v[i]) && i > 0 && (t.v[i - 1] == '.' || t.v[i - 1] == '!' || t.v[i - 1] == '?' || t.v[i - 1] == 0x2026));
    if (!cut) continue;
    size_t a = s0, b = i; s0 = i + 1;
    while (a < b && is_ws(t.v[a])) a++;
    while (b > a && is_ws(t.v[b - 1])) b--;
    if (a == b) continue;
    cps ph = {0};
    if (in_ph) for (size_t k = a; k < b; k++) cps_push(&ph, t.v[k]);
    else { g2p_sentence(&ph, t.v + a, b - a); to_misaki(&ph); }
    /* keep only in-vocab symbols, then cut into <=510-symbol chunks at spaces */
    size_t m = 0;
    for (size_t k = 0; k < ph.n; k++) if (vocab_id(ph.v[k]) >= 0) ph.v[m++] = ph.v[k];
    ph.n = m;
    size_t p = 0;
    while (p < ph.n) {
      size_t len = ph.n - p;
      if (len > MAX_PH) {
        size_t c = MAX_PH;
        while (c > 0 && ph.v[p + c] != ' ') c--;
        len = c ? c : MAX_PH;
      }
      if (ph_only) { utf8_print(stdout, ph.v + p, len); fputc('\n', stdout); }
      else {
        int64_t *ids = malloc((len + 2) * sizeof *ids);
        ids[0] = 0; for (size_t k = 0; k < len; k++) ids[k + 1] = vocab_id(ph.v[p + k]); ids[len + 1] = 0;
        double ts = now(); size_t na; float *au = synth(ids, len + 2, speed, &na); t_syn += now() - ts;
        int16_t *pcm = malloc(na * sizeof *pcm); to_s16(au, na, pcm);
        if (raw) { fwrite(pcm, 2, na, rawout); fflush(rawout); }
        if (wf) fwrite(pcm, 2, na, wf);
        if (t_first < 0) t_first = now() - t1;
        total += na; free(pcm); free(au); free(ids);
      }
      p += len;
      while (p < ph.n && ph.v[p] == ' ') p++;
    }
    free(ph.v);
  }
  if (wf) { fseek(wf, 0, SEEK_SET); wav_header(wf, (uint32_t)total); fclose(wf); }
  st->first = t_first; st->elapsed = now() - t1; st->onnx = t_syn; st->audio = (double)total / SR;
  free(t.v);
  return total || ph_only ? 0 : 1;
}

static const char *opt(const char *a, const char *key) {
  size_t k = strlen(key);
  return (!strncmp(a, key, k) && a[k] == '=') ? a + k + 1 : NULL;
}

int main(int argc, char **argv) {
  const char *model = "paradee_int8.onnx", *outfile = NULL, *eslib = "libespeak-ng.so.1";
  const char *esdata = getenv("ESPEAK_DATA_PATH"), *voice = "en-us", *text = NULL, *v;
  int threads = 1, raw = 0, ph_only = 0, in_ph = 0, serve = 0;
  float speed = 1.0f;
  for (int i = 1; i < argc; i++) {
    const char *a = argv[i];
    if ((v = opt(a, "--model"))) model = v;
    else if ((v = opt(a, "--output-filename"))) outfile = v;
    else if ((v = opt(a, "--espeak-lib"))) eslib = v;
    else if ((v = opt(a, "--espeak-data"))) esdata = v;
    else if ((v = opt(a, "--voice"))) voice = v;
    else if ((v = opt(a, "--num-threads"))) threads = atoi(v);
    else if ((v = opt(a, "--speed"))) speed = (float)atof(v);
    else if ((v = opt(a, "--sid"))) (void)v;
    else if (!strcmp(a, "--raw-stdout")) raw = 1;
    else if (!strcmp(a, "--serve")) serve = 1;
    else if (!strcmp(a, "--phonemes-only")) ph_only = 1;
    else if (!strcmp(a, "--input-phonemes")) in_ph = 1;
    else if (!strcmp(a, "--help") || !strcmp(a, "-h")) {
      fprintf(stderr, "usage: paradee-tts --model=M.onnx [--output-filename=out.wav] [--raw-stdout] [--num-threads=N] [--speed=S]\n"
                      "       [--espeak-lib=L] [--espeak-data=DIR] [--voice=en-us] [--phonemes-only] [--input-phonemes] [--serve] TEXT\n");
      return 0;
    } else if (!strncmp(a, "--", 2)) { fprintf(stderr, "paradee-tts: unknown option %s\n", a); return 2; }
    else text = a;
  }
  char *stdin_buf = NULL;
  if (!text && !serve) {
    size_t cap = 0, n = 0, r; char tmp[4096];
    while ((r = fread(tmp, 1, sizeof tmp, stdin)) > 0) { stdin_buf = realloc(stdin_buf, cap = n + r + 1); memcpy(stdin_buf + n, tmp, r); n += r; }
    if (!stdin_buf) { fprintf(stderr, "paradee-tts: no text\n"); return 2; }
    stdin_buf[n] = 0; text = stdin_buf;
  }
  if (!outfile && !raw && !ph_only && !serve) { fprintf(stderr, "paradee-tts: need --output-filename=F and/or --raw-stdout\n"); return 2; }
  if (!in_ph && espeak_load(eslib, esdata, voice)) return 1;

  double t0 = now();
  if (!ph_only) {
    ort = OrtGetApiBase()->GetApi(ORT_API_VERSION);
    if (!ort) { fprintf(stderr, "paradee-tts: onnxruntime API version mismatch\n"); return 1; }
    OrtEnv *env; ORTCHK(ort->CreateEnv(ORT_LOGGING_LEVEL_ERROR, "paradee", &env));
    OrtSessionOptions *so; ORTCHK(ort->CreateSessionOptions(&so));
    ORTCHK(ort->SetIntraOpNumThreads(so, threads));
    ORTCHK(ort->SetInterOpNumThreads(so, 1));
    ORTCHK(ort->SetSessionGraphOptimizationLevel(so, ORT_ENABLE_ALL));
    ORTCHK(ort->CreateSession(env, model, so, &sess));
    ORTCHK(ort->CreateCpuMemoryInfo(OrtArenaAllocator, OrtMemTypeDefault, &meminfo));
  }
  double t_load = now() - t0;

  if (serve) {
    /* one request per stdin line: OUTPUT.wav<TAB>text ; one reply line per request */
    char *line = NULL; size_t cap = 0; ssize_t r;
    fprintf(stdout, "ready load=%.3fs\n", t_load); fflush(stdout);
    while ((r = getline(&line, &cap, stdin)) > 0) {
      while (r > 0 && (line[r - 1] == '\n' || line[r - 1] == '\r')) line[--r] = 0;
      char *tab = strchr(line, '\t');
      if (!tab) { fprintf(stdout, "err usage: OUT.wav<TAB>TEXT\n"); fflush(stdout); continue; }
      *tab = 0;
      stats st; int rc = speak(tab + 1, line, NULL, 0, in_ph, speed, &st);
      fprintf(stdout, rc ? "err %s\n" : "ok %s audio=%.3fs elapsed=%.3fs\n", line, st.audio, st.elapsed);
      fflush(stdout);
    }
    free(line);
    return 0;
  }
  stats st;
  int rc = speak(text, outfile, raw ? stdout : NULL, ph_only, in_ph, speed, &st);
  if (!ph_only)
    fprintf(stderr, "paradee-tts: threads=%d load=%.3fs first-audio=%.3fs elapsed=%.3fs (onnx %.3fs) audio=%.3fs RTF=%.3f\n",
            threads, t_load, st.first, st.elapsed, st.onnx, st.audio, st.audio > 0 ? st.elapsed / st.audio : 0);
  free(stdin_buf);
  return rc;
}
