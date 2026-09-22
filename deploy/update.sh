#!/usr/bin/env bash
# Bring the checkout to origin/main and restart the relay; sync the batch poller's units too and
# make sure its timer is enabled (it manages its own schedule — nothing here restarts it on every
# deploy, only on a changed unit file, since a oneshot mid-run is harmless but pointless to force).
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
git fetch -q origin main
git reset -q --hard origin/main
after=$(git rev-parse HEAD)

wanted="${SSH_ORIGINAL_COMMAND:-}"
if [ -n "$wanted" ] && [ "$after" != "$wanted" ]; then
  echo "note: CI passed $wanted, origin/main is $after (deploying the latter)"
fi

# Units live in /etc, outside the checkout: without this a change to one would never reach the
# server (it did not, for the relay's own unit, until 2026-09-11).
reload_needed=0
poll_unit_changed=0
sync_unit() {
  local name=$1 changed_var=$2
  local dst=/etc/systemd/system/$name
  if ! cmp -s "deploy/$name" "$dst"; then
    cp "deploy/$name" "$dst"
    reload_needed=1
    printf -v "$changed_var" 1
    echo "$name updated"
  fi
}
sync_unit "$SERVICE.service" relay_unit_changed
sync_unit "$POLL_SERVICE" poll_unit_changed
sync_unit "$POLL_TIMER" poll_unit_changed
[ "$reload_needed" = 0 ] || systemctl daemon-reload

if [ "$before" = "$after" ] && [ "${relay_unit_changed:-0}" = 0 ] \
  && systemctl is-active --quiet "$SERVICE"; then
  echo "already at ${after:0:12}, $SERVICE running"
else
  echo "${before:0:12} -> ${after:0:12}"
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
if [ "$poll_unit_changed" = 1 ] || ! systemctl is-enabled --quiet "$POLL_TIMER" 2>/dev/null; then
  systemctl enable --now "$POLL_TIMER"
  echo "$POLL_TIMER enabled"
fi
