#!/bin/bash
set -e

# Install SSH public key if mounted.
if [ -f /tmp/test_key.pub ]; then
    mkdir -p /root/.ssh
    cp /tmp/test_key.pub /root/.ssh/authorized_keys
    chmod 700 /root/.ssh
    chmod 600 /root/.ssh/authorized_keys
fi

# Generate SSH host keys if missing.
ssh-keygen -A 2>/dev/null || true

# Run the SLURM startup script (configures slurm.conf, starts munge/slurmd/slurmctld).
bash /etc/startup.sh

echo "SLURM ready. Starting sshd..."

# Start sshd in foreground.
exec /usr/sbin/sshd -D -e
