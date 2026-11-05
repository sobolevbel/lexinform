# Reproduce the writer qualification

Requires the pinned corpus INDEX, raw cohort, cached input copies and prior published runners named in [corpus proof](corpus-proof.json). All recipes run offline with immutable input guards. Missing inputs remain explicit. Copy these exact frozen snippets into an owned ignored qualification directory; their recorded source paths identify prerequisite files. No provider or production pipeline is needed.

```sh
uv run --offline --frozen pytest -q tests/unit/test_required_part_writer.py
```

The existing raw-priority runner is unchanged; compare natural/synthetic core objects with its pinned OFF report, allowing only the source fingerprint. The cached and native technical recipes below are frozen before/after comparison instruments, not production tools. The technical OFF assertion must fail only on the newly retained trace; the ON shim preserves that refusal and resumes real public source/receipt/prompt calls without masking the trace. Only its legacy file-core comparison excludes that declared sidecar.

## cached_off.py

```python
import hashlib
import json
import logging
from collections import Counter
from pathlib import Path
from unittest.mock import patch

from lexinform.adapters.llm_prompts import build_user_prompt
from lexinform.document_admissibility import analysis_admission, prefilter_admission, review_admission
from lexinform.document_fingerprints import text_digest
from lexinform.models import AnalysisQuestion, ApplicantType, BatchRequest, BillContext
from lexinform.sections import APPENDIX_KINDS, document_kind
from lexinform.services import documents
from lexinform.services.document_history import DocumentSourceRead
from lexinform.services.input_receipts import capture_primary_input_receipt
from tests.fakes import FakeBatchBackend
from tools import replay_document_candidates
from tools.replay_archive_dispositions import file_core, hashed

logging.basicConfig(level=logging.ERROR)
counts = Counter()
cores = []
io = []
select = documents.TextLoader.select_document
read = documents.TextLoader.read
required = documents.required_part
backend = FakeBatchBackend()


def observe_core(loader, requested, selected, loaded):
    source = loader.capture_source_read(requested, selected, loaded)
    row = file_core(selected, loaded)
    row['normalized_text_digest'] = text_digest(loaded.text or '')
    row['admission'] = {
        'prefilter': prefilter_admission(loaded.selection, loaded.text, pages=loaded.pages, min_chars=documents.MIN_TEXT_CHARS),
        'analysis': analysis_admission(loaded.selection, loaded.text, pages=loaded.pages, min_chars=documents.MIN_TEXT_CHARS),
        'review_project_text': review_admission(loaded.selection, loaded.text or '', pages=loaded.pages, min_chars=documents.MIN_TEXT_CHARS, oversize=loaded.oversize, role='project_text'),
    }
    row['source_capture'] = {'kind': type(source).__name__, 'artifacts_sha256': hashed([v.model_dump(mode='json') for v in source.artifacts]), 'reads_sha256': hashed([v.model_dump(mode='json') for v in source.reads]), 'selection_id': source.selection.selection_id if isinstance(source, DocumentSourceRead) else None}
    if loaded.text is not None:
        context = BillContext(number='OFF', title='Technical fixed-owner OFF probe', description=None, document_date=None, applicant_type=ApplicantType.GOVERNMENT, text=loaded.text, truncated=False, text_source='pdf', source_kind=selected.kind)
        request = backend.prepare_request(BatchRequest(custom_id='OFF', term=10, number='OFF', model='technical-offline', question=AnalysisQuestion(kind='analysis', ctx=context)))
        row['technical_question_sha256'] = hashed(request.question.model_dump(mode='json'))
        row['technical_prepared_request_sha256'] = request.payload_sha256
        row['public_prompt_builder_sha256'] = hashlib.sha256(build_user_prompt(context).encode()).hexdigest()
        receipt = capture_primary_input_receipt(request, loaded.selection, None, source)
        row['technical_input_receipt_sha256'] = hashed(receipt.model_dump(mode='json')) if receipt is not None else None
        counts['technical_prepared_questions'] += 1
        counts['technical_receipts'] += receipt is not None
    return row


def observed_select(loader, requested):
    selected, loaded = select(loader, requested)
    cores.append(observe_core(loader, requested, selected, loaded))
    return selected, loaded


def observed_read(loader, url):
    before = loader.read_metrics().cache_hits
    try:
        result = read(loader, url)
    except Exception as error:
        io.append({'url_sha256': hashed(url), 'error': type(error).__name__})
        raise
    io.append({'url_sha256': hashed(url), 'cache_hit': loader.read_metrics().cache_hits > before, 'returned_body_sha256': result.sha256, 'text_sha256': hashlib.sha256((result.text or '').encode()).hexdigest()})
    return result


def observed_required(bundle, url, file):
    counts['executed_required_parts'] += 1
    if file.text is not None and document_kind(file.text) in APPENDIX_KINDS - {'annex'}:
        counts['exact_excluded_required_non_annex'] += 1
    return required(bundle, url, file)


with patch.object(documents.TextLoader, 'select_document', observed_select), patch.object(documents.TextLoader, 'read', observed_read), patch.object(documents, 'required_part', observed_required):
    result = replay_document_candidates.replay(Path('/Users/sobaleu/projects/lexinform-corpus'))
Path('qualification/cached-off.json').write_text(json.dumps(result, sort_keys=True, separators=(',', ':')) + '\n')
Path('qualification/cached-cores.json').write_text(json.dumps({'scope': 'Actual cached public loader/text/digest/admission/source-capture/IO; prepared question/input receipt use explicit fixed technical owner, no claim of historical or native fitted paid questions.', 'counts': dict(counts), 'selection_calls': len(cores), 'io_calls': len(io), 'cores': cores, 'io': io}, sort_keys=True, separators=(',', ':')) + '\n')
print(json.dumps({'cases': len(result['results']), 'states': result['counts'], 'read_inputs': len(result['read_inputs']), 'missing_inputs': len(result['missing_inputs']), 'counts': dict(counts), 'source_changed': False}))
```

