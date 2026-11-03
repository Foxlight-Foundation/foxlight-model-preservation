#!/bin/sh
# Container entrypoint for a headless Sovereign mirror behind Caddy.
#
# Without SOVEREIGN_MIRROR_CONSENT=yes-i-consent-to-public-serving the
# container only prints the quickstart review (plan-only), changes nothing and
# serves nothing. With it, each start re-applies the reviewed collection and
# policy, renews the restart grant and serves until stopped or the grant ends.
set -eu

dir="${SOVEREIGN_MIRROR_DIR:-/data/mirror}"
collection="${SOVEREIGN_MIRROR_COLLECTION:-/config/collection.json}"
consent="${SOVEREIGN_MIRROR_CONSENT:-}"

set -- mirror quickstart \
  --dir "$dir" \
  --collection "$collection" \
  --bind 127.0.0.1 \
  --port "${SOVEREIGN_MIRROR_PORT:-8080}" \
  --disk-bytes "${SOVEREIGN_MIRROR_BUDGET:-100G}" \
  --ingress-daily "${SOVEREIGN_MIRROR_INGRESS_DAILY:-50G}" \
  --egress-daily "${SOVEREIGN_MIRROR_EGRESS_DAILY:-50G}" \
  --egress-monthly "${SOVEREIGN_MIRROR_EGRESS_MONTHLY:-1T}" \
  --resume-for "${SOVEREIGN_MIRROR_GRANT:-90d}"

# A container's host name changes when it is recreated; prefer a stable name.
name="${SOVEREIGN_MIRROR_NAME:-${SOVEREIGN_MIRROR_DOMAIN:-}}"
if [ -n "$name" ]; then
  set -- "$@" --name "$name"
fi
if [ -n "${SOVEREIGN_MIRROR_DOMAIN:-}" ]; then
  set -- "$@" --public-url "https://$SOVEREIGN_MIRROR_DOMAIN"
fi
if [ -n "${SOVEREIGN_MIRROR_CONTACT:-}" ]; then
  set -- "$@" --contact "$SOVEREIGN_MIRROR_CONTACT"
fi

if [ "$consent" != "yes-i-consent-to-public-serving" ]; then
  echo "No serving consent (SOVEREIGN_MIRROR_CONSENT): plan-only, nothing is served." >&2
  if ! model-preserve "$@" --plan-only; then
    echo "The review failed: check the collection file and settings." >&2
  fi
  # Stay up so the review remains visible in the container logs.
  idle="${SOVEREIGN_MIRROR_IDLE:-infinity}"
  if [ "$idle" != "0" ]; then
    # PID 1 ignores default signals: stop promptly on docker stop.
    trap 'exit 0' TERM INT
    sleep "$idle" &
    wait $!
  fi
  exit 0
fi

exec model-preserve "$@" --yes-i-consent-to-public-serving --run
