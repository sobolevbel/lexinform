from functools import cache

from lexinform.adapters.sqlite_repo import SqliteBillRepository

_REPOSITORIES: list[SqliteBillRepository] = []


@cache
def _empty_template() -> SqliteBillRepository:
    repository = SqliteBillRepository(":memory:")
    repository.migrate()
    return repository


def empty_repository() -> SqliteBillRepository:
    """Give each caller an isolated database copied from the genuinely migrated empty schema."""
    repository = _empty_template().copy_to_memory()
    _REPOSITORIES.append(repository)
    return repository


def close_test_template() -> None:
    if _empty_template.cache_info().currsize:
        _empty_template().close()
        _empty_template.cache_clear()


def close_test_repositories() -> None:
    while _REPOSITORIES:
        _REPOSITORIES.pop().close()
