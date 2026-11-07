#!/usr/bin/env bash
# Optionally install a headless Sovereign mirror as a hardened systemd system
# service (sovereign-mirror@.service). Run as root on a Linux host with systemd.
#
# It authenticates the release archive, creates the unprivileged `sovereign`
# account, installs the release into /opt/sovereign/<version>, points
# /opt/sovereign/current at it, installs the unit, runs `mirror quickstart` as
# `sovereign` (which prints the review and asks for consent) and enables the
# service. Every step is printed and the script is safe to run again, for
# example to upgrade to a new release or to change the mirror's settings.
#
# Usage:
#   sudo ./install-system-service.sh \
#     --archive foxlight-model-preservation-0.28.1-install.zip \
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
#                      first (for example with `-- --plan-only`)
#   -- ARGS            further `mirror quickstart` options
set -euo pipefail

prefix=/opt/sovereign
config_root=/etc/sovereign/mirrors
state_root=/var/lib/sovereign
unit_name=sovereign-mirror@.service
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo=Foxlight-Foundation/foxlight-model-preservation

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
    --yes-i-consent-to-public-serving) consent="$1"; shift ;;
    -h | --help) usage; exit 0 ;;
    --) shift; break ;;
    *) die "unknown option $1 (see --help)" ;;
  esac
done
quickstart_args=("$@")
for argument in "${quickstart_args[@]}"; do
  [ "$argument" = "--plan-only" ] && plan_only=1
done

[ "$(id -u)" -eq 0 ] || die "run as root"
[ -f "$archive" ] || die "--archive must name the release archive"
[ -f "$collection" ] || die "--collection must name the collection file"
[[ "$instance" =~ ^[A-Za-z0-9_-]{1,64}$ ]] || die "--instance may use letters, digits, - and _"
case "$requirements" in
  requirements-mirror.lock | requirements.lock) ;;
  *) die "--requirements must be requirements-mirror.lock or requirements.lock" ;;
esac
for tool in systemctl runuser useradd unzip sha256sum uv; do
  command -v "$tool" >/dev/null || die "$tool is needed (install it first)"
done
python="$(command -v "$python")" || die "Python not found"
case "$(readlink -f "$python")" in
  /home/* | /root/*) die "use a Python outside /home and /root; the service cannot see them" ;;
esac

name="$(basename "$archive")"
[[ "$name" =~ ^foxlight-model-preservation-([0-9]+\.[0-9]+\.[0-9]+)-install\.zip$ ]] ||
  die "expected foxlight-model-preservation-X.Y.Z-install.zip, got $name"
version="${BASH_REMATCH[1]}"
target="$prefix/$version"
mirror_dir="$state_root/mirrors/$instance"
config_dir="$config_root/$instance"
unit="sovereign-mirror@$instance.service"

step "1/7 Authenticate $name (release v$version)"
if [ -n "$bundle" ]; then
  command -v gh >/dev/null || die "gh (GitHub CLI) is needed for --bundle; or use --sha256"
  gh attestation verify "$archive" --bundle "$bundle" \
    --repo "$repo" \
    --signer-workflow "$repo/.github/workflows/release.yml" \
    --source-ref "refs/tags/v$version" --deny-self-hosted-runners
elif [ -n "$sha256" ]; then
  actual="$(sha256sum "$archive" | cut -d ' ' -f 1)"
  [ "$actual" = "$(printf '%s' "$sha256" | tr 'A-F' 'a-f')" ] ||
    die "archive SHA-256 $actual does not match --sha256"
  echo "SHA-256 matches the independently obtained value."
else
  die "refusing an unverified archive: give --bundle or --sha256"
fi

step "2/7 System account 'sovereign'"
if getent passwd sovereign >/dev/null; then
  echo "Already exists: $(getent passwd sovereign)"
else
  nologin="$(command -v nologin || echo /usr/sbin/nologin)"
  useradd --system --user-group --home-dir "$state_root" --no-create-home \
    --shell "$nologin" sovereign
  echo "Created: $(getent passwd sovereign)"
fi

step "3/7 Software in $target"
install -d -o root -g root -m 0755 "$prefix"
if [ -x "$target/bin/model-preserve" ] && [ -f "$target/sovereign-install.json" ]; then
  echo "Already installed; leaving it unchanged."
elif [ -e "$target" ]; then
  die "$target exists but is incomplete (a failed install is kept for inspection); remove it and run again"
else
  work="$(umask 077 && mktemp -d)"
  trap 'rm -rf "$work"' EXIT
  unzip -q "$archive" -d "$work/release"
  found="$("$python" -I -c 'import json, sys; print(json.load(open(sys.argv[1]))["version"])' \
    "$work/release/INSTALL.json")"
  [ "$found" = "$version" ] || die "INSTALL.json names version $found, not $version"
  # The helper verifies every listed file and creates the directory owner-only;
  # the service account needs to read and run it, never write it.
  (umask 022 && "$python" -I "$work/release/install.py" \
    --directory "$target" --requirements "$requirements")
  chmod 0755 "$target"
fi
ln -sfn "$version" "$prefix/current.new"
mv -Tf "$prefix/current.new" "$prefix/current"
echo "$prefix/current -> $(readlink "$prefix/current")"

step "4/7 Collection in $config_dir and state in $mirror_dir"
install -d -o root -g root -m 0755 "$config_root" "$config_dir"
install -o root -g root -m 0644 "$collection" "$config_dir/collection.json"
source_file="$("$python" -I -c 'import json, sys
source = json.load(open(sys.argv[1])).get("source") or {}
print(source.get("file") or "")' "$collection")"
if [ -n "$source_file" ]; then
  case "/$source_file/" in
    //* | */../*) die "collection source file must stay beside the collection" ;;
  esac
  install -D -o root -g root -m 0644 "$(dirname "$collection")/$source_file" \
    "$config_dir/$source_file"
  echo "Copied source file $source_file"
fi
install -d -o root -g root -m 0755 "$state_root"
# Quickstart creates the mirror directory itself, so its parent is the account's.
install -d -o sovereign -g sovereign -m 0700 "$state_root/mirrors"

step "5/7 Unit /etc/systemd/system/$unit_name"
install -o root -g root -m 0644 "$here/$unit_name" "/etc/systemd/system/$unit_name"
systemctl daemon-reload

step "6/7 Review and set up the mirror (mirror quickstart, as sovereign)"
if systemctl is-active --quiet "$unit"; then
  echo "Stopping $unit while the settings change (it drains first)."
  systemctl stop "$unit"
fi
quickstart=(
  "$prefix/current/bin/model-preserve" mirror quickstart
  --dir "$mirror_dir" --collection "$config_dir/collection.json"
)
[ -n "$consent" ] && quickstart+=("$consent")
if ! (cd / && runuser -u sovereign -- env -i PATH=/usr/bin:/bin LANG=C.UTF-8 \
  "${quickstart[@]}" "${quickstart_args[@]}"); then
  die "quickstart refused (see above); nothing is enabled or served"
fi
if [ -n "$plan_only" ]; then
  echo "Plan only: nothing was changed or enabled. Run again without --plan-only to consent."
  exit 0
fi

step "7/7 Enable and start $unit"
systemctl enable "$unit"
# Earlier starts count towards the unit's start limit; this one is deliberate.
systemctl reset-failed "$unit" 2>/dev/null || true
systemctl restart "$unit"
systemctl --no-pager --lines=5 status "$unit" || true
cat <<EOF

Done. The mirror listens on 127.0.0.1:8080; put a TLS reverse proxy in front
of it (see deploy/mirror/README.md). Logs: journalctl -u $unit
EOF
