from typing import ClassVar

from django.contrib.auth.models import AbstractUser, UserManager
from django.db import models
from django.db.models.functions import Lower

from lexinform_web.languages import SUPPORTED_LANGUAGES


class NormalizedEmailUserManager(UserManager["User"]):
    @classmethod
    def normalize_email(cls, email: str | None) -> str:
        return super().normalize_email(email).casefold()


class User(AbstractUser):
    email = models.EmailField()
    interface_language = models.CharField(
        max_length=2,
        choices=SUPPORTED_LANGUAGES,
        default="pl",
    )
    public_name = models.CharField(max_length=80, blank=True)

    objects: ClassVar[NormalizedEmailUserManager] = NormalizedEmailUserManager()

    class Meta:
        verbose_name = "user"
        verbose_name_plural = "users"
        constraints = [
            models.UniqueConstraint(Lower("email"), name="accounts_user_email_ci_unique")
        ]

    def clean(self) -> None:
        super().clean()
        self.email = self.__class__.objects.normalize_email(self.email)
