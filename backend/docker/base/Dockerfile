FROM ubuntu:24.04
SHELL ["/bin/bash", "-o", "pipefail", "-c"]

ENV WORKSPACE_BASE=/home \
    AGENT_DIR=/home/agent \
    AUDIT_DIR=/home/agent/audit \
    SUBMISSION_DIR=/home/agent/submission \
    LOGS_DIR=/home/logs

ENV PATH=$AGENT_DIR/.opencode/bin:$AGENT_DIR/.local/bin:/root/.local/bin:$PATH
ENV HOME=$AGENT_DIR

ENV DEBIAN_FRONTEND=noninteractive
ENV PIP_BREAK_SYSTEM_PACKAGES=1
RUN find /usr/lib -name EXTERNALLY-MANAGED -delete

# dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    git curl unzip ca-certificates gnupg \
    zip unzip git nano vim jq bc lsof xxd \
    build-essential pkg-config libssl-dev \
    linux-libc-dev libstdc++6 \
    python3 python3-pip python3-venv python-is-python3 \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && corepack enable \
    && rm -rf /var/lib/apt/lists/*

# setup folders
RUN mkdir -p "$AUDIT_DIR" "$SUBMISSION_DIR" "$LOGS_DIR"

# install opencode
ARG TARGETARCH
RUN mkdir -p "$AGENT_DIR/.opencode/bin" && \
    curl -fsSL https://opencode.ai/install | bash -s -- --no-modify-path && \
    "$AGENT_DIR/.opencode/bin/opencode" --version

# install opencode x402 plugin
RUN npm install -g @lucid-agents/opencode-x402-plugin

# install ripgrep
RUN apt-get update && apt-get install -y --no-install-recommends ripgrep && rm -rf /var/lib/apt/lists/*

WORKDIR "$AGENT_DIR"

# git config
RUN git config --global --add safe.directory $AGENT_DIR && \
    git config --global --add safe.directory $AUDIT_DIR && \
    git config --global user.email "agent@svmbench.local" && \
    git config --global user.name "agent"
