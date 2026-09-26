from typing import ClassVar

from django.db import models

class EmailAddress(models.Model):
    objects: ClassVar[models.Manager[EmailAddress]]
    email: models.EmailField[str, str]
    verified: models.BooleanField[bool, bool]
    primary: models.BooleanField[bool, bool]
