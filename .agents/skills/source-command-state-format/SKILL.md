---
name: source-command-state-format
description: Stage a change to persisted Lexinform formats for queued requests, saved results, or publication history. Use when a writer would emit data an earlier deployment cannot read.
---

# Persisted format changes

Start with [database rules](../../../.claude/rules/database.md) and the
[architecture release gate](../../../docs/plans/2026-10-22-document-architecture.md#103-переключения-и-rollback).

1. Identify every reader of the changed record, including retry after restart, dump/restore,
   and diagnostics. Identify the earliest revision that can safely read data after activation.
2. Ship the compatible reader with legacy writes intact. Check old, new and malformed
   representations. Where applicable, prove the reconstructed request bytes and identity
   match what was frozen before submission.
3. Verify the reader release with the existing release verifier before activating the writer.
   Qualify the actual writer on a pinned copy with restart and restore. Measure raw SQL and gzip
   size against the same baseline, and record which evidence survives cleanup.
4. Record the verified reader revision as the rollback floor in the architecture journal.
   A disabled writer flag prevents new writes. Records already emitted still need the reader.

Use the corpus replay workflow when source documents are involved. Use the migration skill
when the change also alters the schema.
