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


def test_secondary_batch_options_keep_each_call_model_and_selected_kinds() -> None:
    c = _container(batching=True)
    c.settings.llm_batch_kinds = frozenset({"joint", "supplement"})
    c.settings.llm_joint_model = "gpt-5.1"
    c.settings.llm_supplement_model = "claude-sonnet-5"
    options = c.analysis_options()
    assert options.batch_kinds == frozenset({"joint", "supplement"})
    assert options.batch_models["joint"] == "gpt-5.1"
    assert options.batch_models["supplement"] == "claude-sonnet-5"
    assert options.batch_max_wait.total_seconds() == 6 * 3600
