#!/bin/sh
# Give programs in the Arch chroot (OpenUnum's shell tool) access to the
# phone's hardware commands on the Alpine host root. The host root is reached
# through /proc/<sshd>/root (PID 1 is native-guardian in the ramdisk root, not Alpine).
set -eu
C=/mnt/omarchy-trial
cat > "$C/usr/local/bin/s22-host" <<'EOS'
#!/bin/bash
# s22-host CMD...: run CMD on the S22's Alpine host root (outside the chroot).
P=$(pgrep -xo sshd) || { echo "s22-host: sshd not found" >&2; exit 1; }
exec chroot "/proc/$P/root" /usr/bin/env PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin "$@"
EOS
chmod 755 "$C/usr/local/bin/s22-host"
for t in s22-say s22-rec s22-modem s22-modem-up s22-display; do
  printf '#!/bin/sh\nexec /usr/local/bin/s22-host %s "$@"\n' "$t" > "$C/usr/local/bin/$t"
  chmod 755 "$C/usr/local/bin/$t"
done
mkdir -p "$C/root/.openunum/workspace"
echo "wrappers installed"
