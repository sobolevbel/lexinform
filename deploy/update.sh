#!/usr/bin/env bash
# Bring the checkout to origin/main and restart the relay; sync the batch poller's units too and
# make sure its timer is enabled (it manages its own schedule — nothing here restarts it on every
# deploy, only on a changed unit file, since a oneshot mid-run is harmless but pointless to force).
#
# A unit this script learns about reaches the server one deploy late: bash is already running the
# previous version of this file when `git reset` brings the new one in (verified 2026-09-23, when
# the poller's units sat in the checkout while /etc had only the relay's). The next deploy fixes
# it by itself; `ssh mikrus /opt/lexinform/deploy/update.sh` does it now.
#
# Run by `.github/workflows/deploy-relay.yml` after a green CI on main, through an SSH key that
# is bound to this script (`command=` in authorized_keys: the key can do nothing else), or by
# hand: `ssh mikrus /opt/lexinform/deploy/update.sh`. The workflow passes the commit CI just
# passed as the SSH command; it is only compared with what origin/main holds.
set -euo pipefail

cd /opt/lexinform
UV=/root/.local/bin/uv
SERVICE=lexinform-listen
POLL_TIMER=lexinform-batch-poll.timer
POLL_SERVICE=lexinform-batch-poll.service

before=$(git rev-parse HEAD)
# Checkout HEAD can advance before dependency installation or service restart succeeds.
deployed_file=$(git rev-parse --git-path lexinform-relay-deployed)
deployed=$(cat "$deployed_file" 2>/dev/null || true)
pending_file=$(git rev-parse --git-path lexinform-relay-deploy-pending)
git fetch -q origin main
after=$(git rev-parse origin/main)

wanted="${SSH_ORIGINAL_COMMAND:-}"
if [ "$wanted" = "/opt/lexinform/deploy/update.sh" ]; then
  wanted=""
fi
if [ -n "$wanted" ] && [ "$after" != "$wanted" ]; then
  echo "skip: CI passed $wanted, waiting for CI on origin/main $after"
  exit 0
fi
git reset -q --hard "$after"

# Units live in /etc, outside the checkout: without this a change to one would never reach the
# server (it did not, for the relay's own unit, until 2026-09-11).
reload_needed=0
poll_unit_changed=0
sync_unit() {
  local name=$1 changed_var=$2
  local dst=/etc/systemd/system/$name
  if ! cmp -s "deploy/$name" "$dst"; then
    : > "$pending_file"
    cp "deploy/$name" "$dst"
    reload_needed=1
    printf -v "$changed_var" 1
    echo "$name updated"
  fi
}
sync_unit "$SERVICE.service" relay_unit_changed
sync_unit "$POLL_SERVICE" poll_unit_changed
sync_unit "$POLL_TIMER" poll_unit_changed
if [ "$reload_needed" = 1 ] || [ "$deployed" != "$after" ] || [ -f "$pending_file" ]; then
  systemctl daemon-reload
fi

if [ "$deployed" = "$after" ] && [ ! -f "$pending_file" ] \
  && systemctl is-active --quiet "$SERVICE"; then
  echo "already at ${after:0:12}, $SERVICE running"
else
  echo "${before:0:12} -> ${after:0:12}"
  : > "$pending_file"
  export UV_CONCURRENT_DOWNLOADS=1 UV_CONCURRENT_BUILDS=1 UV_CONCURRENT_INSTALLS=1
  "$UV" sync --frozen --no-dev -q
  systemctl restart "$SERVICE"

  # Three seconds only proved that systemd had started something: a relay that dies on its first
  # Telegram call (a revoked token, an .env that lost a line) reported a clean deploy and then
  # restarted for ever. A poll is 50 s, so a relay still up after 45 has opened its connection and
  # read from it; `is-active` alone is the answer for a unit that is still starting.
  for _ in $(seq 9); do
    sleep 5
    systemctl is-active --quiet "$SERVICE" || { systemctl status "$SERVICE" --no-pager -l; exit 1; }
  done
  echo "$SERVICE up 45 s after restart"
fi

# `enable --now` on an already-enabled, already-active timer is a no-op; on a fresh install or a
# changed unit it (re)starts the timer, never the oneshot service the timer itself schedules.
if [ "$poll_unit_changed" = 1 ] || [ -f "$pending_file" ] \
  || ! systemctl is-enabled --quiet "$POLL_TIMER" 2>/dev/null; then
  : > "$pending_file"
  systemctl enable --now "$POLL_TIMER"
  echo "$POLL_TIMER enabled"
fi

marker_tmp="${deployed_file}.tmp.$$"
trap 'rm -f "$marker_tmp"' EXIT
printf '%s\n' "$after" > "$marker_tmp"
mv -f "$marker_tmp" "$deployed_file"
rm -f "$pending_file"
