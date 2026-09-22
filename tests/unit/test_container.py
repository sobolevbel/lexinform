"""The wiring decisions a test cannot see through a service: what the container builds and when."""

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.adapters.telegram_format import MessageFormatter
from lexinform.container import Container
from lexinform.keywords import KeywordPrefilter
from lexinform.ports import Clock
from lexinform.services.terms import TermResolver
from lexinform.settings import Settings
from tests.fakes import FakeSejmGateway, FixedClock


def _container(*, batching: bool) -> Container:
    repo = SqliteBillRepository(":memory:")
    repo.migrate()
    gateway = FakeSejmGateway()
    clock: Clock = FixedClock()
    return Container(
        settings=Settings(_env_file=None, term=10, llm_batch_enabled=batching),
        clock=clock,
        repo=repo,
        gateway=gateway,
        formatter=MessageFormatter("ru"),
        prefilter=KeywordPrefilter(),
        terms=TermResolver(gateway, repo),
    )


def test_the_batch_backend_is_built_even_with_batching_switched_off() -> None:
    """Switching it off mid-flight would otherwise strand the filed requests, and their bills."""
    off = _container(batching=False)

    assert off.batch_backend() is not None
    assert off.analysis_service() is not None


def test_batching_off_is_what_stops_a_submission() -> None:
    on = _container(batching=True)
    off = _container(batching=False)

    assert on.analysis_options().submit_batches is True
    assert off.analysis_options().submit_batches is False
