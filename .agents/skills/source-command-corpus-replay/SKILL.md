---
name: source-command-corpus-replay
description: Run or qualify offline Lexinform corpus replays for extraction, source selection, prepared input, or evidence. Use when a change needs pinned before-and-after corpus evidence or when verifying a saved replay.
---

# Corpus replay

Choose the smallest existing replay that exercises the changed decision. Start with
`tools/README.md` and the relevant rules linked from `AGENTS.md`. Use the pinned release
matrix when the change crosses extraction, selection, comparison, or delivery boundaries.

- Find the external corpus checkout and start at its `INDEX.json`. Pin the actual input
  hashes, options, and baseline before running a comparison. Keep source and output roots
  separate. Use a new output path, even when repeating an identical run.
- Use `python -m tools.corpus_runs list` to find the registered suite, its child module and
  preset options. Use `run` for a registered suite, then `verify` and `diff`
  for sealed runs. Register existing standalone reports when the registry supports their
  suite, keeping their original producer provenance. Inspect changed observations, input
  identities, and coverage separately. A `same` result with missing inputs only applies
  to the available evidence.
- For extraction or selection changes, replay old matches, inspect every changed decision,
  and run meaningful changes from the project's corpus rules. Separate raw reads,
  cached evidence and retained input maps.
- Preserve reserved holdout cases for independent review. Machine checks do not
  establish legal meaning. Record an unavailable input as unavailable and a missing
  proof as unknown. Keep this workflow offline.
