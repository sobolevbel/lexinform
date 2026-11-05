# Joined local-clock qualification recipe

Controlled native input plus explicit offline model/Telegram doubles; no production acceptance.
The frozen plan and source metadata must be restored from their linked audit assets, and the
original pinned local inputs must be present. Preserve the recorded hashes; relocation requires
new qualification. SQL/checkpoint bodies stay under ignored `out/` only.

Save the following exact code as `out/corpus/j241-observed-timing/joined-proof/recipe.py`.
Prepare `out/corpus/j238-timing-baseline/case-plan.json` from the OFF baseline plan gzip and
copy this audit's `source-pins.json` to the recipe's `candidate-v5/source-pins.json` input.
Run from the qualified checkout with `PYTHONPATH=src:. .venv/bin/python` and the recorded
tokenizer cache; the script rejects different inputs or runtime pins.

```python
import hashlib,json,sqlite3
from contextlib import closing
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.adapters.document_text import DocumentTextExtractor,DocxTextExtractor
from lexinform.adapters.doc_text import DocTextExtractor
from lexinform.adapters.pdf_text import PypdfTextExtractor
from lexinform.models import PublicationKind,BillStatus
from lexinform.services.analysis import AnalysisService,AnalysisOptions
from lexinform.services.documents import TextLoader
from lexinform.services.posting import Poster
from lexinform.services.sources import MetadataOnlySource
from lexinform.sections import TextBudget
from tests.fakes import FakeBatchBackend,FakeLlm,FakePublisher,FakeSejmGateway,FixedClock
from tools import replay_local_timing as driver
from tools.corpus_runtime import CorpusReads
from tools.observed_timing import observed_timing
from tools.computation_doctor import audit as computation_audit
from tools.content_history_doctor import audit as content_audit
from tools.work_doctor import audit as work_audit

ROOT=Path.cwd();OUT=ROOT/'out/corpus/j241-observed-timing/joined-proof';PRIVATE=OUT/'private'
sha=lambda value:hashlib.sha256(value).hexdigest()
plan_path=ROOT/'out/corpus/j238-timing-baseline/case-plan.json';plan_raw=plan_path.read_bytes();plan=json.loads(plan_raw)
assert sha(plan_raw)=='590535b333fcbd0fce839259c6de344271c44092c21d0aed634756b0c965c17d'
driver_path=ROOT/'tools/replay_local_timing.py';assert sha(driver_path.read_bytes())=='1fc3205c2a55eced262377c394aa7a32c281032aca36fe098bbe44c67fa56383'
j241_pins=json.loads((ROOT/'out/corpus/j241-observed-timing/candidate-v5/source-pins.json').read_text())
assert all(sha((ROOT/name).read_bytes())==value for name,value in j241_pins['files'].items())
source_files=['src/lexinform/local_timing.py','src/lexinform/services/analysis.py','src/lexinform/services/documents.py','src/lexinform/services/analysis_applications.py','src/lexinform/adapters/computation_history.py','tools/observed_timing.py','tools/timing_delivery_links.py','tools/work_doctor.py']
pins={name:sha((ROOT/name).read_bytes()) for name in source_files}
for name,expected in plan['read_inputs'].items():assert sha(Path(name).read_bytes())==expected
bodies={name:Path(asset['path']).read_bytes() for name,asset in plan['assets'].items()}
for name,data in bodies.items():assert sha(data)==plan['assets'][name]['sha256'] and len(data)==plan['assets'][name]['bytes']
captured=[]
class CaptureRepo(SqliteBillRepository):
    def close(self):
        intents=self.list_queued_batch_intents()
        if any(intent.request.call_kind=='analysis' for intent in intents):
            value=self.dump();captured.append(value)
        super().close()
case=next(case for case in plan['cases'] if case['id']=='analysis')
reads=CorpusReads(Path('/Users/sobaleu'));reads.allowed+=tuple(Path(name).resolve() for name in plan.get('tokenizer_resources',{}))
with reads.guard():
    with patch.object(driver,'SqliteBillRepository',CaptureRepo):native=driver.snapshot(plan,bodies,case)
assert len(captured)==1 and len(native['core']['prepared'])==1
(OUT/'native-preparation.json').write_text(json.dumps(native,ensure_ascii=False,indent=2)+'\n')
source_parent_tables=('artifact_reads','artifacts','document_selections','extraction_results','selection_artifacts','selection_editions','selection_extractions','source_observations')
def source_parent_hashes(script):
    with closing(sqlite3.connect(':memory:')) as connection:
        connection.executescript(script)
        values={}
        for table in source_parent_tables:
            rows=connection.execute(f'SELECT rowid,* FROM {table} ORDER BY rowid').fetchall()
            values[table]={'rows':len(rows),'rowids_and_values_sha256':sha(json.dumps(rows,sort_keys=True,ensure_ascii=False).encode())}
        return values
(PRIVATE/'queued.sql').write_text(captured[0])
assert native['core']['client_attempts']==native['core']['submit_attempts']==native['core']['synchronous_attempts']==0
path=PRIVATE/'joined.db';path.unlink(missing_ok=True)
with closing(SqliteBillRepository(path)) as repo:
    repo.migrate();repo.restore(captured[0]);assert repo.dump()==captured[0]
    intents=repo.list_queued_batch_intents();assert len(intents)==1
    intent=intents[0];witness=intent.meta.local_timing_witness
    assert witness is not None and witness.read_completed_at is not None
    assert witness.input_scope==intent.meta.input_binding and witness.request_sha256==intent.request.payload_sha256
    frozen_request_sha=sha(intent.request.model_dump_json().encode());frozen_meta_sha=sha(intent.meta.model_dump_json().encode())
    clock=FixedClock(driver.NOW+timedelta(minutes=10));backend=FakeBatchBackend(id_prefix='technical-offline-joined')
    llm=FakeLlm();loader=TextLoader({},DocumentTextExtractor(PypdfTextExtractor(),DocxTextExtractor(),DocTextExtractor()),max_bytes=8*1024*1024)
    service=AnalysisService(repo,MetadataOnlySource(),loader,llm,clock,AnalysisOptions(require_batch=True,batch_provider='openai',batch_kinds=frozenset({'analysis'}),batch_models={'analysis':'gpt-6.1-sol'},triage_min_chars=1_000_000),text_budget=TextBudget(plan['options']['budget_chars']),batch=backend)
    service.start_run();service.submit_queued_batches()
    assert len(backend.submitted)==1 and sha(backend.submitted[0].model_dump_json().encode())==frozen_request_sha
    batch=repo.list_open_llm_batches()[0];item=repo.list_llm_batch_items(batch.batch_id)[0]
    assert sha(item.meta.model_dump_json().encode())==frozen_meta_sha
    clock.advance(minutes=10);backend.resolve();service.collect_batches()
    retained=repo.list_computation_results(intent.request.term,intent.request.number,purpose='analysis');assert len(retained)==1
    result=retained[0];paid_bytes=result.model_dump_json();assert result.local_timing_witness==witness and result.input_binding==intent.meta.input_binding and result.request_sha256==intent.request.payload_sha256
    clock.advance(minutes=5);applied=service.analyze_pending(limit=1)
    bill=repo.get(intent.request.term,intent.request.number);assert bill is not None and bill.analysis is not None and bill.status==BillStatus.ANALYZED
    assert bill.analysis.computation_id==result.result_id
    projections=[application for application in repo.list_result_applications(bill.term,bill.number) if application.phase=='projection' and application.result_id==result.result_id]
    assert len(projections)==1 and projections[0].projected_generation==bill.analysis_generation
    clock.advance(minutes=5);publisher=FakePublisher(publication_by_id=repo.publication_by_id)
    post=Poster(repo,publisher,clock,gateway=FakeSejmGateway(),channel_id='@technical_joined_clock',max_attempts=3,attention_mode='off')
    queued=post.prepare_message(bill,PublicationKind.NEW_BILL,today=clock.now().date());assert post.deliver(queued)
    physical=repo.publication_by_id(queued.id or 0);assert physical is not None and physical.status.value=='sent' and len(publisher.sent)==1
    observed=driver.NOW+timedelta(minutes=40)
    with closing(sqlite3.connect(path)) as connection:
        before=connection.serialize();report=observed_timing(connection,observed).model_dump(mode='json');assert connection.serialize()==before
    assert next(row for row in report['purposes'] if row['purpose']=='explanation')['physical_evidence']=='no_durable_operator_reply_receipt'
    edges=[edge for edge in report['physical_edges'] if edge['purpose']=='analysis'];assert len(edges)==1
    edge=edges[0];assert edge['status']=='recorded_operation' and edge['association']=='known_request_frozen_slot'
    assert edge['application_to_operation']['seconds']==300 and edge['trigger_to_endpoint']['seconds']==1800 and edge['read_to_endpoint']['seconds']==1800
    collection=next(row for row in report['results'] if row.get('result_id')==result.result_id)
    assert collection['source_availability']=='unknown'
    assert collection['trigger_to_collection']['seconds']==1200 and collection['read_to_collection']['seconds']==1200
    application=next(row for row in report['applications'] if row.get('phase')=='projection' and row.get('result_id')==result.result_id)
    assert application['collection_to_application']['seconds']==300 and application['trigger_to_endpoint']['seconds']==1500
    dump=repo.dump();(PRIVATE/'joined.sql').write_text(dump)
    parent_hashes=source_parent_hashes(captured[0])
    assert source_parent_hashes(dump)==parent_hashes
    doctor_path=PRIVATE/'checkpoint.db'
    def doctors(script):
        doctor_path.unlink(missing_ok=True)
        with closing(sqlite3.connect(doctor_path)) as closed:
            closed.executescript(script)
        return {'computation':computation_audit(doctor_path),'content':content_audit(doctor_path),'work':work_audit(doctor_path,observed_at=observed,include_timing_witnesses=True),'work_v1':work_audit(doctor_path,observed_at=observed)}
    doctors_before=doctors(dump)
    assert doctors_before['work']['latencies']['version']==2
    assert doctors_before['work']['latencies']['physical_edges'][0]['application_to_operation']['seconds']==300
    assert doctors_before['work_v1']['latencies']['version']==1
    repo.restore(dump);repo.restore(dump);assert repo.dump()==dump
    doctors_after=doctors(repo.dump())
    assert doctors_before==doctors_after and repo.get_computation_result(result.result_id).model_dump_json()==paid_bytes
    proof={'status':'passed','native_preparation_metadata_sha256':sha((OUT/'native-preparation.json').read_bytes()),'source_parent_rows':parent_hashes,'source_parent_rowids_and_values_preserved':True,'scope':'Natural pinned native preparation, controlled offline paid-answer/batch and fake Telegram operation; no legal gold or natural production-cycle claim.','plan_sha256':sha(plan_raw),'driver_sha256':sha(driver_path.read_bytes()),'case':case,'source_pins':pins,'J241_V5_source_pins':j241_pins['files'],'J241_V5_patch_sha256':j241_pins['patch_sha256'],'report_populations':report['populations'],'original_input_scope':collection['input_scope'],'retained_owner':collection['retained_owner'],'source_availability':collection['source_availability'],'application_owner':application['owner'],'inputs':{name:{'sha256':asset['sha256'],'bytes':asset['bytes']} for name,asset in plan['assets'].items()},'preparation_refusals':{'client_calls':0,'submit_calls':0,'synchronous_calls':0},'native_read_kind':witness.read_kind,'witness_sha256':sha(witness.model_dump_json().encode()),'queued_request_sha256':frozen_request_sha,'meta_sha256':frozen_meta_sha,'result_id':result.result_id,'retained_input_binding_exact':True,'retained_original_witness_exact':True,'retained_paid_bytes_sha256':sha(paid_bytes.encode()),'paid_generation':result.generation,'actual_projected_generation':projections[0].projected_generation,'actual_application_count':len(projections),'publication_id':physical.id,'recorded_message_id':physical.message_id,'submitted_at':batch.submitted_at.isoformat(),'collected_at':result.collected_at.isoformat(),'projected_at':projections[0].created_at.isoformat(),'sent_at':physical.sent_at.isoformat(),'intervals':{'trigger_to_collection':collection['trigger_to_collection'],'read_to_collection':collection['read_to_collection'],'collection_to_application':application['collection_to_application'],'trigger_to_application':application['trigger_to_endpoint'],'read_to_application':application['read_to_endpoint'],'application_to_operation':edge['application_to_operation'],'trigger_to_operation':edge['trigger_to_endpoint'],'read_to_operation':edge['read_to_endpoint']},'physical_edge':edge,'SQL_dump_sha256':sha(dump.encode()),'double_restore_exact':True,'doctor_full_objects_exact_after_restore':True,'doctor_status':{name:value.get('status') for name,value in doctors_before.items()},'doctor_counts':{name:value.get('counts') for name,value in doctors_before.items()},'doctor_sha256':{name:sha(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()) for name,value in doctors_before.items()},'bodies_SQL_source_copies_tracked':False,'external_network_calls':0,'technical_batch_submissions':len(backend.batches),'fake_telegram_sends':len(publisher.sent),'read_inputs_changed':reads.changed_inputs(),'limitations':['Technical offline batch answer and recorded fake Telegram receipt are not real provider/Telegram observations.','Local clocks do not infer source availability or legal correctness.','V1 exact Wykaz source clock remains separate existing unit evidence.','Root decides DA10.4 acceptance after final report source pins/release; DA10.7 natural acceptance remains separate.']}
assert all(sha((ROOT/name).read_bytes())==value for name,value in pins.items())
assert all(sha((ROOT/name).read_bytes())==value for name,value in j241_pins['files'].items())
assert not reads.changed_inputs()
assert sha(plan_path.read_bytes())==sha(plan_raw)
assert sha(driver_path.read_bytes())=='1fc3205c2a55eced262377c394aa7a32c281032aca36fe098bbe44c67fa56383'
for name,expected in plan['read_inputs'].items():assert sha(Path(name).read_bytes())==expected
for name,asset in plan['assets'].items():assert sha(Path(asset['path']).read_bytes())==asset['sha256']
proof['pinned_plan_assets_resources_and_driver_unchanged']=True
(OUT/'proof.json').write_text(json.dumps(proof,ensure_ascii=False,indent=2)+'\n');print(json.dumps({'status':proof['status'],'result_id':proof['result_id'],'read_kind':proof['native_read_kind'],'generation':(proof['paid_generation'],proof['actual_projected_generation']),'intervals':proof['intervals']},ensure_ascii=False))
```
