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
  --resume-for "${SOVEREIGN_MIRROR_GRANT:-180d}"

# Upload caps are opt-in. Unset: a new mirror has none and an existing mirror
# keeps the caps it has. A size caps uploads; "none" removes the cap.
if [ -n "${SOVEREIGN_MIRROR_EGRESS_DAILY:-}" ]; then
  set -- "$@" --egress-daily "$SOVEREIGN_MIRROR_EGRESS_DAILY"
fi
if [ -n "${SOVEREIGN_MIRROR_EGRESS_MONTHLY:-}" ]; then
  set -- "$@" --egress-monthly "$SOVEREIGN_MIRROR_EGRESS_MONTHLY"
fi

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

idle() {
  # Stay up so the message remains in the container logs and an operator can
  # `docker compose exec mirror model-preserve mirror ...`; restarting the
  # container retries. PID 1 ignores default signals: stop promptly.
  seconds="${SOVEREIGN_MIRROR_IDLE:-infinity}"
  if [ "$seconds" != "0" ]; then
    trap 'exit 0' TERM INT
    sleep "$seconds" &
    wait $!
  fi
  exit 0
}

if [ "$consent" != "yes-i-consent-to-public-serving" ]; then
  echo "No serving consent (SOVEREIGN_MIRROR_CONSENT): plan-only, nothing is served." >&2
  if ! model-preserve "$@" --plan-only; then
    echo "The review failed: check the collection file and settings." >&2
  fi
  idle
fi

# Setup refusals (invalid collection, a paused or departed mirror, an
# interrupted init) do not fix themselves: report and idle instead of looping.
if ! model-preserve "$@" --yes-i-consent-to-public-serving; then
  echo "Setup refused (see above); nothing is served. Fix it, then restart." >&2
  idle
fi

# Serve under the grant just recorded. A clean stop (grant expiry) exits 0 and
# the restart policy renews the grant through the setup above.
exec model-preserve mirror run --dir "$dir" --resume
