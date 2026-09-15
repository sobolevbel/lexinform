---
name: "source-command-bill"
description: "Разобрать один законопроект на копии production state без изменений в проде"
---

# source-command-bill

Use for the migrated `bill <number>` command. Work read-only on a fresh `/tmp` copy of the
`state`-branch dump; do not publish or run the pipeline against it.

Use `lexinform show <number>` and `lexinform preview <number>`. Inspect the bill row,
`status_changes`, and `publications`, including pending, failed, unknown, and `joint_bill` rows.
Follow linked RPW, RCL, or wykaz records because they share a thread.

Explain the current substantive process stage (not a terminal `End` node), `next_phase` and
`is_over`; why its prefilter/analysis status arose; what the reader has seen; and what the next
run will do. When something is wrong, name the precise safe operator or CLI remedy; do not edit
code unless asked.
