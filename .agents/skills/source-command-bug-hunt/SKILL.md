---
name: source-command-bug-hunt
description: Investigate recurring Lexinform tracking and delivery defects using offline legislative snapshots and targeted regressions. Use for a bug hunt across lifecycle transitions, missing or duplicate updates, and replay convergence.
---

# Offline bug hunt

Start with the recurring defect classes in [`docs/BUGS.md`](../../../docs/BUGS.md)
and the area rules linked from [`AGENTS.md`](../../../AGENTS.md). Choose a
failure pattern and a cohort that could disprove it. Look for existing probes in
the external corpus checkout under `checks/`, especially the tracker,
idempotence and run-order probes. Use the saved inputs and fakes with zero model
calls.

- Replay each observation using only fields available at that time. Final
  closure dates or ELI values in a saved response can make a historical replay
  end prematurely. Feed each stage tree through flattening once.
- Query publications with the actual term and print number. Separate held
  changes from lost publications and inspect the relevant delivery state.
- Reproduce a confirmed defect with the smallest failing test. Use `World` when
  the failure depends on service interactions or a durable transition. Re-run
  the affected corpus cohort after the fix. Record baseline, candidate,
  denominator and exclusions in the bug registry or existing audit.
