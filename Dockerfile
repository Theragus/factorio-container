# syntax=docker/dockerfile:1
#
# Factorio headless (dedicated server) container image.
#
# Only the Linux headless build is packaged: it is the only Factorio release
# that runs without a graphical stack, and therefore the only one that makes
# sense inside a container.
#
# Build with:
#   docker build --build-arg FACTORIO_VERSION=2.0.77 \
#                --build-arg FACTORIO_SHA256=<sha256 of the tarball> .

ARG DEBIAN_IMAGE=debian:12-slim

# ---------------------------------------------------------------------------
# Stage 1 - download and unpack the official headless tarball.
# ---------------------------------------------------------------------------
FROM ${DEBIAN_IMAGE} AS fetcher

ARG FACTORIO_VERSION
ARG FACTORIO_SHA256=""
ARG FACTORIO_DOWNLOAD_URL=""

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

RUN apt-get update \
    && apt-get install --no-install-recommends -y ca-certificates curl xz-utils \
    && rm -rf /var/lib/apt/lists/*

RUN set -euo pipefail; \
    if [[ -z "${FACTORIO_VERSION}" ]]; then \
        echo "FACTORIO_VERSION build argument is required" >&2; exit 1; \
    fi; \
    url="${FACTORIO_DOWNLOAD_URL:-https://www.factorio.com/get-download/${FACTORIO_VERSION}/headless/linux64}"; \
    echo "Downloading ${url}"; \
    curl --fail --silent --show-error --location --retry 5 --retry-delay 5 \
         --output /tmp/factorio.tar.xz "${url}"; \
    if [[ -n "${FACTORIO_SHA256}" ]]; then \
        echo "${FACTORIO_SHA256}  /tmp/factorio.tar.xz" | sha256sum --check --strict -; \
    else \
        echo "WARNING: no FACTORIO_SHA256 supplied, skipping checksum verification" >&2; \
    fi; \
    mkdir -p /opt; \
    tar -xJf /tmp/factorio.tar.xz -C /opt; \
    rm -f /tmp/factorio.tar.xz; \
    test -x /opt/factorio/bin/x64/factorio

# The example configs live in the image so the entrypoint can seed an empty
# volume with them without reaching out to the network.
RUN set -euo pipefail; \
    mkdir -p /opt/factorio/doc; \
    cp /opt/factorio/data/*.example.json /opt/factorio/doc/; \
    rm -rf /opt/factorio/config /opt/factorio/saves /opt/factorio/mods

# ---------------------------------------------------------------------------
# Stage 2 - runtime.
# ---------------------------------------------------------------------------
FROM ${DEBIAN_IMAGE} AS runtime

ARG FACTORIO_VERSION
ARG BUILD_DATE=""
ARG VCS_REF=""

LABEL org.opencontainers.image.title="Factorio headless server" \
      org.opencontainers.image.description="Automatically packaged Factorio headless (dedicated server) release" \
      org.opencontainers.image.version="${FACTORIO_VERSION}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.source="https://github.com/Theragus/factorio-container" \
      org.opencontainers.image.url="https://github.com/Theragus/factorio-container" \
      org.opencontainers.image.vendor="Theragus" \
      com.factorio.version="${FACTORIO_VERSION}"

ENV FACTORIO_VERSION=${FACTORIO_VERSION} \
    FACTORIO_HOME=/factorio \
    PORT=34197 \
    RCON_PORT=27015 \
    SAVE_NAME=default \
    GENERATE_NEW_SAVE=true \
    LOAD_LATEST_SAVE=true \
    PUID=845 \
    PGID=845 \
    DEBIAN_FRONTEND=noninteractive

# ca-certificates: the server talks to auth.factorio.com over TLS.
# gosu: drop from root to the unprivileged factorio user after fixing volume
# ownership (a plain USER instruction cannot do that for bind mounts).
RUN set -eux; \
    apt-get update; \
    apt-get install --no-install-recommends -y ca-certificates gosu; \
    rm -rf /var/lib/apt/lists/*; \
    groupadd --gid 845 factorio; \
    useradd --uid 845 --gid 845 --home-dir /factorio --no-create-home --shell /usr/sbin/nologin factorio

COPY --from=fetcher --chown=root:root /opt/factorio /opt/factorio
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
COPY docker/healthcheck.sh /usr/local/bin/healthcheck.sh

RUN set -eux; \
    chmod 0755 /usr/local/bin/entrypoint.sh /usr/local/bin/healthcheck.sh; \
    mkdir -p /factorio/config /factorio/saves /factorio/mods /factorio/scenarios /factorio/script-output; \
    chown -R 845:845 /factorio

VOLUME ["/factorio"]

# 34197/udp - the game port. 27015/tcp - RCON.
EXPOSE 34197/udp 27015/tcp

# Factorio saves the map and shuts down cleanly on SIGTERM, which is what
# `docker stop` sends by default.
STOPSIGNAL SIGTERM

HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
    CMD ["/usr/local/bin/healthcheck.sh"]

WORKDIR /factorio
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
