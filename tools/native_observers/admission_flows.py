import hashlib
import json
import os
from pathlib import Path

import lexinform
from tests.fakes import FakeTextExtractor
from tests.harness import World


def test_admission_flows():
    assert Path(lexinform.__file__).resolve().parents[2] == Path(os.environ['DA_EXPECTED_CODE']).resolve()
    output = Path(os.environ['DA_ADMISSION_OUTPUT'])
    output.mkdir()
    cases = {
        'bill': ('USTAWA\no cudzoziemcach\nArt. 1. Cudzoziemiec składa wniosek o zezwolenie na pobyt.\n'*20, 1),
        'thin_pdf': ('Druk nr 4200. Projekt ustawy. '*20, 30),
        'word_fallback': ('Szanowny Panie Marszałku, na podstawie art. 118 ust. 1 Konstytucji wnoszą projekt ustawy. '+'Zmiany dotyczą pobytu czasowego cudzoziemców. '*200, 0),
        'empty_pdf': ('', 12),
        'empty_word': ('', 0),
        'consultation_report': ('RAPORT Z KONSULTACJI\n'+'Omówienie przebiegu konsultacji i zgłoszonych uwag. '*80, 0),
        'osr': ('OCENA SKUTKÓW REGULACJI\n'+'Wpływ regulacji na sektor finansów publicznych. '*80, 0),
        'thin_word': ('Krótki tekst dokumentu '*15, 0),
    }
    profiles = {}
    for workers in (1, 4):
        for batch in (False, True):
            for neutral in (False, True):
                for label, (text, pages) in cases.items():
                    world = World(extractor=FakeTextExtractor(text, page_count=pages), workers=workers, batch=batch, batch_only=batch)
                    title = 'Projekt ustawy o zmianie niektórych ustaw' if neutral else 'Projekt ustawy o cudzoziemcach'
                    world.add_bill('4200', title)
                    first = world.run(track=False)
                    if batch:
                        world.batch.resolve()
                    world.repo.restore(world.repo.dump())
                    second = world.run(track=False)
                    key = f'{label}-{workers}-{batch}-{neutral}'
                    raw = world.repo.dump()
                    (output/f'{key}.sql').write_text(raw)
                    profiles[key] = {'reports': [first.model_dump(mode='json'), second.model_dump(mode='json')], 'sql_sha256':hashlib.sha256(raw.encode()).hexdigest(), 'requests':[r.model_dump(mode='json') for r in world.batch.submitted], 'contexts':[c.model_dump(mode='json') for c in world.llm.contexts], 'messages':[{'kind':m.kind.value, 'text':m.text} for m in world.publisher.sent], 'edits':[{'text':m.text,'message_id':i} for m,i in world.publisher.edited]}
                    world.repo.close()
    assert len(profiles) == 64
    (output/'profiles.json').write_text(json.dumps(profiles,ensure_ascii=False,indent=2)+'\n')
