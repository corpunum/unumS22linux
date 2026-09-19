
#define _GNU_SOURCE
#include <sys/mount.h>
#include <sys/resource.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <sys/sysmacros.h>
#include <fcntl.h>
#include <unistd.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <signal.h>
#include <sched.h>
#include <stdarg.h>
static int mode, logopens, cmdfd=-1, reads, flushfd=-1, writes;
static int hook_open(const char*p,int f,...){
 mode_t m=0;if(f&O_CREAT){va_list a;va_start(a,f);m=va_arg(a,int);va_end(a);}
 int fd=open(p,f,m);
 if(!strcmp(p,"/proc/cmdline")){cmdfd=fd;reads=0;}
 if(!strcmp(p,"/cache/v23_log.txt")){
  logopens++;
  if(logopens==2){flushfd=fd;if(mode==30){mode=0;raise(SIGABRT);}}
 }
 return fd;
}
static ssize_t hook_read(int fd,void*b,size_t n){
 if(fd==cmdfd&&mode==40){if(!reads++){return read(fd,b,n>12?12:n);}errno=EIO;return -1;}
 if(fd==cmdfd&&mode==41){errno=EIO;return -1;}
 return read(fd,b,n);
}
static ssize_t hook_write(int fd,const void*b,size_t n){
 if(mode==32&&fd==flushfd){mode=0;errno=ENOSPC;return -1;}
 return write(fd,b,n);
}
static int hook_close(int fd){int r=close(fd);if(mode==33&&logopens==1){mode=0;raise(SIGABRT);}return r;}
static pid_t hook_fork(void){pid_t p=fork();if(!p&&mode==21){mode=0;*(volatile int *)0=1;}return p;}
static __sighandler_t hook_signal(int s,__sighandler_t h){
 __sighandler_t r=signal(s,h);
 if(mode==22&&s==SIGSEGV&&h==SIG_DFL){mode=0;raise(SIGABRT);}
 return r;
}
static int hook_execl(const char*p,const char*a,...){(void)p;(void)a;if(mode==20){*(volatile int *)0=1;}errno=ENOENT;return -1;}
#define open hook_open
#define read hook_read
#define write hook_write
#define close hook_close
#define fork hook_fork
#define signal hook_signal
#define execl hook_execl
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
static void cmdline_read(void){
	{
		int fd = open("/proc/cmdline", O_RDONLY);
		if (fd >= 0) {
			static char cbuf[4096];
			size_t total = 0;
			int truncated = 0;
			for (;;) {
				if (total >= sizeof(cbuf) - 1) { truncated = 1; break; }
				ssize_t n = read(fd, cbuf + total, sizeof(cbuf) - 1 - total);
				if (n < 0) { if (errno == EINTR) continue; break; }
				if (n == 0) break;
				total += (size_t)n;
			}
			close(fd);
			cbuf[total] = 0;
			log_cmdline_chunked(cbuf, total, truncated);
		} else {
			logf_uptime("open /proc/cmdline FAILED");
		}
	}

}
static void early_summary(void){
 int e_dev=133,e_devtmpfs=133,e_devblock=133,e_proc=133,e_sys=133;
 int e_kmsg_n=133,e_null_n=133,e_con_n=133,e_wd_n=133,e_sda33_n=133,e_procmnt=133,e_sysmnt=133;
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
#undef open
#undef read
#undef write
#undef close
#undef fork
#undef signal
#undef execl
static void put(const char*p,const char*s){int f=open(p,O_CREAT|O_TRUNC|O_WRONLY,0600);if(f<0||write(f,s,strlen(s))!=(ssize_t)strlen(s)){perror("put");exit(2);}close(f);}
static void show(const char*p){char b[8192];int f=open(p,O_RDONLY);if(f<0){printf("%s absent errno=%d\n",p,errno);return;}int n=read(f,b,sizeof(b)-1);close(f);if(n<0){perror("show");return;}b[n]=0;printf("%s bytes=%d {%s}\n",p,n,b);}
static void reset(void){mode=logopens=reads=writes=0;cmdfd=flushfd=-1;g_cache_ok=0;early_log_n=0;unlink("/cache/v23_boot_count.txt");unlink("/cache/v23_boot_count.txt.tmp");unlink("/cache/v23_log.txt");if(ftruncate(g_kmsg_fd,0)||lseek(g_kmsg_fd,0,SEEK_SET)<0)exit(2);}
static void status(pid_t p){int s;if(waitpid(p,&s,0)!=p)exit(2);printf("child exit=%d signal=%d\n",WIFEXITED(s)?WEXITSTATUS(s):-1,WIFSIGNALED(s)?WTERMSIG(s):0);}
int main(int argc,char**argv){
 setbuf(stdout,NULL);if(argc!=2)return 2;
 if(unshare(CLONE_NEWNS)||mount(NULL,"/",NULL,MS_REC|MS_PRIVATE,NULL)){perror("namespace");return 2;}
 if(mkdir(argv[1],0700)||chdir(argv[1])||chroot(".")){perror("fixture");return 2;}
 mkdir("/cache",0700);mkdir("/dev",0700);mkdir("/etc",0700);mkdir("/etc/modules",0700);mkdir("/proc",0700);
 put("/dev/null","");g_kmsg_fd=open("/kmsg",O_CREAT|O_TRUNC|O_RDWR,0600);
 struct rlimit core={0,0};setrlimit(RLIMIT_CORE,&core);
 int sigs[]={SIGSEGV,SIGBUS,SIGILL,SIGABRT,SIGFPE};for(int i=0;i<5;i++)signal(sigs[i],fatal_sig_handler);
 for(int k=20;k<=22;k++){reset();g_cache_ok=1;mode=k;printf("MODPROBE injection=%d (20=exec 21=fork-return 22=after-first-reset)\n",k);modprobe("test-module");mode=0;log_line("PARENT STILL ALIVE\n");show("/cache/v23_log.txt");}
 int modes[]={30,32,33};
 for(int i=0;i<3;i++){reset();log_line("EARLY FIRST\n");log_line("EARLY SECOND\n");printf("CACHE mode=%d (30=flush-open-signal 32=flush-write-failure 33=banner-close-signal)\n",modes[i]);pid_t p=fork();if(!p){mode=modes[i];cache_sequence();_exit(0);}status(p);show("/cache/v23_log.txt");show("/kmsg");}
 reset();printf("OPTIONS real /dev/full ENOSPC\n");if(mknod("/etc/modules/s3c2410_wdt",S_IFCHR|0600,makedev(1,7)))return 2;options_write();show("/kmsg");unlink("/etc/modules/s3c2410_wdt");
 for(int lim=10;lim<=12;lim++){reset();printf("OPTIONS real RLIMIT_FSIZE=%d\n",lim);pid_t p=fork();if(!p){g_kmsg_fd=-1;signal(SIGXFSZ,SIG_IGN);struct rlimit r={lim,lim};if(setrlimit(RLIMIT_FSIZE,&r))_exit(2);options_write();for(int i=0;i<early_log_n;i++)printf("queue len=%zu {%s}\n",strlen(early_log[i]),early_log[i]);_exit(0);}status(p);show("/etc/modules/s3c2410_wdt");}
 for(int k=40;k<=41;k++){reset();put("/proc/cmdline","console=none sec_debug_reset_reason.reset_reason=7 tail=1\n");mode=k;cmdline_read();mode=0;printf("CMDLINE mode=%d reads=%d queued=%d\n",k,reads,early_log_n);show("/kmsg");}
 reset();early_summary();for(int i=0;i<early_log_n;i++)printf("MAX ERRNO summary[%d] len=%zu newline=%d {%s}\n",i,strlen(early_log[i]),early_log[i][strlen(early_log[i])-1]=='\n',early_log[i]);
 for(int i=0;i<5;i++){reset();pid_t p=fork();if(!p){g_cache_ok=1;raise(sigs[i]);_exit(0);}status(p);show("/cache/v23_log.txt");}
 return 0;
}
