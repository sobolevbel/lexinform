import time

from django.db import transaction
from django.tasks import task

from lexinform_web.operations.models import WorkerProbe


@task
def worker_probe(probe_id: str, payload_version: int = 1) -> str:
    if payload_version != 1:
        raise ValueError("unsupported probe payload version")
    with transaction.atomic():
        probe = WorkerProbe.objects.select_for_update().get(pk=probe_id)
        if probe.completed:
            return probe_id
        probe.attempts += 1
        probe.save(update_fields=["attempts"])
    if probe.fail_once and probe.attempts == 1:
        raise RuntimeError("requested transient probe failure")
    deadline = time.monotonic() + 30
    while not probe.released:
        if time.monotonic() >= deadline:
            raise TimeoutError("probe was not released within 30 seconds")
        time.sleep(0.05)
        probe.refresh_from_db()
    WorkerProbe.objects.filter(pk=probe_id).update(completed=True)
    return probe_id
