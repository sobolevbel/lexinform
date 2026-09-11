#!/usr/bin/env bash
# Bring the relay's checkout to origin/main and restart the service.
#
# Run by `.github/workflows/deploy-relay.yml` after a green CI on main, through an SSH key that
# is bound to this script (`command=` in authorized_keys: the key can do nothing else), or by
# hand: `ssh mikrus /opt/lexinform/deploy/update.sh`. The workflow passes the commit CI just
# passed as the SSH command; it is only compared with what origin/main holds.
set -euo pipefail

cd /opt/lexinform
UV=/root/.local/bin/uv
SERVICE=lexinform-listen

before=$(git rev-parse HEAD)
git fetch -q origin main
git reset -q --hard origin/main
after=$(git rev-parse HEAD)

wanted="${SSH_ORIGINAL_COMMAND:-}"
if [ -n "$wanted" ] && [ "$after" != "$wanted" ]; then
  echo "note: CI passed $wanted, origin/main is $after (deploying the latter)"
fi

if [ "$before" = "$after" ] && systemctl is-active --quiet "$SERVICE"; then
  echo "already at ${after:0:12}, $SERVICE running"
  exit 0
fi

echo "${before:0:12} -> ${after:0:12}"
"$UV" sync --frozen --no-dev -q
systemctl restart "$SERVICE"
sleep 3
systemctl is-active "$SERVICE"
