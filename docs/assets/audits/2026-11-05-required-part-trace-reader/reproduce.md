# Reproduce reader compatibility

Requires the two immutable source copies named and hashed in [legacy proof](legacy-proof.json), the qualified source [pins](source-pins.json), and the frozen environment. It never fetches sources, calls providers or writes the input databases. Restore checks use temporary memory repositories.

```sh
git show c12929f5c6ec1f0cbc86773f9a4a49c5613498b9:src/lexinform/models/candidate_trace.py > /tmp/j257-old-model.py
git show c12929f5c6ec1f0cbc86773f9a4a49c5613498b9:tools/source_history_doctor.py > /tmp/j257-old-doctor.py
uv run --offline --frozen pytest -q tests/unit/test_required_part_trace_reader.py
```

Run this recipe from the repository root after creating its separate output directory. Adjust only the pinned input copy paths and output path if necessary; require their recorded SHA256 values.

```python
import itertools
import hashlib
import json
import runpy
import sys
from contextlib import closing
from pathlib import Path

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.models import CandidateDispositionTrace
from tools import source_history_doctor
from tools.sqlite_snapshot import open_snapshot

root = Path('/Users/sobaleu/.codex/worktrees/doc-note-links/lexinform')
legacy = runpy.run_path(sys.argv[1] if len(sys.argv) > 1 else 'qualification/base/src/lexinform/models/candidate_trace.py')['CandidateDispositionTrace']
old_doctor = runpy.run_path(sys.argv[2] if len(sys.argv) > 2 else 'qualification/base/tools/source_history_doctor.py')['audit']
results = {}
for name, relative in [('b9', 'out/corpus/j237-card-history-writer/fresh-b.db'), ('fresh', 'out/corpus/j251-amendment-input-reader/fresh-state/baseline.db')]:
    path = root / relative
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    with open_snapshot(path) as snap:
        rows = snap.connection.execute('SELECT trace_id,trace_json FROM candidate_disposition_traces ORDER BY rowid').fetchall()
        views = []
        for row in rows:
            previous = legacy.model_validate_json(row['trace_json'])
            current = CandidateDispositionTrace.model_validate_json(row['trace_json'])
            assert current.trace_id == previous.trace_id == row['trace_id']
            assert current.model_dump_json(exclude_defaults=True) == previous.model_dump_json(exclude_defaults=True)
            assert current.required_exclusions is None
            views.append(current)
        script = '\n'.join(snap.connection.iterdump()) + f'\nPRAGMA user_version = {snap.connection.execute("PRAGMA user_version").fetchone()[0]};\n'
    with closing(SqliteBillRepository(':memory:')) as repo:
        repo.migrate(); repo.restore(script)
        assert [repo.get_candidate_disposition_trace(item.trace_id) for item in views] == views
        saved = repo.dump(); repo.restore(saved); assert repo.dump() == saved
    flags = [dict(zip(("include_candidate_traces", "include_selection_coverage", "include_selection_limitations"), values, strict=True)) for values in itertools.product((False, True), repeat=3)]
    for options in flags:
        previous = json.dumps(old_doctor(path, **options), sort_keys=True, separators=(',', ':'))
        current = json.dumps(source_history_doctor.audit(path, **options), sort_keys=True, separators=(',', ':'))
        assert current == previous
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    results[name] = {'source_sha256': before, 'legacy_traces': len(rows), 'all_old_json_ids_exact': True, 'public_get_restore_double_restore_exact': True, 'eight_default_and_opt_in_source_reports_byte_exact': True, 'source_unchanged': True}
Path('out/corpus/j257-required-exclusion/root-qa/legacy-proof.json').write_text(json.dumps(results, indent=2) + '\n')
print(json.dumps(results))
```

Supply `/tmp/j257-old-model.py` and `/tmp/j257-old-doctor.py` as its two arguments. The old source module uses the same unchanged shared infrastructure; direct old/new model equality is independently checked per actual saved row before the full public restore/report replay. Corrupt duplicate/deep JSON and new typed-field admission are separate unit controls; they are not natural corruption claims. Runtime writer B is absent.
