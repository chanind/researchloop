#!/bin/bash
set -e

# Install SSH public key if mounted.
if [ -f /tmp/test_key.pub ]; then
    # Set up for both root and sgeuser.
    for homedir in /root /home/sgeuser; do
        mkdir -p "$homedir/.ssh"
        cp /tmp/test_key.pub "$homedir/.ssh/authorized_keys"
        chmod 700 "$homedir/.ssh"
        chmod 600 "$homedir/.ssh/authorized_keys"
    done
    chown -R sgeuser:sgeuser /home/sgeuser/.ssh 2>/dev/null || true
fi

# Generate SSH host keys if missing.
ssh-keygen -A 2>/dev/null || true

# Create a non-root user for SGE job submission.
# SGE blocks root by default (min_uid=100).
id sgeuser >/dev/null 2>&1 || useradd -m -s /bin/bash sgeuser

# Run the original SGE boot script.
# Pass "true" so it runs `exec true` instead of `exec bash`.
/root/boot-sge.sh true

# Source SGE settings.
. /etc/profile.d/sge_settings.sh

echo "SGE ready."

# Make SGE commands available via SSH sessions.
echo "PermitUserEnvironment yes" >> /etc/ssh/sshd_config

# Set environment for sgeuser (the SSH user for tests).
mkdir -p /home/sgeuser/.ssh
cat > /home/sgeuser/.ssh/environment << ENVEOF
SGE_ROOT=$SGE_ROOT
SGE_CELL=$SGE_CELL
SGE_CLUSTER_NAME=$SGE_CLUSTER_NAME
SGE_QMASTER_PORT=$SGE_QMASTER_PORT
SGE_EXECD_PORT=$SGE_EXECD_PORT
PATH=$PATH
ENVEOF
chmod 600 /home/sgeuser/.ssh/environment
chown -R sgeuser:sgeuser /home/sgeuser/.ssh

# Also set for root (for fallback).
cat > /root/.ssh/environment << ENVEOF
SGE_ROOT=$SGE_ROOT
SGE_CELL=$SGE_CELL
SGE_CLUSTER_NAME=$SGE_CLUSTER_NAME
SGE_QMASTER_PORT=$SGE_QMASTER_PORT
SGE_EXECD_PORT=$SGE_EXECD_PORT
PATH=$PATH
ENVEOF
chmod 600 /root/.ssh/environment

# Give sgeuser write access to the test directory.
mkdir -p /tmp/researchloop
chown sgeuser:sgeuser /tmp/researchloop

echo "Starting sshd..."
exec /usr/sbin/sshd -D -e
