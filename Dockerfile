ARG NODE_VERSION=24

FROM node:${NODE_VERSION}-alpine

ARG CODE_SERVER_VERSION=4.138.0

ENV PYTHON=/usr/bin/python3 \
    GOBIN=/usr/local/bin \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/root/.bun/bin:${PATH}"

# =============================================================================
# BASE SYSTEM
# =============================================================================

RUN apk add --no-cache \
        bash \
        bash-completion \
        bind-tools \
        bubblewrap \
        ca-certificates \
        curl \
        docker-cli \
        docker-cli-compose \
        fail2ban \
        fd \
        file \
        git \
        go \
        grep \
        iproute2 \
        iputils \
        jq \
        libc6-compat \
        less \
        libstdc++ \
        lsof \
        nano \
        ncdu \
        nginx \
        netcat-openbsd \
        openssl \
        openssh-client \
        postgresql-client \
        procps \
        psmisc \
        python3 \
        py3-pip \
        ripgrep \
        rsync \
        shellcheck \
        sqlite \
        strace \
        tree \
        tzdata \
        unzip \
        vim \
        wget \
        yq \
        zip \
    && ln -sf /usr/bin/python3 /usr/local/bin/python \
    && ln -sf /usr/bin/fd /usr/local/bin/fdfind \
    && printf '%s\n' \
        '#!/usr/bin/env sh' \
        'exec docker compose "$@"' \
        > /usr/local/bin/docker-compose \
    && chmod +x /usr/local/bin/docker-compose \
    && rm -rf \
        /var/cache/apk/* \
        /tmp/* \
        /var/tmp/*

# =============================================================================
# CODE-SERVER
#
# Alpine = musl.
# Installation via npm afin de compiler les dépendances natives nécessaires.
#
# Version volontairement figée pour garantir un build reproductible.
# =============================================================================

RUN apk add --no-cache --virtual .code-server-build-deps \
        alpine-sdk \
        krb5-dev \
    \
    && echo "============================================================" \
    && echo " Installing code-server ${CODE_SERVER_VERSION}" \
    && echo "============================================================" \
    \
    && npm install -g \
        "code-server@${CODE_SERVER_VERSION}" \
    \
    && echo \
    && echo "=== code-server ===" \
    && code-server --version \
    \
    && echo \
    && echo "=== Node ===" \
    && node --version \
    \
    && echo \
    && echo "=== npm ===" \
    && npm --version \
    \
    && echo \
    && echo "=== Checking code-server installation ===" \
    && test -f /usr/local/lib/node_modules/code-server/package.json \
    && test -d /usr/local/lib/node_modules/code-server/lib/vscode \
    \
    && echo \
    && echo "=== code-server size ===" \
    && du -sh /usr/local/lib/node_modules/code-server \
    && du -sh /usr/local/lib/node_modules/code-server/lib/vscode \
    \
    && echo \
    && echo "=== GitHub authentication extension ===" \
    && find \
        /usr/local/lib/node_modules/code-server/lib/vscode/extensions/github-authentication \
        -maxdepth 5 \
        -type f \
        -print \
        2>/dev/null \
        | sort \
        || true \
    \
    && echo \
    && echo "=== VSDA ===" \
    && find \
        /usr/local/lib/node_modules/code-server/lib/vscode/node_modules/vsda \
        -maxdepth 7 \
        -type f \
        -print \
        2>/dev/null \
        | sort \
        || true \
    \
    && echo \
    && echo "=== Removing build dependencies ===" \
    && apk del .code-server-build-deps \
    \
    && npm cache clean --force \
    \
    && rm -rf \
        /root/.npm \
        /root/.cache \
        /tmp/* \
        /var/tmp/*

# =============================================================================
# BUN
# =============================================================================

RUN echo "============================================================" \
    && echo " Installing Bun" \
    && echo "============================================================" \
    \
    && curl -fsSL https://bun.sh/install \
        | BUN_INSTALL=/usr/local bash \
    \
    && echo \
    && echo "=== Bun ===" \
    && bun --version \
    \
    && bun add -g \
        hono \
        create-hono \
        postgres \
    \
    && ln -sf \
        /root/.bun/bin/create-hono \
        /usr/local/bin/create-hono \
    \
    && rm -rf \
        /root/.bun/install/cache \
        /root/.cache \
        /tmp/* \
        /var/tmp/*

# =============================================================================
# OPENAI CODEX
# =============================================================================

RUN echo "============================================================" \
    && echo " Installing OpenAI Codex" \
    && echo "============================================================" \
    \
    && npm install -g \
        @openai/codex@latest \
    \
    && echo \
    && echo "=== Codex ===" \
    && codex --version \
    \
    && npm cache clean --force \
    \
    && rm -rf \
        /root/.npm \
        /root/.cache \
        /tmp/* \
        /var/tmp/*

# =============================================================================
# OVHCLOUD AI AGENT
# =============================================================================

RUN python3 -m venv /opt/ovh-agent \
    && /opt/ovh-agent/bin/pip install --no-cache-dir \
        'pydantic-ai-slim[openai]==2.56.0'

# =============================================================================
# ENTRYPOINT
# =============================================================================

COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

RUN chmod 755 /usr/local/bin/docker-entrypoint.sh

# =============================================================================
# FINAL VALIDATION
# =============================================================================

RUN echo "============================================================" \
    && echo " TRINITY code-server image" \
    && echo "============================================================" \
    && printf 'Node        : ' \
    && node --version \
    && printf 'code-server : ' \
    && code-server --version \
    && printf 'Bun         : ' \
    && bun --version \
    && printf 'Codex       : ' \
    && codex --version \
    && echo "============================================================"

# =============================================================================
# RUNTIME
# =============================================================================

EXPOSE 8080

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]

CMD ["--bind-addr", "0.0.0.0:8080", "--disable-telemetry", "--disable-update-check"]
