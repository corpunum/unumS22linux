#define main unused_cinit_main
#include "cinit12.snapshot.c"
#undef main
#include <limits.h>
int main(void) {
 printf("modules dss=%d wdt=%d ufs=%d absent=%d\n",module_present("dss"),module_present("s3c2410_wdt"),module_present("ufs-exynos-core"),module_present("round3_absent"));
 printf("partitions sda33=%d da33=%d a33=%d sda330=%d\n",wait_for_partition("sda33",1),wait_for_partition("da33",1),wait_for_partition("a33",1),wait_for_partition("sda330",1));
 printf("bound wdt=%d usb=%d ufs=%d phy=%d missing_device=%d missing_driver=%d\n",platform_bound("s3c2410-wdt","10050000.watchdog_cl0"),platform_bound("exynos-dwc3","10b00000.usb"),platform_bound("exynos-ufs","11100000.ufs"),platform_bound("phy_exynos_usbdrd","10aa0000.phy"),platform_bound("exynos-ufs","round3_absent"),platform_bound("round3_absent","round3_absent"));
 char b[4096];size_t n=0;int fd=open("/proc/cmdline",O_RDONLY);ssize_t r;while(n<sizeof(b)&&(r=read(fd,b+n,sizeof(b)-n))>0)n+=r;close(fd);
 log_cmdline_chunked(b,n,0);size_t off=0;int match=1;for(int i=0;i<early_log_n;i++){char *p=strstr(early_log[i],": ")+2;size_t k=strlen(p);if(!k||p[k-1]!='\n'){match=0;break;}k--;if(off+k>n||memcmp(p,b+off,k))match=0;off+=k;}
 printf("cmdline bytes=%zu chunks=%d exact=%d\n",n,early_log_n,match&&off==n);
 unsigned long vals[]={0,9,10,99,100,9999999,ULONG_MAX};int fail=0;for(unsigned i=0;i<sizeof(vals)/sizeof(*vals);i++){char a[32],e[32];int z=u2a(vals[i],a);a[z]=0;snprintf(e,sizeof(e),"%lu",vals[i]);if(strcmp(a,e))fail++;}printf("u2a boundary_failures=%d\n",fail);
 return 0;
}
