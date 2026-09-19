#define _GNU_SOURCE
#include <sys/ptrace.h>
#include <sys/prctl.h>
#include <sys/wait.h>
#include <sys/uio.h>
#include <linux/seccomp.h>
#include <linux/filter.h>
#include <asm/ptrace.h>
#include <elf.h>
#include <stddef.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <signal.h>
#include <string.h>
static void deny_modules(void){
 struct sock_filter f[]={BPF_STMT(BPF_LD|BPF_W|BPF_ABS,offsetof(struct seccomp_data,nr)),BPF_JUMP(BPF_JMP|BPF_JEQ|BPF_K,105,2,0),BPF_JUMP(BPF_JMP|BPF_JEQ|BPF_K,273,1,0),BPF_STMT(BPF_RET|BPF_K,SECCOMP_RET_ALLOW),BPF_STMT(BPF_RET|BPF_K,SECCOMP_RET_ERRNO|EPERM)};
 struct sock_fprog p={sizeof(f)/sizeof(*f),f};if(prctl(PR_SET_NO_NEW_PRIVS,1,0,0,0)||prctl(PR_SET_SECCOMP,SECCOMP_MODE_FILTER,&p))_exit(90);
}
int main(int argc,char**argv){
 if(argc!=2)return 2;setbuf(stdout,NULL);pid_t p=fork();if(!p){if(chdir(argv[1])||chroot("."))_exit(91);deny_modules();if(ptrace(PTRACE_TRACEME,0,0,0))_exit(92);raise(SIGSTOP);execl("/busybox","/busybox","modprobe","s3c2410_wdt",NULL);_exit(93);}
 int s;waitpid(p,&s,0);if(!WIFSTOPPED(s))return 3;ptrace(PTRACE_SETOPTIONS,p,0,PTRACE_O_TRACESYSGOOD|PTRACE_O_EXITKILL);int entering=1;
 for(;;){if(ptrace(PTRACE_SYSCALL,p,0,0))return 4;waitpid(p,&s,0);if(WIFEXITED(s)||WIFSIGNALED(s)){printf("busybox exit=%d signal=%d\n",WIFEXITED(s)?WEXITSTATUS(s):-1,WIFSIGNALED(s)?WTERMSIG(s):0);break;}if(WSTOPSIG(s)!=(SIGTRAP|0x80))continue;
 struct user_pt_regs r;struct iovec i={&r,sizeof(r)};if(ptrace(PTRACE_GETREGSET,p,(void*)NT_PRSTATUS,&i))return 5;
 if(entering&&(r.regs[8]==105||r.regs[8]==273)){
 unsigned long addr=r.regs[r.regs[8]==273?1:2];char b[256]={0};for(int j=0;j<248;j+=sizeof(long)){errno=0;long v=ptrace(PTRACE_PEEKDATA,p,(void*)(addr+j),0);if(errno)break;memcpy(b+j,&v,sizeof(v));if(memchr(&v,0,sizeof(v)))break;}
 printf("BLOCKED module syscall=%lu options={%s} (seccomp EPERM, never loaded)\n",(unsigned long)r.regs[8],b);
 }entering=!entering;
 }return 0;
}
