import pytest

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.models.batch import BatchKind
from tests.computations import KINDS, retained_result


@pytest.mark.parametrize("purpose", KINDS)
def test_purpose_filter_preserves_owner_order_and_default_history_reader(
    purpose: BatchKind,
) -> None:
    repository = SqliteBillRepository(":memory:")
    repository.migrate()
    expected = [retained_result(kind) for kind in KINDS]
    for result in expected:
        repository.save_computation_result(result)
    before = repository.dump()

    selected = repository.list_computation_results(10, "3039", purpose=purpose)

    assert selected == [result for result in expected if result.purpose == purpose]
    assert repository.list_computation_results(10, "3039") == expected
    assert not repository.list_computation_results(11, "3039", purpose=purpose)
    assert repository.dump() == before
    repository.close()
