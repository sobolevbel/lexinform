# Reproduce the limitation census

Use immutable local copies of the pinned databases named in [pinned proof](pinned-proof.json), the qualified source from [pins](source-pins.json), and the frozen environment. No provider or source download is needed.

```sh
uv run --offline --frozen python -m tools.source_history_doctor --database COPY.db --output out/limitations.json --selection-limitations
uv run --offline --frozen pytest -q tests/unit/test_selection_state_coverage.py
```

For exact prior-output comparison, load the old source-doctor from commit `f9a4efa97ae97556a9eeef98575f9d2763649a5a` alongside the current module, and compare sorted compact JSON for all four combinations of `include_selection_coverage` and `include_candidate_traces`. Both functions receive the same immutable database. The new flag stays false for these comparisons. With the new flag true, remove only `selection_limitation_coverage` and compare the remainder against the prior report; require total equals known rows plus unattributed rows, and unchanged database SHA before/after.

The native missing-annex, overlapping fractions, unknown denominator and legacy absent-field controls live in the unit module. Natural50/54observation populations verify report preservation and recorded-code census, not a missing-annex incident or legal completeness.

Run the exact comparison in the repository with `COPY.db` set to either pinned input:

```sh
git show f9a4efa97ae97556a9eeef98575f9d2763649a5a:tools/source_history_doctor.py > /tmp/j255-source-baseline.py
uv run --offline --frozen python - COPY.db <<'PYCODE'
import hashlib
import itertools
import json
import runpy
import sys
from pathlib import Path
from tools.source_history_doctor import audit

previous = runpy.run_path("/tmp/j255-source-baseline.py")["audit"]
path = Path(sys.argv[1])
before = hashlib.sha256(path.read_bytes()).hexdigest()
for coverage, candidates in itertools.product((False, True), repeat=2):
    options = {"include_selection_coverage": coverage, "include_candidate_traces": candidates}
    old = json.dumps(previous(path, **options), sort_keys=True, separators=(",", ":"))
    new = json.dumps(audit(path, **options), sort_keys=True, separators=(",", ":"))
    assert new == old
expanded = audit(path, include_selection_limitations=True, include_selection_coverage=True)
limits = expanded.pop("selection_limitation_coverage")
assert expanded == audit(path, include_selection_coverage=True)
assert limits["total"] == limits["known_limitations_rows"] + limits["unattributed"]
assert hashlib.sha256(path.read_bytes()).hexdigest() == before
print(json.dumps(limits, sort_keys=True))
PYCODE
```
