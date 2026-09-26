import uuid

from django.db import models


class WorkerProbe(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    released = models.BooleanField(default=True)
    fail_once = models.BooleanField(default=False)
    attempts = models.PositiveIntegerField(default=0)
    completed = models.BooleanField(default=False)


class TaskRecovery(models.Model):
    task_id = models.UUIDField()
    worker_id = models.CharField(max_length=64)
    reason = models.TextField()
    previous_status = models.CharField(max_length=16)
    previous_error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
