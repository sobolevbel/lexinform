"""Persist batch submission boundaries outside an ephemeral workflow runner."""

from collections.abc import Callable
from pathlib import Path

from lexinform.adapters.state_checkpoint import GitStateCheckpoint
from lexinform.models import LlmBatch


class GitBatchCheckpoint(GitStateCheckpoint):
    def __init__(
        self,
        dump: Callable[[], str],
        path: Path,
        branch: str,
        pending_batches: Callable[[], list[LlmBatch]],
    ) -> None:
        super().__init__(
            dump,
            path,
            branch,
            pending_batches,
            message="state: batch submission checkpoint",
        )
