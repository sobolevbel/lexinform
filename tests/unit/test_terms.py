from datetime import UTC, datetime

import pytest

from lexinform.errors import SejmApiUnavailableError
from lexinform.services.terms import TermResolver
from tests.database import empty_repository
from tests.fakes import FakeSejmGateway
from tests.harness import summary


@pytest.mark.parametrize("start_first", [False, True])
def test_term_outage_is_shared_by_current_and_start_lookups(start_first: bool) -> None:
    gateway = FakeSejmGateway(outages={"list_terms"})
    repo = empty_repository()
    repo.upsert_summary(summary("3039", "Projekt ustawy"), now=datetime(2026, 11, 12, tzinfo=UTC))
    resolver = TermResolver(gateway, repo)

    if start_first:
        assert resolver.started(10) is None
    assert resolver.current() == 10
    assert resolver.started(10) is None

    assert gateway.calls == ["list_terms"]


def test_term_outage_with_no_stored_term_keeps_the_original_error() -> None:
    gateway = FakeSejmGateway(outages={"list_terms"})
    resolver = TermResolver(gateway, empty_repository())

    assert resolver.started(10) is None
    with pytest.raises(SejmApiUnavailableError, match="list_terms: connection refused"):
        resolver.current()

    assert gateway.calls == ["list_terms"]


def test_pinned_term_does_not_require_the_listing() -> None:
    gateway = FakeSejmGateway(outages={"list_terms"})
    resolver = TermResolver(gateway, empty_repository(), pinned=9)

    assert resolver.current() == 9
    assert gateway.calls == []
    assert resolver.started(9) is None
    assert resolver.current() == 9
    assert gateway.calls == ["list_terms"]
