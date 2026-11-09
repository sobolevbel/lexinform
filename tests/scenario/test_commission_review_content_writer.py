from datetime import timedelta
from typing import Literal
from unittest.mock import patch

import pytest

from lexinform.adapters import review_content_evidence
from lexinform.models import PublicationKind
from lexinform.services import document_reviews
from tests.harness import RCL, World
from tests.scenario.test_commission_reviews import prepared_world


def published(mode: Literal["recorded", "missing", "invalid"]) -> World:
    world = prepared_world()
    assert world.run().ok
    world.batch.resolve()
    if mode == "missing":
        with patch.object(document_reviews, "record_secondary_application"):
            assert world.run().commission_reviews_posted == 1
    elif mode == "invalid":
        with patch.object(
            review_content_evidence, "read_result_application", side_effect=ValueError("invalid")
        ):
            assert world.run().commission_reviews_posted == 1
    else:
        assert world.run().commission_reviews_posted == 1
    return world


@pytest.mark.parametrize("fallback", ["missing", "invalid"])
def test_review_link_freeze_does_not_change_delivery_and_survives_cleanup(
    fallback: Literal["missing", "invalid"],
) -> None:
    normal, legacy = published("recorded"), published(fallback)
    assert normal.publisher.sent == legacy.publisher.sent
    for world in (normal, legacy):
        pub = world.publication(RCL, PublicationKind.COMMISSION_REVIEW)
        assert pub is not None and pub.id is not None
        original = world.repo.get_publication_content(pub.id)
        assert original is not None and original.original_delivery_evidence is not None
        assert (
            original.original_delivery_evidence.commission_review_application_id is not None
        ) == (world is normal)
        cutoff = world.clock.now() + timedelta(days=200)
        world.repo.prune_history(before=cutoff, memo_before=cutoff)
        after = world.repo.dump()
        world.repo.restore(after)
        assert world.repo.dump() == after
        assert world.repo.get_publication_content(pub.id) == original
