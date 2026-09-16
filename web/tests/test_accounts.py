import pytest
from django.db import IntegrityError

from lexinform_web.accounts.models import User

pytestmark = pytest.mark.django_db


def test_user_email_is_normalized() -> None:
    user = User.objects.create_user(username="editor", email="Editor@Example.COM", password="x")

    assert user.email == "editor@example.com"


def test_user_email_is_unique_case_insensitively() -> None:
    User.objects.create_user(username="first", email="editor@example.com", password="x")

    with pytest.raises(IntegrityError):
        User.objects.create_user(username="second", email="EDITOR@example.com", password="x")
