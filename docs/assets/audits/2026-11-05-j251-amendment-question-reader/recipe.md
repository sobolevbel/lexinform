# Reproduce the migration comparison

Use the exact J251 candidate source matching source-pins.json (the one test-only closing change is in resource-closing-delta.json), not a later main. The immutable state commit and raw SQL SHA are in [source-manifest.json](source-manifest.json). Restore that dump with baseline source11557f163a2bdb38ba9416c78296078888629dd8 to a new temporary DB, then SQLite-backup it to a separate candidate DB. With J251 source, call migrate, dump, restore twice and compare both dumps exactly. Never run the pipeline or restore into production.

Save the following as probe.py outside either checkout, then run with the same installed Python environment and explicit PYTHONPATH=<checkout>/src:<checkout>, passing the corresponding database path. Use baseline code for the before DB and J251 code for the migrated DB. It emits only hashes/counts; all48old table inventories and509Bill JSON hashes must match, schema moves66→67 and only amendment_input_snapshots is added empty.

```python
import hashlib
import json
import sqlite3
import sys
from pathlib import Path
from lexinform.adapters.sqlite_repo import SqliteBillRepository

path = Path(sys.argv[1])
repo = SqliteBillRepository(path)
with sqlite3.connect(path) as connection:
    tables = [r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    identities = connection.execute('SELECT term,number FROM bills ORDER BY term,number').fetchall()
    schema = connection.execute('PRAGMA user_version').fetchone()[0]
    inventory = {}
    for table in tables:
        quoted = '"' + table.replace('"', '""') + '"'
        rows = connection.execute(f'SELECT rowid,* FROM {quoted} ORDER BY rowid').fetchall()
        inventory[table] = {'rows':len(rows), 'sha256':hashlib.sha256(repr(rows).encode()).hexdigest()}
connection.close()
bills = []
for term, number in identities:
    bill = repo.get(term, number)
    assert bill is not None, 'The persisted identity must load its Bill'
    bills.append(bill.model_dump(mode='json'))
dump = repo.dump()
repo.close()
print(json.dumps({'schema':schema, 'tables':inventory,'bills':len(bills),'bill_json_sha256':hashlib.sha256(json.dumps(bills,sort_keys=True).encode()).hexdigest(),'dump_sha256':hashlib.sha256(dump.encode()).hexdigest()},sort_keys=True))
```

Plain dump delta is len(after.dump().encode()) minus len(before.dump().encode()); gzip uses gzip.compress(..., mtime=0). Run the five default doctor audit functions and batch_integrity_doctor with/without --batch-receipts on each DB; only schema_version66→67 and the correctly measured input file database_sha256 may differ. The detailed expected14paths are in [doctor-comparison.json](doctor-comparison.json).
