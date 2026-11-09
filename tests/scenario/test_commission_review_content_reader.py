import sqlite3
from contextlib import closing
from datetime import timedelta
from unittest.mock import patch

import pytest

from lexinform.models import ContentVersion, PublicationKind
from tests.checkpoint_state import saved_state
from tests.harness import RCL
from tests.scenario.test_commission_reviews import prepared_world


def with_content(dump: str, old: ContentVersion, new: ContentVersion) -> str:
    old_json = old.model_dump_json().replace("'", "''")
    new_json = new.model_dump_json().replace("'", "''")
    assert dump.count(old_json) == 1, "the saved content row uses canonical version JSON"
    return dump.replace(old_json, new_json).replace(old.content_id, new.content_id)


def test_commission_notice_reader_retains_paid_application_after_cleanup() -> None:
    world = prepared_world()
    assert world.run().ok
    world.batch.resolve()
    with patch.object(world.repo, "find_review_application_id", return_value=None):
        assert world.run().commission_reviews_posted == 1
    publication = world.publication(RCL, PublicationKind.COMMISSION_REVIEW)
    assert publication is not None and publication.id is not None
    legacy = world.repo.get_publication_content(publication.id)
    assert legacy is not None and legacy.original_delivery_evidence is not None
    assert legacy.original_delivery_evidence.commission_review_application_id is None
    cutoff = world.clock.now() + timedelta(days=200)
    world.repo.prune_history(before=cutoff, memo_before=cutoff)
    old = world.repo.dump()
    with closing(sqlite3.connect(":memory:")) as raw:
        raw.executescript(old)
        ((application_id,),) = raw.execute(
            "SELECT id FROM result_applications"
            " WHERE json_extract(application_json, '$.target.kind')='document_review'"
        ).fetchall()
    evidence = legacy.original_delivery_evidence.model_copy(
        update={"commission_review_application_id": application_id}
    )
    future = legacy.model_copy(update={"original_delivery_evidence": evidence})
    future_dump = with_content(old, legacy, future)

    with saved_state(old) as durable:
        assert durable.repo.get_publication_content(publication.id) == legacy
        durable.repo.restore(future_dump)
        assert durable.repo.get_publication_content(publication.id) == future
        assert durable.repo.dump() == future_dump
        wrong = evidence.model_copy(
            update={"commission_review_application_id": application_id + 500}
        )
        invalid = future.model_copy(update={"original_delivery_evidence": wrong})
        with pytest.raises(ValueError, match="invalid_content_history"):
            durable.repo.restore(with_content(old, legacy, invalid))
        assert durable.repo.dump() == future_dump
