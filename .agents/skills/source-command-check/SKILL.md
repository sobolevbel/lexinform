---
name: "source-command-check"
description: "Прогнать и при необходимости починить полный локальный quality gate проекта"
---

# source-command-check

Use for the migrated `check` command. Run, in order, `uv run ruff format src tests`,
`uv run ruff check src tests`, `uv run mypy`, and `uv run pytest -q`.

Fix root causes, not configuration or suppressions: no `type: ignore`, local imports, `noqa`, or
weakened type/lint rules. Tests use `World` and public fakes; keep arrange-act-assert and avoid
private state. Run integration tests only when an adapter/network change warrants them.

Re-run the full gate after changes. If the request contains `--commit <message>`, commit only
after a green gate, with that English message and no push.
