
#define _GNU_SOURCE
#include <sys/mount.h>
#include <sys/resource.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <fcntl.h>
#include <unistd.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <signal.h>
#include <sched.h>
static int mode, counterfd=-1, reads, writes, bannerfd=-1;
static ssize_t hook_read(int fd, void *buf, size_t len) {
 if(fd==counterfd) {
  reads++;
  if(mode==1) { if(reads==1) return read(fd,buf,2); errno=EIO; return -1; }
  if(mode==2) { if(reads==1) {errno=EINTR;return -1;} if(len>1)len=1; }
  if(mode==3) {errno=EIO;return -1;}
 }
 return read(fd,buf,len);
}
#include <stdarg.h>
static int hook_open(const char *p,int flags,...) {
 mode_t m=0; if(flags&O_CREAT){va_list a;va_start(a,flags);m=va_arg(a,int);va_end(a);}
 if(!strcmp(p,"/cache/v23_boot_count.txt") && flags==O_RDONLY && mode==4){errno=EACCES;return -1;}
 int fd=open(p,flags,m);
 if(!strcmp(p,"/cache/v23_boot_count.txt") && flags==O_RDONLY)counterfd=fd;
 if(!strcmp(p,"/cache/v23_log.txt") && mode==5)bannerfd=fd;
 return fd;
}
static int hook_close(int fd) {
 int r=close(fd);
 if(fd==counterfd)counterfd=-1;
 if(mode==5 && fd==bannerfd){mode=0;raise(SIGABRT);}
 return r;
}
static ssize_t hook_write(int fd,const void *buf,size_t n) {
 if(mode==6){writes++;if(writes==1){errno=EINTR;return -1;}if(n>3)n=3;}
 if(mode==7){errno=ENOSPC;return -1;}
 return write(fd,buf,n);
}
#define read hook_read
#define open hook_open
#define close hook_close
#define write hook_write
#define main unused_cinit_main
#include "cinit12.snapshot.c"
#undef main
static int cache_sequence(void){
			int boot_n = -1; /* -1 = counter unavailable, logged as such */
			{
				int prev = -1; /* -1 = unknown/untrustworthy, not "reset to 0" */
				int cfd = open("/cache/v23_boot_count.txt", O_RDONLY);
				if (cfd < 0 && errno == ENOENT) {
					prev = 0; /* legitimately the first boot */
				} else if (cfd >= 0) {
					static char cb[32];
					size_t total = 0;
					/* Astra round-3 finding 1 fix: the read loop's `break` on
					 * a real (non-EINTR) read() error is indistinguishable
					 * from a normal EOF `break` to the code after the loop -
					 * both just fall through with whatever bytes happened to
					 * land in `total`. Proven live: a read() that returns 2
					 * valid digit bytes then fails with EIO left `total==2`,
					 * which validated fine as "12" and got trusted as a real
					 * prior count, silently truncating a real "123456" to
					 * "12". Track genuine EOF explicitly and only accept the
					 * buffer's content when the loop ended via EOF, never via
					 * an I/O error. */
					int hit_eof = 0;
					for (;;) {
						if (total >= sizeof(cb) - 1) break; /* buffer full, not an error */
						ssize_t rn = read(cfd, cb + total, sizeof(cb) - 1 - total);
						if (rn < 0) { if (errno == EINTR) continue; break; /* real error - hit_eof stays 0 */ }
						if (rn == 0) { hit_eof = 1; break; }
						total += (size_t)rn;
					}
					close(cfd);
					if (hit_eof && total > 0 && total < sizeof(cb) - 1) {
						size_t dl = 0;
						while (dl < total && cb[dl] >= '0' && cb[dl] <= '9') dl++;
						int trailing_ok = (dl == total) || (dl == total - 1 && cb[dl] == '\n');
						/* Astra round-3 finding 6 fix: the previous 7-digit
						 * cap validated the STORED value's digit count but
						 * not the INCREMENTED candidate - 9999999 (7 digits,
						 * accepted) plus one is 10000000 (8 digits), which
						 * the next boot's read would then reject outright,
						 * permanently wedging the counter one boot after it
						 * silently wrote a value it could no longer parse
						 * back. Reject the increment here, before writing,
						 * if the candidate itself would exceed 7 digits. */
						if (dl > 0 && dl <= 7 && trailing_ok) {
							cb[dl] = 0;
							int parsed = atoi(cb);
							if (parsed < 9999999) prev = parsed;
							/* else: candidate would be 10000000 (8 digits) -
							 * leave prev at -1 (unavailable) rather than
							 * write a value the counter can't represent. */
						}
					}
				}
				if (prev >= 0) {
					int candidate = prev + 1;
					int tfd = open("/cache/v23_boot_count.txt.tmp", O_WRONLY | O_CREAT | O_TRUNC, 0644);
					if (tfd >= 0) {
						char wb[16];
						int wl = snprintf(wb, sizeof(wb), "%d", candidate);
						if (write_full(tfd, wb, (size_t)wl) == 0 && fsync(tfd) == 0) {
							close(tfd);
							if (rename("/cache/v23_boot_count.txt.tmp", "/cache/v23_boot_count.txt") == 0) {
								boot_n = candidate;
							}
						} else {
							close(tfd);
						}
					}
				}
			}
			char banner[96];
			if (boot_n >= 0) {
				snprintf(banner, sizeof(banner), "[v23] ===== BOOT ATTEMPT #%d (V23/cinit12) =====\n", boot_n);
			} else {
				snprintf(banner, sizeof(banner), "[v23] ===== BOOT ATTEMPT #unknown, counter unavailable/untrustworthy (V23/cinit12) =====\n");
			}

			/* Astra round-2 P2 fix (ordering): queuing the banner into
			 * early_log before calling flush_early_log() does NOT make it
			 * appear first - log_line() appends at early_log_n and
			 * flush_early_log() walks forward from index 0, so the banner
			 * (queued last) is flushed LAST, still after every one of this
			 * attempt's own early lines (proven on-device: a harness of the
			 * actual queue-then-flush sequence produced "EARLY FIRST /
			 * EARLY SECOND / ===== BOOT ATTEMPT ====="). Fixed by writing
			 * the banner directly to the log file FIRST, via its own
			 * open+write_full+fsync, bypassing early_log entirely - then
			 * flush_early_log() appends this attempt's buffered pre-mount
			 * lines after it. This achieves true "banner first" at the
			 * actual file level, which queue-then-flush structurally could
			 * not. */
			int banner_ok = 0;
			{
				int fd = open("/cache/v23_log.txt", O_WRONLY | O_CREAT | O_APPEND, 0644);
				if (fd >= 0) {
					if (write_full(fd, banner, strlen(banner)) == 0 && fsync(fd) == 0) banner_ok = 1;
					close(fd);
				}
			}

			/* Astra round-3 finding 2 fix: g_cache_ok was previously only
			 * set after flush_early_log() ALSO succeeded, meaning a fatal
			 * signal delivered during the flush itself (after the banner
			 * was already durably written) found g_cache_ok still 0 and the
			 * fatal handler had nowhere persistent to write - proven live
			 * with a real SIGABRT landing exactly in that window, which
			 * left only the banner in /cache and the fatal explanation
			 * stranded in kmsg only. Fixed by enabling g_cache_ok as soon
			 * as the banner itself is confirmed durable, independent of
			 * whether the early-log flush that follows succeeds - log_line()
			 * already clears g_cache_ok again on its own if a later cache
			 * write actually fails, so this doesn't reintroduce D4's
			 * original problem. */
			if (banner_ok) g_cache_ok = 1;

			/* D4 fix: only claim cache is usable for further logging after
			 * a confirmed create+write succeeds, not merely after mount()
			 * succeeds. Flush attempted regardless of banner_ok - a banner
			 * failure shouldn't also discard this attempt's actual early
			 * evidence lines. */
			int flush_ok = flush_early_log();
			if (flush_ok) {
				g_cache_ok = 1;
				if (banner_ok) {
					logf_uptime("cache mounted OK, banner + early log flushed and confirmed written");
				} else {
					logf_uptime("cache mounted OK, early log flushed but BANNER WRITE FAILED - attempt numbering may be missing for this run");
				}
			} else if (banner_ok) {
				logf_uptime("cache mounted OK, banner written, but flush_early_log() FAILED - early pre-mount lines lost, live logging continues");
			} else {
				logf_uptime("cache mounted but flush_early_log() FAILED - staying on in-memory log only");
			}

 return boot_n;
}
static void shutdown_sequence(void){
	if (g_cache_mounted) {
		logf_uptime("unmounting /cache before reboot");
		sync();
		/* Astra round-2 P3 fix (race): disable cache logging BEFORE
		 * attempting the unmount, not after. Previously a signal delivered
		 * between a successful umount() syscall and the flag-clear line
		 * below still found g_cache_ok==1 and let fatal_sig_handler()
		 * O_CREAT a fresh log file underneath the now-detached ramdisk
		 * tmpfs directory - a write that "succeeds" but goes nowhere
		 * persistent. Proven on-device: Astra delivered SIGABRT immediately
		 * after a real successful umount() syscall and confirmed the
		 * write landed in the underlying ramdisk, not on the unmounted
		 * ext4 device. Disabling here closes that window entirely, since
		 * there is no longer a gap between "umount succeeded" and "logging
		 * disabled" for a signal to land in. */
		g_cache_ok = 0;
		int un = umount("/cache");
		if (un == 0) {
			g_cache_mounted = 0;
		} else if (g_kmsg_fd >= 0) {
			/* Astra round-2 P4 fix: previously BOTH flags were cleared
			 * unconditionally even when umount() FAILED (reproduced live
			 * via a real EBUSY from a busy mountpoint), falsely claiming
			 * the cache was unmounted while it was, in fact, still mounted
			 * and perfectly writable. g_cache_mounted is now only cleared
			 * on a confirmed successful unmount. g_cache_ok stays disabled
			 * either way, by design (see above) - this failure is reported
			 * through kmsg, the only channel this shutdown path still
			 * trusts once it has committed to tearing cache logging down. */
			char kb[100];
			int kn = snprintf(kb, sizeof(kb), "[v23] umount(/cache) FAILED errno=%d, cache remains mounted\n", errno);
			ssize_t w = write(g_kmsg_fd, kb, kn);
			(void)w;
		}
	} else {
		sync();
	}

}
static void early_summary(void){
 int e_dev=17,e_devtmpfs=0,e_devblock=0,e_proc=17,e_sys=17;
 int e_kmsg_n=0,e_null_n=0,e_con_n=0,e_wd_n=0,e_sda33_n=0,e_procmnt=13,e_sysmnt=22;
	{
		char m[100];
		snprintf(m, sizeof(m), "early syscalls A: dev=%d devtmpfs=%d devblock=%d proc=%d sys=%d (0=ok else errno)",
		         e_dev, e_devtmpfs, e_devblock, e_proc, e_sys);
		logf_uptime(m);
	}
	{
		char m[130];
		snprintf(m, sizeof(m), "early syscalls B: kmsg_n=%d null_n=%d con_n=%d wd_n=%d sda33_n=%d procmnt=%d sysmnt=%d (0=ok else errno)",
		         e_kmsg_n, e_null_n, e_con_n, e_wd_n, e_sda33_n, e_procmnt, e_sysmnt);
		logf_uptime(m);
	}

}
static void watchdog_note(void){
		logf_uptime("NOTE: tmr_atboot=1 arms watchdog at probe (1st build to do so)");
		logf_uptime("NOTE: running raw DT timeout, NOT stock watchdogd's 30s SETTIMEOUT");

}
static void options_write(void){
		int fd = open("/etc/modules/s3c2410_wdt", O_WRONLY | O_CREAT | O_TRUNC, 0644);
		int wrote_ok = 0;
		if (fd >= 0) {
			wrote_ok = (write_full(fd, "tmr_atboot=1", 12) == 0);
			close(fd);
		}
		if (wrote_ok) {
			logf_uptime("wrote /etc/modules/s3c2410_wdt: tmr_atboot=1 (match stock bootloader policy)");
		} else {
			logf_uptime("FAILED to write /etc/modules/s3c2410_wdt options file - watchdog will probe with compiled default tmr_atboot=0, diverging from stock");
		}

}
#undef read
#undef open
#undef close
#undef write
static void put(const char*p,const char*s){int fd=open(p,O_CREAT|O_TRUNC|O_WRONLY,0600);if(fd<0){perror(p);exit(2);}write(fd,s,strlen(s));close(fd);}
static void show(const char*p){char b[8192];int fd=open(p,O_RDONLY);if(fd<0){printf("%s=<absent> errno=%d\n",p,errno);return;}int n=read(fd,b,sizeof(b)-1);close(fd);if(n<0){perror(p);return;}b[n]=0;printf("%s bytes=%d {%s}\n",p,n,b);}
static void reset(void){mode=0;reads=writes=0;counterfd=bannerfd=-1;g_cache_ok=0;early_log_n=0;unlink("/cache/v23_boot_count.txt");unlink("/cache/v23_boot_count.txt.tmp");unlink("/cache/v23_log.txt");ftruncate(g_kmsg_fd,0);lseek(g_kmsg_fd,0,SEEK_SET);}
static void child_status(pid_t p){int s;waitpid(p,&s,0);printf("child exit=%d signal=%d\n",WIFEXITED(s)?WEXITSTATUS(s):-1,WIFSIGNALED(s)?WTERMSIG(s):0);}
int main(int argc,char**argv){
 setbuf(stdout,NULL);
 if(argc!=2)return 2;
 if(unshare(CLONE_NEWNS)<0 || mount(NULL,"/",NULL,MS_REC|MS_PRIVATE,NULL)<0){perror("namespace");return 2;}
 mkdir(argv[1],0700);if(chdir(argv[1])<0||chroot(".")<0){perror("chroot");return 2;}
 mkdir("/cache",0700);mkdir("/etc",0700);mkdir("/etc/modules",0700);
 g_kmsg_fd=open("/kmsg",O_CREAT|O_TRUNC|O_RDWR,0600);
 signal(SIGABRT,fatal_sig_handler);
 const char*values[]={NULL,"42","42\n","","1x","1\n2","2147483647","9999998","9999999","10000000","123456","123456","123456","123456"};
 int modes[]={0,0,0,0,0,0,0,0,0,0,1,2,3,4};
 for(unsigned i=0;i<sizeof(modes)/sizeof(*modes);i++){
  reset();if(values[i])put("/cache/v23_boot_count.txt",values[i]);mode=modes[i];
  int b=cache_sequence();mode=0;printf("COUNTER input=%s fault=%d boot=%d reads=%d ",values[i]?values[i]:"ENOENT",modes[i],b,reads);show("/cache/v23_boot_count.txt");
 }
 reset();log_line("EARLY FIRST\n");log_line("EARLY SECOND\n");cache_sequence();printf("ORDER normal\n");show("/cache/v23_log.txt");
 reset();log_line("EARLY FIRST\n");log_line("EARLY SECOND\n");pid_t p=fork();if(!p){mode=5;cache_sequence();_exit(0);}child_status(p);printf("ORDER signal after banner close, before flush\n");show("/cache/v23_log.txt");show("/kmsg");
 reset();early_summary();watchdog_note();log_line("NEXT RECORD\n");cache_sequence();printf("NEW DIAGNOSTIC LINES\n");show("/cache/v23_log.txt");show("/kmsg");
 reset();p=fork();if(!p){g_cache_ok=1;mode=6;raise(SIGABRT);_exit(0);}child_status(p);printf("HANDLER EINTR plus short writes\n");show("/cache/v23_log.txt");show("/kmsg");
 reset();p=fork();if(!p){struct rlimit rl={3,3};setrlimit(RLIMIT_FSIZE,&rl);g_cache_ok=1;raise(SIGABRT);_exit(0);}child_status(p);printf("HANDLER real RLIMIT_FSIZE\n");show("/cache/v23_log.txt");show("/kmsg");
 reset();mode=7;options_write();mode=0;printf("OPTIONS failed write\n");show("/etc/modules/s3c2410_wdt");show("/kmsg");printf("queued=%s\n",early_log[0]);
 reset();if(mount("tmpfs","/cache","tmpfs",0,"size=1m")<0){perror("mount cache");return 2;}g_cache_mounted=1;g_cache_ok=1;int held=open("/cache",O_DIRECTORY|O_RDONLY);shutdown_sequence();printf("BUSY flags mounted=%d ok=%d\n",g_cache_mounted,g_cache_ok);show("/cache/v23_log.txt");show("/kmsg");close(held);shutdown_sequence();printf("SUCCESS flags mounted=%d ok=%d\n",g_cache_mounted,g_cache_ok);unlink("/cache/v23_log.txt");p=fork();if(!p){raise(SIGABRT);_exit(0);}child_status(p);printf("AFTER UNMOUNT underlying\n");show("/cache/v23_log.txt");
 return 0;
}
