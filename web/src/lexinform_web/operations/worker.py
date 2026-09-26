import fcntl
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from uuid import UUID

from django.conf import settings
from django.core.management import CommandError
from django.db import transaction
from django.tasks import TaskResultStatus
from django_tasks_db.models import DBTaskResult

from lexinform_web.operations.models import TaskRecovery
from lexinform_web.operations.tasks import worker_probe


@contextmanager
def worker_lock() -> Iterator[None]:
    # One stable inode fences worker and recovery on the single deployment host.
    with Path(settings.WORKER_LOCK_PATH).open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise CommandError("worker is still running; recovery/start refused") from exc
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def recover_probe(task_id: UUID, worker_id: str, reason: str) -> None:
    if not reason.strip():
        raise CommandError("a recovery reason is required")
    with worker_lock(), transaction.atomic():
        result = DBTaskResult.objects.select_for_update().get(pk=task_id)
        if result.task_path != worker_probe.module_path:
            raise CommandError("only the idempotent worker probe can be recovered here")
        if result.status not in {TaskResultStatus.RUNNING, TaskResultStatus.FAILED}:
            raise CommandError("task is neither running nor failed")
        if not result.worker_ids or result.worker_ids[-1] != worker_id:
            raise CommandError("worker id does not match the last claim")
        if TaskRecovery.objects.filter(task_id=task_id).count() >= 2:
            raise CommandError("probe retry limit reached")
        TaskRecovery.objects.create(
            task_id=task_id,
            worker_id=worker_id,
            reason=reason,
            previous_status=result.status,
            previous_error=result.traceback,
        )
        result.status = TaskResultStatus.READY
        result.started_at = None
        result.finished_at = None
        result.exception_class_path = ""
        result.traceback = ""
        result.save(
            update_fields=[
                "status",
                "started_at",
                "finished_at",
                "exception_class_path",
                "traceback",
            ]
        )
