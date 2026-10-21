#!/bin/sh
# NPU-only stock models (no DSP_BINARY section). DSP models crash the phone (DNC page fault -> WDT).
cd /srv/s22/npu-trial
out=/srv/s22/npu-trial/sweep2.log; : > $out
for m in $(ls -S -r /srv/s22/enn-rt/data/models/stock); do
  if grep -q DSP_BINARY /srv/s22/enn-rt/data/models/stock/$m; then echo "=== $m SKIPPED (DSP_BINARY)" >> $out; continue; fi
  echo "=== $m" >> $out
  sync
  ENN_BIN=/bin/enn-run timeout -s KILL 120 ./enn-chroot-run /data/models/stock/$m 30 2>&1 | grep -v linker >> $out
  echo "rc=$? npu=$(cat /sys/class/devfreq/17000060.devfreq_npu/cur_freq)" >> $out
  dmesg | grep -v sec_input | tail -40 | grep -iE "fault|error|fail" | tail -3 >> $out
  sync
done
echo SWEEP-DONE >> $out
