# Historical delivery after operational cleanup

The qualification restores the SQL from state commit
`7598f545010b8a9a33dd13c35d0487c7396fcd8a` into two disposable databases.
Keep one copy for the checkpoint. Apply `SqliteBillRepository.prune_history`
to the other with delivery and memo cutoffs of 2027-08-10 and 2027-05-12
at midnight UTC. This simulates the standard 90-day delivery and 180-day memo
policy on 2027-11-08. Use checkpointed SQLite copies.

```sh
uv run python -m tools.primary_delivery_cycle --database after.db \
  --publication-id 258 --delivery-checkpoint before.db "$(shasum -a 256 before.db | cut -d ' ' -f 1)" \
  --output report.json
```

The command exits 2 because the cycle is unobserved in the current copy.
The separate historical result matches the pre-cleanup cycle for publication
258. The hash pin and physical send must match, and both copies are read-only.
A wrong hash or a different physical message is rejected.

The simulated cleanup removed 181 delivery payloads and 130 memos and reduced
the gzipped SQL dump by 2,726,764 bytes. All five confirmed content histories
and five observed secondary delivery suffixes survived. A replay on the
snapshot date, 2026-11-08, removed no payloads. The checkpoint is optional and
requires an existing earlier snapshot. This qualification neither enables new
storage nor changes the production retention policy.

[Cleanup replay](retention.json) · [Checkpoint qualification](checkpoint.json)