## technical_off.py

```python
import hashlib
import json
from pathlib import Path

from lexinform.adapters.llm_prompts import build_user_prompt
from lexinform.document_admissibility import analysis_admission, prefilter_admission, review_admission
from lexinform.document_fingerprints import text_digest
from lexinform.models import AnalysisQuestion, ApplicantType, BatchRequest, BillContext, TextDocument
from lexinform.services.document_history import DocumentSourceRead
from lexinform.services.documents import MIN_TEXT_CHARS, TextLoader
from lexinform.services.input_receipts import capture_primary_input_receipt
from tests.fakes import FakeBatchBackend, FakeTextExtractor
from tools.replay_archive_dispositions import file_core, hashed

body = ('Projekt z dnia 1 września 2026 r.\nUSTAWA\nz dnia …\no zmianie ustawy o cudzoziemcach\nArt. 1. Cudzoziemiec składa wniosek w terminie 30 dni.\nArt. 2. Organ rozpatruje wniosek i doręcza decyzję stronie postępowania.\nArt. 3. Ustawa wchodzi w życie po upływie 14 dni od dnia ogłoszenia.\n' + 'Cudzoziemiec składa wniosek. ' * 30).encode()
extra = ('Projekt\nROZPORZĄDZENIE\nMINISTRA ...\n' + 'r' * 500).encode()
inputs = {'https://off.test/main': body, 'https://off.test/required': extra}
calls = []


def download(url, *, max_bytes=None):
    value = inputs[url]
    assert max_bytes is None or len(value) <= max_bytes
    calls.append({'url_sha256': hashed(url), 'body_sha256': hashlib.sha256(value).hexdigest(), 'bytes': len(value)})
    return value


loader = TextLoader({'off.test': download}, FakeTextExtractor(by_content={value: value.decode() for value in inputs.values()}, page_count=0), max_bytes=100000)
document = TextDocument(url='https://off.test/main', kind='print', required_urls=('https://off.test/required',), version_basis=('official/technical-off-relation',))
selected, loaded = loader.select_document(document)
assert loaded.text is not None and len(calls) == 2
assert not loaded.selection.rejected and not loaded.selection.observed_parts and loaded.candidate_trace is None
source = loader.capture_source_read(document, selected, loaded)
context = BillContext(number='3039', title='Technical OFF original title', description=None, document_date=None, applicant_type=ApplicantType.GOVERNMENT, text=loaded.text, truncated=False, text_source='pdf', source_kind=selected.kind)
request = FakeBatchBackend().prepare_request(BatchRequest(custom_id='technical-off', term=10, number='3039', model='technical-offline', question=AnalysisQuestion(kind='analysis', ctx=context)))
receipt = capture_primary_input_receipt(request, loaded.selection, None, source)
assert receipt is not None and receipt.request_sha256 == request.payload_sha256
row = {'scope': 'Explicit technical native loader + fake text extraction/fake preparation; no natural/legal/provider/SQL application claim.', 'input_pins': calls, 'file_core': file_core(selected, loaded), 'normalized_text_digest': text_digest(loaded.text), 'admission': {'prefilter': prefilter_admission(loaded.selection, loaded.text, pages=loaded.pages, min_chars=MIN_TEXT_CHARS), 'analysis': analysis_admission(loaded.selection, loaded.text, pages=loaded.pages, min_chars=MIN_TEXT_CHARS), 'review_project_text': review_admission(loaded.selection, loaded.text, pages=loaded.pages, min_chars=MIN_TEXT_CHARS, oversize=loaded.oversize, role='project_text')}, 'technical_question_sha256': hashed(request.question.model_dump(mode='json')), 'technical_prepared_request_sha256': request.payload_sha256, 'public_prompt_builder_sha256': hashlib.sha256(build_user_prompt(context).encode()).hexdigest(), 'input_receipt': receipt.model_dump(mode='json'), 'source_capture': {'kind': type(source).__name__, 'selection_id': source.selection.selection_id if isinstance(source, DocumentSourceRead) else None, 'artifacts_sha256': hashed([v.model_dump(mode='json') for v in source.artifacts]), 'reads_sha256': hashed([v.model_dump(mode='json') for v in source.reads])}, 'read_metrics': loader.read_metrics().model_dump(mode='json'), 'required_part_calls': 1, 'exact_excluded_required_non_annex': 1, 'off_observed_descriptors': 0, 'off_exclusion_reasons': 0, 'off_trace_retained': False, 'no_network_provider_or_pipeline': True}
Path('qualification/technical-off.json').write_text(json.dumps(row, indent=2) + '\n')
print(json.dumps({'exact_branch': 1, 'downloads': len(calls), 'input_receipt': True, 'admission': row['admission'], 'paid_calls': 0}))
```

