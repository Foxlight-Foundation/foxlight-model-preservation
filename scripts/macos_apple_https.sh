#!/usr/bin/env bash
# GitHub-hosted Apple CloudKit HTTP/3 workaround, scoped to this command/job.
# https://github.com/actions/runner-images/issues/14924
set -euo pipefail
if [[ "${GITHUB_ACTIONS:-}" != true || "${GITHUB_REF:-}" != refs/tags/sovereign-macos-publisher-v0.1.0-build8-v2 || "${GITHUB_REPOSITORY:-}" != Foxlight-Foundation/foxlight-model-preservation || "${RUNNER_ENVIRONMENT:-}" != github-hosted || "$(uname -s)" != Darwin || $# -eq 0 ]]; then
    echo "Apple HTTPS routing requires an authorized hosted Mac verification command." >&2
    exit 2
fi
if [[ ! "${GITHUB_RUN_ID:-}" =~ ^[0-9]+$ || ! "${GITHUB_RUN_ATTEMPT:-}" =~ ^[0-9]+$ || ! "${GITHUB_JOB:-}" =~ ^[a-zA-Z0-9_-]+$ ]]; then
    echo "Invalid runner-owned firewall scope." >&2
    exit 2
fi
: "${RUNNER_TEMP:?Missing runner temporary directory}"
umask 077
network_scratch="$(mktemp -d "${RUNNER_TEMP}/sovereign-https.XXXXXX")"
network_anchor="com.apple/sovereign-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}-${GITHUB_JOB}"
network_token=""
network_loaded=false

cleanup_network() {
    local command_status=$?
    local cleanup_status=0
    if [[ "${network_loaded}" == true ]]; then
        sudo /sbin/pfctl -a "${network_anchor}" -F rules || cleanup_status=$?
    fi
    if [[ -n "${network_token}" ]]; then
        sudo /sbin/pfctl -X "${network_token}" || cleanup_status=$?
    fi
    if (( command_status != 0 )); then exit "${command_status}"; fi
    exit "${cleanup_status}"
}
trap cleanup_network EXIT

# Keep the root rules and every other service's anchor/reference untouched.
sudo /sbin/pfctl -sr > "${network_scratch}/root-rules.txt"
if ! grep -Eq '^[[:space:]]*anchor "com\.apple/\*"' "${network_scratch}/root-rules.txt"; then
    echo "Expected macOS service-anchor rule is absent; refusing root-rule replacement." >&2
    exit 2
fi
sudo /sbin/pfctl -a "${network_anchor}" -sr > "${network_scratch}/existing-rules.txt"
if [[ -s "${network_scratch}/existing-rules.txt" ]]; then
    echo "Runner network anchor already exists; refusing to replace it." >&2
    exit 2
fi
printf '%s\n' 'block return out quick proto udp from any to any port 443' > "${network_scratch}/rule.pf"
sudo /sbin/pfctl -a "${network_anchor}" -f "${network_scratch}/rule.pf"
network_loaded=true
sudo /sbin/pfctl -E > "${network_scratch}/enable.txt" 2>&1
network_token="$(awk '/^Token[[:space:]]*:/ { print $3 }' "${network_scratch}/enable.txt")"
if [[ ! "${network_token}" =~ ^[0-9]+$ ]]; then
    network_token=""
    echo "PF did not return an owned enable reference; refusing to continue." >&2
    exit 2
fi
echo "Runner-only UDP/443 block enabled; Apple HTTPS keeps normal TCP/TLS verification."
"$@"
