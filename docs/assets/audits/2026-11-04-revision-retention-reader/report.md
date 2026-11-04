# Retained amendment revision reader

The revision listing now includes publications whose operational delivery payload is absent
when their immutable content binding identifies the requested original. Bound version
identity, reason and owner are checked; conflicting JSON/index ownership fails closed.
Existing inline revisions keep their original listing behavior and descending order.

Binding admission retains one shared validation path, extracted for later cleanup reuse.
The blanket retention exclusion for amendment revisions remains in force. This release
does not clear revision payloads; the cleanup writer waits for actual deployed reader proof.

The existing completed-work lifecycle scenario now checks listing after manual removal
of original/revision operational payloads and dump/restore. Its existing missing/wrong
binding controls still reject corruption. No new scenario is added in this reader stage.
Unit tests remain the default; scenarios are reserved for required service/lifecycle edges.

```sh
uv run --frozen pytest -q tests/scenario/test_revision_application_runtime.py
```

Pinned state105de577 was restored in memory from its original SQL. Both original roots
and all three legacy revisions retain exact SQL-derived IDs/order. The canonical dump
is unchanged and matches the previous restore proof. No naturally pruned revisions were
observed; future cleanup acceptance uses separate meaningful lifecycle and unit controls.

No extraction, selection, text comparison, schema, backend or paid purpose changes.
DA6.6 remains partial until its other retention obligations are qualified.

[Qualification](qualification.json), [isolated state](fresh-state-proof.json),
[independent review](peer-review.json).
