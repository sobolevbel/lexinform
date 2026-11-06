---
name: "source-command-check"
description: "Прогнать и при необходимости починить полный локальный quality gate проекта"
---

# source-command-check

Run `make check` for the bot and tools. It checks formatting without editing files.
Use `make format` when fixing formatting, then review the diff for unrelated edits.
For documentation or web changes, use the corresponding gates in
[`CONTRIBUTING.md`](../../../CONTRIBUTING.md).

Fix root causes, not configuration or suppressions: no `type: ignore`, local imports, `noqa`, or
weakened type/lint rules. Prefer unit tests. Use `World` for behavior that spans services or
durable lifecycle boundaries. Run integration tests only when an adapter/network change warrants them.

Re-run the full gate after changes. If the request contains `--commit <message>`, commit only
after a green gate, with that English message and no push.
