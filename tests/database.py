from contextlib import closing
from functools import cache

from lexinform.adapters.sqlite_repo import SqliteBillRepository

_REPOSITORIES: list[SqliteBillRepository] = []


@cache
def _empty_dump() -> str:
    with closing(SqliteBillRepository(":memory:")) as repository:
        repository.migrate()
        return repository.dump()


def empty_repository() -> SqliteBillRepository:
    """Give each caller an isolated database from the current, genuinely migrated empty schema."""
    repository = SqliteBillRepository(":memory:")
    repository.restore(_empty_dump())
    _REPOSITORIES.append(repository)
    return repository


def close_test_repositories() -> None:
    while _REPOSITORIES:
        _REPOSITORIES.pop().close()
