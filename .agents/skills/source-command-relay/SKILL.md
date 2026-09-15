---
name: "source-command-relay"
description: "Проверить реле операторских команд, inbox и связанные GitHub Actions без изменений"
---

# source-command-relay

Use for the migrated `relay` command. This is strictly read-only: inspect recent CI,
`deploy-relay`, and daily Actions runs; relay service status/logs and deployed revision on mikrus;
and the `inbox` branch.

Compare the VPS revision with `origin/main`. A queued inbox file should be correlated with the
commands table and a commands-phase run; a recorded command is not executed twice. Explain that a
green deploy with an old service revision may mean no restart, while failed CI also leaves the
relay undeployed.

Return three concise states (relay, queue, Actions) plus actionable interventions. Do not restart,
deploy, or delete anything without explicit authorisation.
