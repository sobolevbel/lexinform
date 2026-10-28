from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from typing import Protocol

from lexinform.adapters.sqlite_repo import SqliteBillRepository
from lexinform.models import Publication, PublicationKind
from tests.harness import CHANNEL, TERM


class StoredState(Protocol):
    @property
    def repo(self) -> SqliteBillRepository: ...

    def publication(
        self, number: str, kind: PublicationKind = PublicationKind.NEW_BILL
    ) -> Publication | None: ...


@dataclass(frozen=True)
class SavedState:
    """Read a checkpoint through the real repository without constructing a pipeline."""

    repo: SqliteBillRepository

    def publication(
        self, number: str, kind: PublicationKind = PublicationKind.NEW_BILL
    ) -> Publication | None:
        return self.repo.get_publication(TERM, number, kind, CHANNEL)


@contextmanager
def saved_state(script: str) -> Iterator[SavedState]:
    with closing(SqliteBillRepository(":memory:")) as repository:
        repository.restore(script)
        yield SavedState(repository)
