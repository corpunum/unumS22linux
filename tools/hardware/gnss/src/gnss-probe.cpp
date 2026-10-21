// gnss-probe: minimal GNSS client for the S22 under native Linux.
//
// Loads Samsung's passthrough GNSS HAL (android.hardware.gnss@2.1-impl.so) in-process
// (no binder, no hwservicemanager), registers an IGnssCallback 2.1, injects the system
// time and starts a standalone session. It prints satellites in view (SV/C/N0) every
// few seconds and a fix summary. Coordinates are REDACTED unless --show-location;
// NMEA sentences that carry a position (GGA, RMC, GLL, GNS) are redacted the same way.
//
// usage: gnss-probe [--seconds N] [--hal PATH] [--nmea] [--show-location] [--no-time]
//
// Built with the NDK against AOSP's generated HIDL headers, linked against the phone's own
// libhidlbase/libutils/libc++ (platform libc++, std::__1). See build.sh.

#include <android/hardware/gnss/2.1/IGnss.h>
#include <android/hardware/gnss/2.1/IGnssCallback.h>
#include <dlfcn.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#include <map>
#include <mutex>
#include <string>
#include <vector>

using ::android::sp;
using ::android::hardware::hidl_bitfield;
using ::android::hardware::hidl_string;
using ::android::hardware::hidl_vec;
using ::android::hardware::Return;
using ::android::hardware::Void;
namespace V1_0 = ::android::hardware::gnss::V1_0;
namespace V2_0 = ::android::hardware::gnss::V2_0;
namespace V2_1 = ::android::hardware::gnss::V2_1;

static bool g_show_loc = false, g_nmea = false;
static volatile sig_atomic_t g_stop = 0;
static std::mutex g_mu;

static double now_s() {
    timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return t.tv_sec + t.tv_nsec / 1e9;
}
static double g_t0 = now_s();

struct Sv {
    int cons, svid;
    float cn0, elev, az;
    bool used, eph, alm;
};
static std::vector<Sv> g_svs;
static int g_sv_reports = 0, g_nmea_lines = 0, g_fixes = 0, g_status = -1;
static float g_last_acc = -1;
static double g_first_sv = -1, g_first_fix = -1;
static uint32_t g_caps = 0;
static std::string g_name;

static const char *cons_name(int c) {
    switch (c) {
        case 1: return "GPS";
        case 2: return "SBAS";
        case 3: return "GLO";
        case 4: return "QZSS";
        case 5: return "BDS";
        case 6: return "GAL";
        case 7: return "IRNSS";
        default: return "?";
    }
}

static void redact_nmea(const std::string &s) {
    // $xxGGA,$xxRMC,$xxGLL,$xxGNS carry lat/lon: keep the talker+type and the fix-quality fields only
    std::string t = s;
    while (!t.empty() && (t.back() == '\n' || t.back() == '\r')) t.pop_back();
    bool pos = t.size() > 6 && (!t.compare(3, 3, "GGA") || !t.compare(3, 3, "RMC") || !t.compare(3, 3, "GLL") ||
                                !t.compare(3, 3, "GNS"));
    if (pos && !g_show_loc) {
        // GGA field 6 = fix quality, 7 = satellites used; RMC field 2 = status A/V
        std::vector<std::string> f;
        size_t a = 0, b;
        while ((b = t.find(',', a)) != std::string::npos) {
            f.push_back(t.substr(a, b - a));
            a = b + 1;
        }
        f.push_back(t.substr(a));
        if (!t.compare(3, 3, "GGA") && f.size() > 7)
            printf("[%6.1f] NMEA %s (position redacted) quality=%s sats=%s\n", now_s() - g_t0, f[0].c_str(), f[6].c_str(), f[7].c_str());
        else if (!t.compare(3, 3, "RMC") && f.size() > 2)
            printf("[%6.1f] NMEA %s (position redacted) status=%s\n", now_s() - g_t0, f[0].c_str(), f[2].c_str());
        else
            printf("[%6.1f] NMEA %s (position redacted)\n", now_s() - g_t0, f[0].c_str());
    } else {
        printf("[%6.1f] NMEA %s\n", now_s() - g_t0, t.c_str());
    }
}

