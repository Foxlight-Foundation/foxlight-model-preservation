#!/usr/bin/env bash
# Optionally install a headless Sovereign mirror as a hardened systemd system
# service (sovereign-mirror@.service). Run as root on a Linux host with systemd.
#
# Every check and refusal comes first: it authenticates the release archive,
# prints the `mirror quickstart` review from a temporary copy and asks for
# consent before changing anything. Only then does it create the unprivileged
# `sovereign` account, install the release into /opt/sovereign/<version>, set
# up the mirror as `sovereign`, point /opt/sovereign/current at the release,
# install the unit and enable the service. Every step is printed and the
# script is safe to run again, for example to upgrade or change settings.
#
# With `-- --plan-only` it stops after the review and changes nothing on the
# host: a running service, its software, unit and configuration stay as they
# are.
#
# Usage:
#   sudo ./install-system-service.sh \
#     --archive foxlight-model-preservation-0.33.0-install.zip \
#     --bundle release.sigstore.json \
#     --collection collection.json --instance example \
#     -- --public-url https://mirror.example.org --disk-bytes 500G
#
# Options:
#   --archive PATH     the release installation archive
#   --bundle PATH      release.sigstore.json from the same release; the archive
#                      is checked with `gh attestation verify`
#   --sha256 HEX       instead of --bundle: the archive's SHA-256, obtained
#                      independently (for example from the release's
#                      SHA256SUMS after verifying that file's attestation on
#                      another machine)
#   --collection PATH  the reviewed collection file; a source "file" it names
#                      is copied from beside it
#   --instance NAME    mirror instance name (default: main)
#   --python PATH      Python 3.11+ for the environment (default: python3); it
#                      must live outside /home and /root
#   --requirements F   requirements-mirror.lock (default, adds libtorrent for
#                      BitTorrent and the DHT) or requirements.lock
#   --yes-i-consent-to-public-serving
#                      consent without the interactive prompt; read the review
#                      first (with `-- --plan-only`)
#   -- ARGS            further `mirror quickstart` options
set -euo pipefail

prefix=/opt/sovereign
config_root=/etc/sovereign/mirrors
state_root=/var/lib/sovereign
unit_name=sovereign-mirror@.service
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo=Foxlight-Foundation/foxlight-model-preservation
consent_flag=--yes-i-consent-to-public-serving

archive=""
bundle=""
sha256=""
collection=""
instance=main
python=python3
requirements="requirements-mirror.lock"
consent=""
plan_only=""

step() { printf '\n==> %s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }
usage() { sed -n '2,/^set -euo/{/^set -euo/d;s/^# \{0,1\}//;p;}' "$0"; }

while [ $# -gt 0 ]; do
  case "$1" in
    --archive) archive="${2:?}"; shift 2 ;;
    --bundle) bundle="${2:?}"; shift 2 ;;
    --sha256) sha256="${2:?}"; shift 2 ;;
    --collection) collection="${2:?}"; shift 2 ;;
    --instance) instance="${2:?}"; shift 2 ;;
    --python) python="${2:?}"; shift 2 ;;
    --requirements) requirements="${2:?}"; shift 2 ;;
    "$consent_flag") consent=1; shift ;;
    -h | --help) usage; exit 0 ;;
    --) shift; break ;;
    *) die "unknown option $1 (see --help)" ;;
  esac
done
# Plan-only is decided here, before anything else: it never changes the host.
quickstart_args=()
for argument in "$@"; do
  case "$argument" in
    --plan-only) plan_only=1 ;;
    "$consent_flag") consent=1 ;;
    --dir | --dir=* | --collection | --collection=* | --run)
      die "$argument is set by this script; leave it out" ;;
    *) quickstart_args+=("$argument") ;;
  esac
done

# --- Checks (nothing is changed until the review is consented to) -----------
[ "$(id -u)" -eq 0 ] || die "run as root"
[ -f "$archive" ] || die "--archive must name the release archive"
[ -f "$collection" ] || die "--collection must name the collection file"
[[ "$instance" =~ ^[A-Za-z0-9_-]{1,64}$ ]] || die "--instance may use letters, digits, - and _"
case "$requirements" in
  requirements-mirror.lock | requirements.lock) ;;
  *) die "--requirements must be requirements-mirror.lock or requirements.lock" ;;
esac
[ -n "$bundle" ] || [ -n "$sha256" ] ||
  die "refusing an unverified archive: give --bundle or --sha256"
