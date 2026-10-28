import gzip
import hashlib
import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path

import lexinform
from lexinform.models import BillStatus
from lexinform.services.analysis import Waiting
from tests.harness import RCL, World, summary
from tests.scenario.test_commission_reviews import context as commission_context
from tests.scenario.test_document_reviews import context


def snapshot(world, output, name):
    raw = world.repo.dump().encode()
    (output / f'{name}.sql').write_bytes(raw)
    with closing(sqlite3.connect(':memory:')) as database:
        database.executescript(raw.decode())
        assert database.execute('PRAGMA integrity_check').fetchone() == ('ok',)
        assert not database.execute('PRAGMA foreign_key_check').fetchall()
        size = len(database.serialize())
    return {'sql_sha256': hashlib.sha256(raw).hexdigest(), 'sql_bytes': len(raw), 'gzip_bytes': len(gzip.compress(raw, mtime=0)), 'sqlite_bytes': size, 'source': world.bill(RCL).model_dump(mode='json'), 'target': world.bill('5').model_dump(mode='json'), 'source_results': [row.model_dump(mode='json') for row in world.repo.list_computation_results(10, RCL)], 'target_results': [row.model_dump(mode='json') for row in world.repo.list_computation_results(10, '5')], 'requests': [row.model_dump(mode='json') for row in world.batch.submitted], 'messages': [row.text for row in world.publisher.sent], 'edits': [row.text for row, _ in world.publisher.edited]}


def test_profile():
    assert Path(lexinform.__file__).resolve().parents[2] == Path(os.environ['DA_EXPECTED_CODE']).resolve()
    output = Path(os.environ['DA_HANDOFF_PROFILE_OUTPUT'])
    output.mkdir()
    profiles = {}
    for model in ('gpt-5.1', 'claude-opus-5-5'):
        for workers in (1, 4):
            for stage in ('open', 'ready', 'consultation-queued', 'consultation-open', 'commission-queued', 'commission-open'):
                world = World(profile='production', workers=workers, analysis_model=model, batch_kinds=frozenset({'analysis', 'reanalysis', 'consultation_review', 'commission_review'}), consultation_reviews_enabled=True, commission_reviews_enabled=True)
                project = world.add_rcl_project()
                world.run()
                source = None
                if '-' in stage:
                    source = (commission_context() if stage.startswith('commission-') else context()).model_copy(update={'number': RCL, 'title': project.title})
                    assert isinstance(world.analysis.request_document_review(world.bill(RCL), source), Waiting)
                    if stage.endswith('-open'):
                        world.analysis.submit_queued_batches()
                elif stage == 'ready':
                    world.batch.resolve()
                    world.analysis.collect_batches()
                world.repo.upsert_summary(summary('5', project.title), now=world.clock.now())
                world.repo.set_status(10, '5', BillStatus.BATCH_PENDING)
                frozen = [row.model_dump(mode='json') for row in world.batch.submitted]
                queued = [row.model_dump(mode='json') for row in world.repo.list_queued_batch_intents()]
                transfer = getattr(world.repo, 'move_primary_analysis_work', None) or getattr(world.repo, 'move_batch_work')
                with world.repo.atomic():
                    transfer(10, RCL, '5')
                    world.repo.link_bills(10, RCL, '5')
                after_queue = [row.model_dump(mode='json') for row in world.repo.list_queued_batch_intents()]
                world.analysis.submit_queued_batches()
                world.repo.restore(world.repo.dump())
                world.batch.resolve()
                error = ''
                try:
                    world.analysis.collect_batches()
                except ValueError as exc:
                    error = str(exc).split('[type=')[0].strip()
                name = f'{model}-{workers}-{stage}'
                profiles[name] = {'error': error, 'frozen_before': frozen, 'queued_before': queued, 'queued_after': after_queue, 'snapshot': snapshot(world, output, name)}
                world.repo.close()
    (output / 'profiles.json').write_text(json.dumps(profiles, ensure_ascii=False, indent=2) + '\n')
