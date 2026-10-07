FROM ubuntu:24.04

# Prevent interactive prompts during software installation
ENV DEBIAN_FRONTEND=noninteractive

# Install essential dev tools, runtimes, and libraries for coding agents
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    git \
    jq \
    build-essential \
    ca-certificates \
    python3 \
    python3-pip \
    python3-venv \
    nodejs \
    npm \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace
CMD ["/bin/bash"]