## technical_on.py

```python
import hashlib
import json
from dataclasses import replace
from pathlib import Path

from tools.replay_archive_dispositions import file_core

recipe = Path('qualification/technical_off.py').read_text()
namespace = {'__name__': '__technical_off_preserved__'}
try:
    exec(compile(recipe, 'qualification/technical_off.py', 'exec'), namespace)
except AssertionError as error:
    tb = error.__traceback__
    while tb.tb_next is not None:
        tb = tb.tb_next
    assert 'loaded.candidate_trace is None' in recipe.splitlines()[tb.tb_lineno - 1]
else:
    raise AssertionError('the frozen OFF trace-absence refusal must remain active')
loaded = namespace['loaded']
trace = loaded.candidate_trace
assert trace is not None and trace.required_exclusions is not None
assert trace.required_exclusions.observed_rows == 1
assert trace.required_exclusions.rows[0].required_index == 0
assert not loaded.selection.rejected and not loaded.selection.observed_parts
suffix = recipe[recipe.index('source = loader.capture_source_read'):recipe.index("Path('qualification/technical-off.json')")]
exec(compile(suffix, '<same-public-technical-tail>', 'exec'), namespace)
row = namespace['row']
off = json.loads(Path('/private/tmp/lexinform-j257-writer-off/qualification/technical-off.json').read_text())
legacy_core = file_core(namespace['selected'], replace(loaded, candidate_trace=None))
assert legacy_core == off['file_core']
core_keys = sorted(set(off) - {'file_core'})
assert {k: row[k] for k in core_keys} == {k: off[k] for k in core_keys}
assert row['file_core'] != off['file_core']
result = {
    'scope': 'Native public technical branch, fake extraction/preparation; no natural/legal/provider claim.',
    'frozen_recipe_sha256': hashlib.sha256(recipe.encode()).hexdigest(),
    'off_refusal_preserved': True,
    'legacy_core_exact_after_only_candidate_trace_removed': True,
    'all_other_public_core_fields_exact': core_keys,
    'intentional_delta': {'candidate_trace': trace.model_dump(mode='json', exclude_defaults=True)},
    'observed_descriptors': len(trace.required_exclusions.rows[0].parts),
    'new_trace_bytes': len(trace.model_dump_json(exclude_defaults=True).encode()),
    'actual_downloads': len(namespace['calls']),
    'actual_known_file_sha256': trace.required_exclusions.rows[0].file_sha256,
    'actual_returned_text_sha256': trace.required_exclusions.rows[0].text_sha256,
    'paid_calls': 0,
}
Path('qualification/technical-on.json').write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps({k: v for k, v in result.items() if k != 'intentional_delta'}))
```