struct Cb : public V2_1::IGnssCallback {
    // ---- 1.0
    Return<void> gnssLocationCb(const V1_0::GnssLocation &l) override {
        report(l, "1.0");
        return Void();
    }
    Return<void> gnssStatusCb(V1_0::IGnssCallback::GnssStatusValue s) override {
        std::lock_guard<std::mutex> g(g_mu);
        g_status = (int)s;
        static const char *n[] = {"NONE", "SESSION_BEGIN", "SESSION_END", "ENGINE_ON", "ENGINE_OFF"};
        printf("[%6.1f] status %s\n", now_s() - g_t0, (int)s < 5 ? n[(int)s] : "?");
        fflush(stdout);
        return Void();
    }
    Return<void> gnssSvStatusCb(const V1_0::IGnssCallback::GnssSvStatus &st) override {
        std::vector<Sv> v;
        for (uint32_t i = 0; i < st.numSvs && i < 64; i++) {
            auto &s = st.gnssSvList[i];
            v.push_back(sv(s, (int)s.constellation));
        }
        svs(v, "1.0");
        return Void();
    }
    Return<void> gnssNmeaCb(int64_t, const hidl_string &nmea) override {
        std::lock_guard<std::mutex> g(g_mu);
        g_nmea_lines++;
        if (g_nmea) redact_nmea(nmea);
        return Void();
    }
    Return<void> gnssSetCapabilitesCb(hidl_bitfield<V1_0::IGnssCallback::Capabilities> c) override {
        caps(c);
        return Void();
    }
    Return<void> gnssAcquireWakelockCb() override { return Void(); }
    Return<void> gnssReleaseWakelockCb() override { return Void(); }
    Return<void> gnssRequestTimeCb() override {
        printf("[%6.1f] HAL requests time\n", now_s() - g_t0);
        return Void();
    }
    Return<void> gnssSetSystemInfoCb(const V1_0::IGnssCallback::GnssSystemInfo &i) override {
        printf("[%6.1f] system info: year of hw %u\n", now_s() - g_t0, i.yearOfHw);
        return Void();
    }
    // ---- 1.1
    Return<void> gnssNameCb(const hidl_string &name) override {
        g_name = name;
        printf("[%6.1f] engine name: %s\n", now_s() - g_t0, name.c_str());
        return Void();
    }
    Return<void> gnssRequestLocationCb(bool) override {
        printf("[%6.1f] HAL requests a location (network location not available)\n", now_s() - g_t0);
        return Void();
    }
    // ---- 2.0
    Return<void> gnssSetCapabilitiesCb_2_0(hidl_bitfield<V2_0::IGnssCallback::Capabilities> c) override {
        caps(c);
        return Void();
    }
    Return<void> gnssLocationCb_2_0(const V2_0::GnssLocation &l) override {
        report(l.v1_0, "2.0");
        return Void();
    }
    Return<void> gnssRequestLocationCb_2_0(bool, bool) override {
        printf("[%6.1f] HAL requests a location (network location not available)\n", now_s() - g_t0);
        return Void();
    }
    Return<void> gnssSvStatusCb_2_0(const hidl_vec<V2_0::IGnssCallback::GnssSvInfo> &l) override {
        std::vector<Sv> v;
        for (auto &s : l) v.push_back(sv(s.v1_0, (int)s.constellation));
        svs(v, "2.0");
        return Void();
    }
    // ---- 2.1
    Return<void> gnssSetCapabilitiesCb_2_1(hidl_bitfield<V2_1::IGnssCallback::Capabilities> c) override {
        caps(c);
        return Void();
    }
    Return<void> gnssSvStatusCb_2_1(const hidl_vec<V2_1::IGnssCallback::GnssSvInfo> &l) override {
        std::vector<Sv> v;
        for (auto &s : l) v.push_back(sv(s.v2_0.v1_0, (int)s.v2_0.constellation));
        svs(v, "2.1");
        return Void();
    }

  private:
    static Sv sv(const V1_0::IGnssCallback::GnssSvInfo &s, int cons) {
        uint8_t f = s.svFlag;
        return Sv{cons, s.svid, s.cN0Dbhz, s.elevationDegrees, s.azimuthDegrees, (f & 4) != 0, (f & 1) != 0, (f & 2) != 0};
    }
    static void caps(uint32_t c) {
        g_caps = c;
        printf("[%6.1f] capabilities 0x%x\n", now_s() - g_t0, c);
    }
    static void svs(std::vector<Sv> &v, const char *ver) {
        std::lock_guard<std::mutex> g(g_mu);
        g_svs = v;
        g_sv_reports++;
        if (!v.empty() && g_first_sv < 0) g_first_sv = now_s() - g_t0;
        (void)ver;
    }
    static void report(const V1_0::GnssLocation &l, const char *ver) {
        std::lock_guard<std::mutex> g(g_mu);
        g_fixes++;
        if (g_first_fix < 0) g_first_fix = now_s() - g_t0;
        g_last_acc = l.horizontalAccuracyMeters;
        if (g_show_loc)
            printf("[%6.1f] FIX (%s) %.6f %.6f alt %.0f m acc %.0f m\n", now_s() - g_t0, ver, l.latitudeDegrees,
                   l.longitudeDegrees, l.altitudeMeters, l.horizontalAccuracyMeters);
        else
            printf("[%6.1f] FIX (%s) flags 0x%x acc %.0f m (coordinates redacted)\n", now_s() - g_t0, ver,
                   (unsigned)l.gnssLocationFlags, l.horizontalAccuracyMeters);
        fflush(stdout);
    }
};

