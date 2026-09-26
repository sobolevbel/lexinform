from typing import Any, ClassVar

from django.db import models

class Authenticator(models.Model):
    objects: ClassVar[models.Manager[Authenticator]]
    type: models.CharField[str, str]
    data: models.JSONField[dict[str, Any], dict[str, Any]]
