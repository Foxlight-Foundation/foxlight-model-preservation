# syntax=docker/dockerfile:1
# Headless Sovereign mirror image (linux/amd64 and linux/arm64).
#
# Build from the repository root:
#   docker build -f deploy/mirror/Dockerfile -t sovereign-mirror .
# The mirror listens on loopback only; run it with compose.yaml so Caddy
# terminates TLS in the same network namespace. See deploy/mirror/README.md.

ARG PYTHON_IMAGE=python:3.13-slim-bookworm

FROM ${PYTHON_IMAGE} AS build
# Optional extras, comma separated. "bittorrent" adds libtorrent for native
# torrent seeding and the optional DHT rendezvous; set EXTRAS="" to omit it.
ARG EXTRAS=bittorrent
ARG UV_VERSION=0.12.23
WORKDIR /src
RUN python -m pip install --no-cache-dir "uv==${UV_VERSION}"
COPY pyproject.toml uv.lock README.md LICENSE NOTICE ./
COPY src ./src
# Dependencies come hash-pinned from the committed lock; the package itself is
# the wheel built from this checkout.
RUN set -eu; \
    extras=""; \
    for extra in $(echo "${EXTRAS}" | tr ',' ' '); do \
      extras="${extras} --extra ${extra}"; \
    done; \
    uv export --locked --no-dev --no-emit-project ${extras} \
      --format requirements-txt --output-file /requirements.txt; \
    uv build --wheel --out-dir /dist

FROM ${PYTHON_IMAGE}
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONNOUSERSITE=1 \
    PYTHONUNBUFFERED=1 \
    SOVEREIGN_MIRROR_DIR=/data/mirror \
    SOVEREIGN_MIRROR_COLLECTION=/config/collection.json
COPY --from=build /requirements.txt /dist/ /tmp/install/
RUN set -eu; \
    python -m pip install --no-cache-dir --only-binary :all: --require-hashes \
      -r /tmp/install/requirements.txt; \
    python -m pip install --no-cache-dir --no-deps /tmp/install/*.whl; \
    rm -rf /tmp/install; \
    useradd --system --uid 10001 --user-group --home-dir /data \
      --shell /usr/sbin/nologin sovereign; \
    install -d -o sovereign -g sovereign -m 0700 /data; \
    install -d -m 0755 /config
COPY --chmod=0755 deploy/mirror/entrypoint.sh /usr/local/bin/sovereign-mirror
USER sovereign
WORKDIR /data
VOLUME ["/data"]
# SIGTERM drains workers and keeps every retained byte.
STOPSIGNAL SIGTERM
ENTRYPOINT ["/usr/local/bin/sovereign-mirror"]
