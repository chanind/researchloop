FROM kgutwin/simple-sge

# Install SSH server and Python 3 (needed for job scripts).
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        openssh-server python3 curl && \
    rm -rf /var/lib/apt/lists/* && \
    mkdir -p /run/sshd

# Configure sshd: allow root login, key auth, high MaxAuthTries.
RUN sed -i 's/#PermitRootLogin.*/PermitRootLogin yes/' /etc/ssh/sshd_config && \
    sed -i 's/#PubkeyAuthentication.*/PubkeyAuthentication yes/' /etc/ssh/sshd_config && \
    sed -i 's/#MaxAuthTries.*/MaxAuthTries 20/' /etc/ssh/sshd_config

# Install mock claude CLI.
COPY mock_claude.sh /usr/local/bin/claude
RUN chmod +x /usr/local/bin/claude

# Copy entrypoint that starts SGE + sshd.
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 22

ENTRYPOINT ["/entrypoint.sh"]
