import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from django.core.management import CommandError, call_command
from django.db import connection, transaction
from django.tasks import TaskResultStatus
from django.test import override_settings
from django.utils import timezone
from django_tasks_db.models import DBTaskResult

from lexinform_web.operations.models import TaskRecovery, WorkerProbe
from lexinform_web.operations.tasks import worker_probe
from lexinform_web.operations.worker import recover_probe

pytestmark = pytest.mark.django_db(transaction=True)
MANAGE = Path(__file__).resolve().parents[1] / "manage.py"


@pytest.fixture
def worker_env(tmp_path: Path) -> Iterator[dict[str, str]]:
    lock = str(tmp_path / "worker.lock")
    env = {
        **os.environ,
        "DJANGO_SETTINGS_MODULE": "lexinform_web.config.test",
        "LEXINFORM_WEB_DB_NAME": str(connection.settings_dict["NAME"]),
        "LEXINFORM_WEB_DB_USER": str(connection.settings_dict["USER"]),
        "LEXINFORM_WEB_DB_PASSWORD": str(connection.settings_dict["PASSWORD"]),
        "LEXINFORM_WEB_DB_HOST": str(connection.settings_dict["HOST"]),
        "LEXINFORM_WEB_DB_PORT": str(connection.settings_dict["PORT"]),
        "LEXINFORM_WEB_WORKER_LOCK_PATH": lock,
    }
    with override_settings(WORKER_LOCK_PATH=lock):
        yield env


