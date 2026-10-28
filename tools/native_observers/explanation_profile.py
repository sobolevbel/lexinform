import gzip
import hashlib
import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path

import lexinform
import pytest
from lexinform.models import AnalysisRecord, BatchIntent
from lexinform.services.analysis import Waiting
from tests.harness import World


def test_profile():
    assert Path(lexinform.__file__).resolve().parents[2] == Path(os.environ['DA_EXPECTED_CODE']).resolve()
    output = Path(os.environ['DA_EXPLANATION_PROFILE_OUTPUT'])
    output.mkdir()
    profiles = {}
    for model in ('gpt-5.1', 'claude-opus-5-5'):
        for workers in (1, 4):
            for stage in ('warm', 'cold', 'changed', 'legacy'):
                world = World(profile='production', workers=workers, analysis_model=model)
                world.add_bill('3039', 'Projekt ustawy o cudzoziemcach')
                world.run(track=False)
                world.batch.resolve()
                world.run(track=False)
                world.analysis.start_run()
                save = world.repo.save_batch_intent

                def legacy(intent: BatchIntent) -> None:
                    save(intent.model_copy(update={'meta': intent.meta.model_copy(update={'input_binding': None})}))

                with pytest.MonkeyPatch.context() as patch:
                    if stage == 'legacy':
                        patch.setattr(world.repo, 'save_batch_intent', legacy)
                    assert isinstance(world.analysis.explain_bill(world.bill('3039')), Waiting)
                request = world.batch.submitted[-1].model_dump(mode='json')
                world.batch.resolve()
                world.analysis.collect_batches()
                world.analysis.acknowledge_batch_costs()
                original = next(row for row in world.repo.list_computation_results(10, '3039') if row.purpose == 'explanation')
                world.clock.advance(days=200)
                if stage != 'warm':
                    world.repo.prune_history(before=world.clock.now(), memo_before=world.clock.now())
                if stage == 'changed':
                    world.gateway.details['3039'] = world.gateway.details['3039'].model_copy(update={'title': 'Inny projekt ustawy o cudzoziemcach'})
                world.repo.restore(world.repo.dump())
                world.analysis.start_run()
                error, answer = '', None
                try:
                    answer = world.analysis.explain_bill(world.bill('3039'))
                except RuntimeError as exc:
                    error = str(exc)
                raw = world.repo.dump().encode()
                name = f'{model}-{workers}-{stage}'
                (output / f'{name}.sql').write_bytes(raw)
                with closing(sqlite3.connect(':memory:')) as database:
                    database.executescript(raw.decode())
                    assert database.execute('PRAGMA integrity_check').fetchone() == ('ok',)
                    assert not database.execute('PRAGMA foreign_key_check').fetchall()
                    size = len(database.serialize())
                assert world.repo.get_computation_result(original.result_id) == original
                profiles[name] = {'error': error, 'answer': answer.model_dump(mode='json') if isinstance(answer, AnalysisRecord) else None, 'waiting': isinstance(answer, Waiting), 'original_request': request, 'original_result': original.model_dump(mode='json'), 'requests': [row.model_dump(mode='json') for row in world.batch.submitted], 'bill': world.bill('3039').model_dump(mode='json'), 'applications': [row.model_dump(mode='json') for row in world.repo.list_result_applications(10, '3039')], 'messages': [row.text for row in world.publisher.sent], 'edits': [row.text for row, _ in world.publisher.edited], 'sql_sha256': hashlib.sha256(raw).hexdigest(), 'sql_bytes': len(raw), 'gzip_bytes': len(gzip.compress(raw, mtime=0)), 'sqlite_bytes': size}
                world.repo.close()
    (output / 'profiles.json').write_text(json.dumps(profiles, ensure_ascii=False, indent=2) + '\n')