if [ -n "$bundle" ]; then
  [ -f "$bundle" ] || die "--bundle must name release.sigstore.json"
  command -v gh >/dev/null || die "gh (GitHub CLI) is needed for --bundle; or use --sha256"
fi
for tool in systemctl runuser useradd unzip sha256sum uv; do
  command -v "$tool" >/dev/null || die "$tool is needed (install it first)"
done
python="$(command -v "$python")" || die "Python not found"
case "$(readlink -f "$python")" in
  /home/* | /root/*) die "use a Python outside /home and /root; the service cannot see them" ;;
esac
[ -f "$here/$unit_name" ] || die "$unit_name is missing beside this script"

name="$(basename "$archive")"
[[ "$name" =~ ^foxlight-model-preservation-([0-9]+\.[0-9]+\.[0-9]+)-install\.zip$ ]] ||
  die "expected foxlight-model-preservation-X.Y.Z-install.zip, got $name"
version="${BASH_REMATCH[1]}"
target="$prefix/$version"
mirror_dir="$state_root/mirrors/$instance"
config_dir="$config_root/$instance"
unit="sovereign-mirror@$instance.service"

installed=""
if [ -x "$target/bin/model-preserve" ] && [ -f "$target/sovereign-install.json" ]; then
  installed=1
elif [ -e "$target" ]; then
  die "$target exists but is incomplete (a failed install is kept for inspection); remove it and run again"
fi
source_file="$("$python" -I -c 'import json, sys
source = json.load(open(sys.argv[1])).get("source") or {}
print(source.get("file") or "")' "$collection")" || die "the collection file is not valid JSON"
if [ -n "$source_file" ]; then
  case "/$source_file/" in
    //* | */../*) die "collection source file must stay beside the collection" ;;
  esac
  [ -f "$(dirname "$collection")/$source_file" ] ||
    die "the collection's source file $source_file is not beside it"
fi
if [ -z "$plan_only" ] && [ -z "$consent" ] && ! [ -t 0 ]; then
  die "no terminal to ask for consent: read the review with -- --plan-only, then pass $consent_flag"
fi

# Scratch space for the review; removed on exit. Readable so the unprivileged
# reviewer can run the temporary software and read the staged collection.
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
chmod 0755 "$work"

step "Authenticate $name (release v$version)"
if [ -n "$bundle" ]; then
  gh attestation verify "$archive" --bundle "$bundle" \
    --repo "$repo" \
    --signer-workflow "$repo/.github/workflows/release.yml" \
    --source-ref "refs/tags/v$version" --deny-self-hosted-runners
  echo "Attestation verified."
else
  actual="$(sha256sum "$archive" | cut -d ' ' -f 1)"
  [ "$actual" = "$(printf '%s' "$sha256" | tr 'A-F' 'a-f')" ] ||
    die "archive SHA-256 $actual does not match --sha256"
  echo "SHA-256 matches the independently obtained value."
fi
(umask 077 && unzip -q "$archive" -d "$work/release")
found="$("$python" -I -c 'import json, sys; print(json.load(open(sys.argv[1]))["version"])' \
  "$work/release/INSTALL.json")"
[ "$found" = "$version" ] || die "INSTALL.json names version $found, not $version"

install_release() {
  # The helper verifies every listed file and creates the directory owner-only;
  # the service account needs to read and run it, never write it.
  (umask 022 && "$python" -I "$work/release/install.py" \
    --directory "$1" --requirements "$requirements")
  chmod 0755 "$1"
}

step "Review the mirror settings (mirror quickstart --plan-only)"
if [ -n "$installed" ]; then
  software="$target"
  echo "Using the installed release in $target."
else
  echo "Installing the release into a temporary directory for the review."
  install_release "$work/software" >/dev/null
  software="$work/software"
fi
install -d -m 0755 "$work/collection"
install -m 0644 "$collection" "$work/collection/collection.json"
if [ -n "$source_file" ]; then
  install -D -m 0644 "$(dirname "$collection")/$source_file" "$work/collection/$source_file"
fi
reviewer=nobody
getent passwd sovereign >/dev/null && reviewer=sovereign
review=(
  "$software/bin/model-preserve" mirror quickstart
  --dir "$mirror_dir" --collection "$work/collection/collection.json"
  "${quickstart_args[@]}"
)
(cd / && runuser -u "$reviewer" -- env -i PATH=/usr/bin:/bin LANG=C.UTF-8 \
  "${review[@]}" --plan-only >/dev/null) ||
  die "the review failed (see above); nothing on this host was changed"

if [ -n "$plan_only" ]; then
  if systemctl is-active --quiet "$unit"; then
    echo "Plan only: nothing on this host was changed; $unit is still running."
  else
    echo "Plan only: nothing on this host was changed."
  fi
  exit 0
fi

if [ -z "$consent" ]; then
  printf "\nType 'serve' to consent to public serving as reviewed: "
  read -r answer || answer=""
  [ "$answer" = "serve" ] || die "no consent; nothing on this host was changed"
fi

# --- Changes (every check and the consent above have passed) ----------------
step "System account 'sovereign'"
if getent passwd sovereign >/dev/null; then
  echo "Already exists: $(getent passwd sovereign)"
else
  nologin="$(command -v nologin || echo /usr/sbin/nologin)"
  useradd --system --user-group --home-dir "$state_root" --no-create-home \
    --shell "$nologin" sovereign
  echo "Created: $(getent passwd sovereign)"
fi

step "Software in $target"
install -d -o root -g root -m 0755 "$prefix"
if [ -n "$installed" ]; then
  echo "Already installed; leaving it unchanged."
else
  install_release "$target"
fi

step "Unit /etc/systemd/system/$unit_name and state in $mirror_dir"
install -o root -g root -m 0644 "$here/$unit_name" "/etc/systemd/system/$unit_name"
systemctl daemon-reload
install -d -o root -g root -m 0755 "$state_root"
# Quickstart creates the mirror directory itself, so its parent is the account's.
install -d -o sovereign -g sovereign -m 0700 "$state_root/mirrors"

step "Set up the mirror (mirror quickstart, as sovereign)"
was_active=""
if systemctl is-active --quiet "$unit"; then
  was_active=1
  echo "Stopping $unit while the settings change (it drains first)."
  systemctl stop "$unit"
fi
# Taken once the service has stopped writing, to report a refusal truthfully.
ledger_before=""
if [ -f "$mirror_dir/mirror.json" ]; then
  ledger_before="$(sha256sum "$mirror_dir/mirror.json" | cut -d ' ' -f 1)"
fi
setup=(
  "$target/bin/model-preserve" mirror quickstart
  --dir "$mirror_dir" --collection "$work/collection/collection.json"
  "${quickstart_args[@]}" "$consent_flag"
)
if ! (cd / && runuser -u sovereign -- env -i PATH=/usr/bin:/bin LANG=C.UTF-8 \
  "${setup[@]}" >/dev/null); then
  ledger_after=""
  if [ -f "$mirror_dir/mirror.json" ]; then
    ledger_after="$(sha256sum "$mirror_dir/mirror.json" | cut -d ' ' -f 1)"
  fi
  if [ -z "$ledger_before$ledger_after" ]; then
    outcome="no mirror was set up"
  elif [ "$ledger_after" = "$ledger_before" ]; then
    outcome="the mirror's settings and restart grant are unchanged"
  else
    # Releases before the all-or-nothing quickstart could refuse part-way.
    outcome="the mirror's settings changed before the refusal, which can clear its restart grant; check \`mirror status\` and renew the grant if needed"
  fi
  if [ -n "$was_active" ]; then
    systemctl reset-failed "$unit" 2>/dev/null || true
    if systemctl start "$unit" && sleep 5 && systemctl is-active --quiet "$unit"; then
      die "quickstart refused (see above); $outcome. $unit is running again"
    fi
    die "quickstart refused (see above); $outcome. $unit did not start again: journalctl -u $unit"
  fi
  die "quickstart refused (see above); $outcome. $unit was not running and was not started"
fi

step "Record the collection in $config_dir and use $version"
install -d -o root -g root -m 0755 "$config_root" "$config_dir"
install -o root -g root -m 0644 "$work/collection/collection.json" "$config_dir/collection.json"
if [ -n "$source_file" ]; then
  install -D -o root -g root -m 0644 "$work/collection/$source_file" "$config_dir/$source_file"
fi
ln -sfn "$version" "$prefix/current.new"
mv -Tf "$prefix/current.new" "$prefix/current"
echo "$prefix/current -> $(readlink "$prefix/current")"

step "Enable and start $unit"
systemctl enable "$unit"
# Earlier starts count towards the unit's start limit; this one is deliberate.
systemctl reset-failed "$unit" 2>/dev/null || true
systemctl restart "$unit"
systemctl --no-pager --lines=5 status "$unit" || true
cat <<EOF

Done. The mirror listens on 127.0.0.1:8080; put a TLS reverse proxy in front
of it (see deploy/mirror/README.md). Logs: journalctl -u $unit
EOF
