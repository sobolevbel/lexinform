---
name: "source-command-relay"
description: "Проверить реле операторских команд, очередь inbox или CI и автоматический deploy реле для конкретного SHA"
---

# source-command-relay

For a specific release, use `make release-check RELEASE_SHA=<full-sha>`. Follow the
[release runbook](../../../docs/operations/index.md#проверить-выпуск-и-автоматическое-обновление-relay)
for bounded waiting, the optional previous verified SHA and the JSON receipt. Pass those flags
through `ARGS`. Report the exact
SHA, CI and deploy run links, and the verifier's status. A verified deploy is evidence of the
restart and timer, with Telegram delivery checked separately when requested.

For a general relay incident, inspect recent CI, `deploy-relay` and daily Actions runs, service
status/logs and the deployed revision on mikrus, and the `inbox` branch. Keep both paths read-only.

Compare the VPS revision with `origin/main`. A queued inbox file should be correlated with the
commands table and a commands-phase run; a recorded command is not executed twice. Explain that a
green deploy with an old service revision may mean no restart, while failed CI also leaves the
relay undeployed.

Return three concise states (relay, queue, Actions) plus actionable interventions. Do not restart,
deploy, or delete anything without explicit authorisation.
