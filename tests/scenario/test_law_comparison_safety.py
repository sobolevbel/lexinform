"""Unsafe legacy identities and small material changes must reach a new reading."""

import hashlib
import json
import re
from dataclasses import replace

import pytest

from lexinform.law_digest import law_digest
from lexinform.models.report import CallKind
from lexinform.services.analysis import Waiting, text_digest
from tests.fakes import FakeTextExtractor
from tests.harness import TERM, World, print_url
from tests.scenario.test_batch_only import SECONDARY_KINDS, _secondary_world
from tests.scenario.test_law_fingerprint import _analysed_print_then_a_new_version

ACT = """Art. 1. Przepisy dotyczą cudzoziemców zamieszkałych na terytorium Polski.
Wysokość opłaty wynosi 92,67 zł. Opłata za wydanie dokumentu:
1000
Art. 2. Wniosek składa się w terminie 14 dni. Organ wydaje decyzję w terminie 30 dni.
1) Niniejszą ustawą zmienia się ustawę o obywatelstwie polskim.\fCudzoziemiec nie może złożyć wniosku po upływie 30 dni.
Art. 3. Ustawa wchodzi w życie z dniem 1 stycznia 2027 r.
"""


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("92,67", "9267"),
        ("1000", "1001"),
        ("nie może", "może"),
        ("upływie 30 dni", "upływie 31 dni"),
    ],
)
def test_material_changes_survive_both_early_fingerprints(old: str, new: str) -> None:
    changed = ACT.replace(old, new)
    w = World(
        extractor=FakeTextExtractor(
            by_content={b"%PDF-druk": ACT, b"%PDF-new": changed},
            page_count=1,
        )
    )
    _analysed_print_then_a_new_version(w, b"%PDF-new")

    report = w.run()

    assert report.reanalyzed == 1
    assert len(w.llm.contexts) == 2
    assert w.llm.change_contexts
    assert w.bill("3039").analysis is not None


@pytest.mark.parametrize("replace_source", [False, True])
def test_unversioned_hashes_cannot_certify_a_reloaded_baseline(replace_source: bool) -> None:
    changed = ACT.replace("1000", "1001")
    w = World(
        extractor=FakeTextExtractor(
            by_content={b"%PDF-druk": ACT, b"%PDF-new": changed},
            page_count=1,
        )
    )
    _analysed_print_then_a_new_version(w, b"%PDF-new")
    previous = w.bill("3039").analysis
    assert previous is not None
    w.repo.save_analysis(
        TERM,
        "3039",
        previous.model_copy(
            update={
                "text_sha256": text_digest(changed).split(":")[-1],
                "law_sha256": str(law_digest(changed)).split(":")[-1],
            }
        ),
    )
    if replace_source:
        w.gateway.files[print_url("3039")] = b"%PDF-new"
    w.pipeline = replace(w.container).pipeline(dry_run=False)

    report = w.run()

    assert report.reanalyzed == 1
    assert not w.llm.change_contexts
    stored = w.bill("3039").analysis
    assert stored is not None and stored.text_sha256 == text_digest(changed)


@pytest.mark.parametrize("kind", SECONDARY_KINDS)
@pytest.mark.parametrize("state", ["queued", "submitting", "open"])
def test_legacy_secondary_work_is_not_resubmitted_under_a_new_hash(
    kind: CallKind, state: str
) -> None:
    w, overflow, analysis, ask = _secondary_world(kind)
    first = ask()
    assert isinstance(first, Waiting)
    (intent,) = w.repo.list_queued_batch_intents()
    ctx = intent.request.ctx
    data = ctx.model_dump(mode="json", exclude={"scan", "text"})
    text = str(ctx.model_dump().get("text", ""))
    text = re.sub(r"^\s*[–\-—]?\s*\d{1,4}\s*[–\-—]?\s*$", "", text, flags=re.MULTILINE)
    data["text_sha256"] = hashlib.sha256(re.sub(r"\s+", " ", text).strip().encode()).hexdigest()
    data.update(term=TERM, purpose=kind)
    legacy_key = hashlib.sha256(
        json.dumps(data, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    assert legacy_key != intent.request.custom_id
    w.repo.delete_batch_intents([intent.request.custom_id])
    w.repo.save_batch_intent(
        intent.model_copy(
            update={
                "request": intent.request.model_copy(update={"custom_id": legacy_key}),
                "meta": intent.meta.model_copy(update={"memo_key": legacy_key}),
            }
        )
    )
    if state == "submitting":
        w.repo.mark_batch_intents_submitting([legacy_key])
    elif state == "open":
        analysis.submit_queued_batches()
    w.repo.restore(w.repo.dump())
    w.clock.advance(days=3)
    analysis.start_run()

    assert ask() == first
    assert all(item.request.custom_id == legacy_key for item in w.repo.list_queued_batch_intents())
    assert (
        not w.llm.amendment_contexts and not w.llm.supplement_contexts and not w.llm.joint_contexts
    )
    assert len(overflow.submitted) == (1 if state == "open" else 0)