@contextmanager
def worker(env: dict[str, str]) -> Iterator[subprocess.Popen[bytes]]:
    process = subprocess.Popen(
        [sys.executable, str(MANAGE), "web_worker", "--batch"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    try:
        yield process
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=10)


def wait_for(condition: Callable[[], bool]) -> None:
    deadline = time.monotonic() + 10
    while not condition():
        if time.monotonic() > deadline:
            pytest.fail("worker did not reach the expected state within 10 seconds")
        time.sleep(0.05)


def finish(process: subprocess.Popen[bytes]) -> None:
    output, _ = process.communicate(timeout=10)
    assert process.returncode == 0, output.decode()


def result_for(task_id: str) -> DBTaskResult[Any, Any]:
    return DBTaskResult.objects.get(pk=task_id)


def test_enqueue_is_transactional_and_duplicate_probe_is_idempotent(
    worker_env: dict[str, str],
) -> None:
    with pytest.raises(RuntimeError), transaction.atomic():
        probe = WorkerProbe.objects.create()
        worker_probe.enqueue(str(probe.pk))
        raise RuntimeError("rollback")
    assert not DBTaskResult.objects.exists()
    assert not WorkerProbe.objects.exists()
    with transaction.atomic():
        probe = WorkerProbe.objects.create()
        result = worker_probe.enqueue(str(probe.pk))
        with worker(worker_env) as process:
            finish(process)
        assert result_for(result.id).status == TaskResultStatus.READY
    worker_probe.enqueue(str(probe.pk))
    with worker(worker_env) as process:
        finish(process)
    probe.refresh_from_db()
    assert probe.completed
    assert probe.attempts == 1
    assert result_for(result.id).status == TaskResultStatus.SUCCESSFUL


def test_sigterm_finishes_claimed_task_before_shutdown(worker_env: dict[str, str]) -> None:
    probe = WorkerProbe.objects.create(released=False)
    result = worker_probe.enqueue(str(probe.pk))
    with worker(worker_env) as process:
        wait_for(lambda: WorkerProbe.objects.get(pk=probe.pk).attempts == 1)
        process.send_signal(signal.SIGTERM)
        WorkerProbe.objects.filter(pk=probe.pk).update(released=True)
        finish(process)
    assert result_for(result.id).status == TaskResultStatus.SUCCESSFUL
    assert WorkerProbe.objects.get(pk=probe.pk).completed


def test_sigkill_requires_explicit_recovery_and_never_steals_live_work(
    worker_env: dict[str, str],
) -> None:
    probe = WorkerProbe.objects.create(released=False)
    result = worker_probe.enqueue(str(probe.pk))
    with worker(worker_env) as process:
        wait_for(lambda: WorkerProbe.objects.get(pk=probe.pk).attempts == 1)
        claimed = result_for(result.id)
        worker_id = claimed.worker_ids[-1]
        DBTaskResult.objects.filter(pk=result.id).update(
            started_at=timezone.now() - timedelta(days=1)
        )
        with pytest.raises(CommandError, match="still running"):
            recover_probe(UUID(result.id), worker_id, "stale timestamp does not prove death")
        with worker(worker_env) as second:
            output, _ = second.communicate(timeout=10)
            assert second.returncode != 0
            assert b"still running" in output
        process.kill()
        process.communicate(timeout=10)
    assert result_for(result.id).status == TaskResultStatus.RUNNING
    with worker(worker_env) as restarted:
        finish(restarted)
    assert result_for(result.id).status == TaskResultStatus.RUNNING
    with pytest.raises(CommandError, match="does not match"):
        recover_probe(UUID(result.id), "wrong-worker", "wrong claim")
    call_command("recover_worker_probe", result.id, worker_id=worker_id, reason="SIGKILL confirmed")
    WorkerProbe.objects.filter(pk=probe.pk).update(released=True)
    with worker(worker_env) as restarted:
        finish(restarted)
    assert result_for(result.id).status == TaskResultStatus.SUCCESSFUL
    assert WorkerProbe.objects.get(pk=probe.pk).attempts == 2
    assert TaskRecovery.objects.get(task_id=result.id).previous_status == TaskResultStatus.RUNNING


def test_failed_probe_retry_and_retention_preserve_unfinished_work(
    worker_env: dict[str, str],
) -> None:
    probe = WorkerProbe.objects.create(fail_once=True)
    result = worker_probe.enqueue(str(probe.pk))
    with worker(worker_env) as process:
        finish(process)
    failed = result_for(result.id)
    assert failed.status == TaskResultStatus.FAILED
    recover_probe(UUID(result.id), failed.worker_ids[-1], "retry transient probe failure")
    with worker(worker_env) as process:
        finish(process)
    assert result_for(result.id).status == TaskResultStatus.SUCCESSFUL
    assert (
        "requested transient probe failure"
        in TaskRecovery.objects.get(task_id=result.id).previous_error
    )
    old = timezone.now() - timedelta(days=30)
    DBTaskResult.objects.filter(pk=result.id).update(finished_at=old)
    queued = worker_probe.enqueue(str(probe.pk))
    running = worker_probe.enqueue(str(probe.pk))
    DBTaskResult.objects.filter(pk=running.id).update(
        status=TaskResultStatus.RUNNING, started_at=old
    )

    call_command("prune_db_task_results", min_age_days=14)

    assert not DBTaskResult.objects.filter(pk=result.id).exists()
    assert DBTaskResult.objects.filter(pk__in=[queued.id, running.id]).count() == 2
    assert TaskRecovery.objects.filter(task_id=result.id).exists()


def test_recovery_refuses_unknown_work_and_excess_retries(worker_env: dict[str, str]) -> None:
    probe = WorkerProbe.objects.create()
    result = worker_probe.enqueue(str(probe.pk))
    DBTaskResult.objects.filter(pk=result.id).update(
        status=TaskResultStatus.FAILED, worker_ids=["dead"]
    )
    with pytest.raises(CommandError, match="reason"):
        recover_probe(UUID(result.id), "dead", " ")
    for _ in range(2):
        recover_probe(UUID(result.id), "dead", "test retry")
        DBTaskResult.objects.filter(pk=result.id).update(status=TaskResultStatus.FAILED)
    with pytest.raises(CommandError, match="limit"):
        recover_probe(UUID(result.id), "dead", "fourth attempt")
    DBTaskResult.objects.filter(pk=result.id).update(task_path="unknown.paid_task")
    with pytest.raises(CommandError, match="only the idempotent"):
        recover_probe(UUID(result.id), "dead", "must not repeat uncertain work")
