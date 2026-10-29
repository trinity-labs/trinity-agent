ARG NODE_IMAGE=node:24-alpine
ARG GO_IMAGE=golang:1.27.3-alpine
FROM ${GO_IMAGE} AS go-toolchain
FROM ${NODE_IMAGE}

COPY --from=go-toolchain /usr/local/go /usr/local/go

ENV PYTHON=/usr/bin/python3 \
    GOBIN=/usr/local/bin \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/usr/local/go/bin:/root/.bun/bin:${PATH}"

# =============================================================================
# BASE SYSTEM
# =============================================================================

RUN apk upgrade --no-cache \
    && apk add --no-cache \
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
        gcc \
        git \
        grep \
        iproute2 \
        iputils \
        jq \
        libc6-compat \
        less \
        libstdc++ \
        lsof \
        musl-dev \
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
# =============================================================================

RUN apk add --no-cache --virtual .code-server-build-deps \
        alpine-sdk \
        krb5-dev \
    \
    && echo "============================================================" \
    && echo " Installing code-server latest" \
    && echo "============================================================" \
    \
    && npm install -g \
        "code-server@latest" \
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
    && /opt/ovh-agent/bin/pip install --upgrade --upgrade-strategy eager \
        'pydantic-ai-slim[openai]'

# =============================================================================
# ENTRYPOINT
# =============================================================================

COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

RUN chmod 755 /usr/local/bin/docker-entrypoint.sh

# =============================================================================
# FINAL VALIDATION
# =============================================================================

RUN apk add --no-cache docker-cli-buildx \
    && ln -sf /usr/local/go/bin/go /usr/local/bin/go \
    && ln -sf /usr/local/go/bin/gofmt /usr/local/bin/gofmt \
    && npm install -g npm@12 \
    && npm cache clean --force \
    && rm -rf /root/.npm \
    && echo "============================================================" \
    && echo " TRINITY code-server image" \
    && echo "============================================================" \
    && printf 'Node        : ' \
    && node --version \
    && printf 'npm         : ' \
    && npm --version \
    && printf 'Go          : ' \
    && go version \
    && printf 'code-server : ' \
    && code-server --version \
    && printf 'Bun         : ' \
    && bun --version \
    && printf 'Codex       : ' \
    && codex --version \
    && /opt/ovh-agent/bin/pip check \
    && docker buildx version \
    && echo "============================================================"

# =============================================================================
# RUNTIME
# =============================================================================

EXPOSE 8080

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]

CMD ["--bind-addr", "0.0.0.0:8080", "--disable-telemetry", "--disable-update-check"]