static void print_svs(bool final_) {
    std::lock_guard<std::mutex> g(g_mu);
    int used = 0, over20 = 0;
    float best = 0;
    std::map<std::string, int> per;
    for (auto &s : g_svs) {
        used += s.used;
        over20 += s.cn0 >= 20;
        if (s.cn0 > best) best = s.cn0;
        per[cons_name(s.cons)]++;
    }
    printf("[%6.1f] %s: %zu SVs in view (", now_s() - g_t0, final_ ? "FINAL" : "svs", g_svs.size());
    bool first = true;
    for (auto &p : per) {
        printf("%s%s %d", first ? "" : ", ", p.first.c_str(), p.second);
        first = false;
    }
    printf("), used in fix %d, C/N0>=20 %d, best C/N0 %.1f dB-Hz; reports %d, nmea %d, fixes %d\n", used, over20, best,
           g_sv_reports, g_nmea_lines, g_fixes);
    if (final_ || g_svs.size() <= 40) {
        for (auto &s : g_svs)
            if (s.cn0 > 0)
                printf("         %-5s %3d  C/N0 %4.1f  el %3.0f az %3.0f%s%s%s\n", cons_name(s.cons), s.svid, s.cn0,
                       s.elev, s.az, s.used ? " USED" : "", s.eph ? " eph" : "", s.alm ? " alm" : "");
    }
    fflush(stdout);
}

static void on_sig(int) { g_stop = 1; }

int main(int argc, char **argv) {
    int seconds = 120;
    bool inject_time = true;
    const char *hal = "/vendor/lib64/hw/android.hardware.gnss@2.1-impl.so";
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--seconds") && i + 1 < argc) seconds = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--hal") && i + 1 < argc) hal = argv[++i];
        else if (!strcmp(argv[i], "--nmea")) g_nmea = true;
        else if (!strcmp(argv[i], "--show-location")) g_show_loc = true;
        else if (!strcmp(argv[i], "--no-time")) inject_time = false;
        else {
            fprintf(stderr, "usage: gnss-probe [--seconds N] [--hal PATH] [--nmea] [--show-location] [--no-time]\n");
            return 2;
        }
    }
    setvbuf(stdout, nullptr, _IOLBF, 0);
    signal(SIGINT, on_sig);
    signal(SIGTERM, on_sig);
    void *h = dlopen(hal, RTLD_NOW);
    if (!h) {
        printf("dlopen %s failed: %s\n", hal, dlerror());
        return 3;
    }
    using Fetch = V1_0::IGnss *(*)(const char *);
    auto fetch = (Fetch)dlsym(h, "HIDL_FETCH_IGnss");
    if (!fetch) {
        printf("no HIDL_FETCH_IGnss in %s\n", hal);
        return 3;
    }
    sp<V1_0::IGnss> g10 = fetch("default");
    if (!g10) {
        printf("HIDL_FETCH_IGnss returned null\n");
        return 4;
    }
    sp<V2_1::IGnss> g = V2_1::IGnss::castFrom(g10);
    if (!g) {
        printf("HAL object is not IGnss@2.1\n");
        return 4;
    }
    printf("[%6.1f] HAL loaded: %s\n", now_s() - g_t0, hal);
    sp<Cb> cb = new Cb();
    bool ok = g->setCallback_2_1(cb);
    printf("[%6.1f] setCallback_2_1 -> %d\n", now_s() - g_t0, ok);
    if (!ok) return 5;
    ok = g->setPositionMode_1_1(V1_0::IGnss::GnssPositionMode::STANDALONE,
                                V1_0::IGnss::GnssPositionRecurrence::RECURRENCE_PERIODIC, 1000, 0, 0, false);
    printf("[%6.1f] setPositionMode_1_1(standalone, 1 s) -> %d\n", now_s() - g_t0, ok);
    if (inject_time) {
        timespec rt, bt;
        clock_gettime(CLOCK_REALTIME, &rt);
        clock_gettime(CLOCK_BOOTTIME, &bt);
        int64_t ms = (int64_t)rt.tv_sec * 1000 + rt.tv_nsec / 1000000;
        int64_t ref = (int64_t)bt.tv_sec * 1000 + bt.tv_nsec / 1000000;
        ok = g->injectTime(ms, ref, 2000);
        printf("[%6.1f] injectTime(system clock, +-2 s) -> %d\n", now_s() - g_t0, ok);
    }
    ok = g->start();
    printf("[%6.1f] start -> %d\n", now_s() - g_t0, ok);
    double last = now_s();
    while (!g_stop && now_s() - g_t0 < seconds) {
        usleep(200 * 1000);
        if (now_s() - last >= 10) {
            print_svs(false);
            last = now_s();
        }
    }
    print_svs(true);
    printf("SUMMARY engine=\"%s\" caps=0x%x status=%d sv_reports=%d nmea=%d fixes=%d first_sv_s=%.1f first_fix_s=%.1f last_acc_m=%.0f\n",
           g_name.c_str(), g_caps, g_status, g_sv_reports, g_nmea_lines, g_fixes, g_first_sv, g_first_fix, g_last_acc);
    g->stop();
    g->cleanup();
    printf("[%6.1f] stopped\n", now_s() - g_t0);
    fflush(stdout);
    _exit(0);  // skip HAL static destructors
}
