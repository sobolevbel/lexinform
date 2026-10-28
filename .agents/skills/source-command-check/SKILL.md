---
name: "source-command-check"
description: "Прогнать и при необходимости починить полный локальный quality gate проекта"
---

# source-command-check

Use for the migrated `check` command. Run `make format`, then `make check`; both cover
`src`, `tests` and `tools`, matching CI.

Fix root causes, not configuration or suppressions: no `type: ignore`, local imports, `noqa`, or
weakened type/lint rules. Tests use `World` and public fakes; keep arrange-act-assert and avoid
private state. Run integration tests only when an adapter/network change warrants them.

Re-run the full gate after changes. If the request contains `--commit <message>`, commit only
after a green gate, with that English message and no push.
