FROM nathanhess/slurm:full-root

# Install SSH server.
RUN apt-get update && \
    apt-get install -y --no-install-recommends openssh-server && \
    rm -rf /var/lib/apt/lists/* && \
    mkdir -p /run/sshd

# Configure sshd for key-based auth with high MaxAuthTries
# (asyncssh tries SSH agent keys before client_keys, which can exhaust attempts).
RUN sed -i 's/#PermitRootLogin.*/PermitRootLogin yes/' /etc/ssh/sshd_config && \
    sed -i 's/#PubkeyAuthentication.*/PubkeyAuthentication yes/' /etc/ssh/sshd_config && \
    sed -i 's/#MaxAuthTries.*/MaxAuthTries 20/' /etc/ssh/sshd_config

# Keep completed jobs visible to scontrol for 10 minutes.
RUN echo "MinJobAge=600" >> /etc/slurm/slurm.conf

# Install mock claude CLI.
COPY mock_claude.sh /usr/local/bin/claude
RUN chmod +x /usr/local/bin/claude

# Copy startup wrapper that runs SLURM setup then starts sshd.
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 22

CMD ["/entrypoint.sh"]
