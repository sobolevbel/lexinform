import time
from urllib.parse import urlsplit

import pytest
from allauth.account.models import EmailAddress
from allauth.mfa.models import Authenticator
from allauth.mfa.recovery_codes.internal.auth import RecoveryCodes
from allauth.mfa.totp.internal.auth import TOTP, format_hotp_value, generate_totp_secret, hotp_value
from django.contrib.auth.models import Permission
from django.core import mail
from django.core.cache import cache
from django.test import Client
from django.urls import reverse
from wagtail.models import Page

from lexinform_web.accounts.models import User
from lexinform_web.editorial.models import GuidePage

pytestmark = pytest.mark.django_db
PASSWORD = "editor-test-password-381"
ADMIN_PATHS = (
    "/admin/",
    "/admin/pages/1/edit/preview/",
    "/admin/pages/1/view_draft/",
    "/admin/choose-page/",
    "/admin/images/chooser/",
    "/admin/images/add/",
    "/admin/documents/add/",
    "/admin/snippets/editorial/topic/",
)


@pytest.fixture
def editor() -> User:
    cache.clear()
    user = User.objects.create_user(
        username="editor", email="editor@example.com", password=PASSWORD, is_staff=True
    )
    user.user_permissions.add(Permission.objects.get(codename="access_admin"))
    EmailAddress.objects.create(user=user, email=user.email, verified=True, primary=True)
    return user


def enable_totp(user: User) -> str:
    secret = generate_totp_secret()
    TOTP.activate(user, secret)
    return format_hotp_value(hotp_value(secret, int(time.time()) // 30))


def login(client: Client, user: User, password: str = PASSWORD) -> None:
    response = client.post(reverse("account_login"), {"login": user.email, "password": password})
    assert response.status_code == 302


@pytest.mark.parametrize("path", ADMIN_PATHS)
@pytest.mark.parametrize("superuser", [False, True])
def test_password_only_session_cannot_access_any_admin_path(
    client: Client, editor: User, path: str, superuser: bool
) -> None:
    editor.is_superuser = superuser
    editor.save()
    client.force_login(editor)

    response = client.get(path)

    assert response.status_code == 302
    assert urlsplit(response["Location"]).path == reverse("mfa_activate_totp")
    assert "no-store" in response["Cache-Control"]


@pytest.mark.parametrize("path", ADMIN_PATHS)
def test_existing_totp_requires_session_proof(client: Client, editor: User, path: str) -> None:
    enable_totp(editor)
    client.force_login(editor)

    response = client.post(path)

    assert response.status_code == 302
    assert urlsplit(response["Location"]).path == reverse("mfa_reauthenticate")


def test_password_then_totp_and_editor_permissions(client: Client, editor: User) -> None:
    code = enable_totp(editor)
    login(client, editor)
    assert "_auth_user_id" not in client.session
    assert client.post(reverse("mfa_authenticate"), {"code": "invalid"}).status_code == 200
    assert "_auth_user_id" not in client.session

    response = client.post(reverse("mfa_authenticate"), {"code": code})

    assert response.status_code == 302
    assert client.get("/admin/").status_code == 200
    assert client.get("/admin/users/").status_code == 302
    assert client.get("/admin/images/add/").status_code == 302


def test_recovery_code_is_single_use(client: Client, editor: User) -> None:
    enable_totp(editor)
    code = RecoveryCodes.activate(editor).get_unused_codes()[0]
    login(client, editor)
    assert client.post(reverse("mfa_authenticate"), {"code": code}).status_code == 302
    assert client.get("/admin/").status_code == 200
    client.logout()
    login(client, editor)

    response = client.post(reverse("mfa_authenticate"), {"code": code})

    assert response.status_code == 200
    assert "_auth_user_id" not in client.session


def test_signup_closed_and_legacy_login_cannot_authenticate(client: Client, editor: User) -> None:
    assert client.get(reverse("account_signup")).status_code == 200
    response = client.post("/admin/login/", {"username": editor.username, "password": PASSWORD})
    assert urlsplit(response["Location"]).path == reverse("account_login")
    assert "_auth_user_id" not in client.session
    response = client.post(
        reverse("account_signup"),
        {"email": "reader@example.com", "password1": PASSWORD, "password2": PASSWORD},
    )
    assert not User.objects.filter(email="reader@example.com").exists()


def test_password_reset_preserves_mfa(client: Client, editor: User) -> None:
    enable_totp(editor)
    response = client.post(reverse("account_reset_password"), {"email": editor.email})
    assert response.status_code == 302
    assert len(mail.outbox) == 1
    link = next(line for line in mail.outbox[0].body.splitlines() if "/password/reset/key/" in line)
    response = client.get(urlsplit(link.strip()).path)
    reset_path = response["Location"]
    password = "replacement-test-password-984"

    response = client.post(reset_path, {"password1": password, "password2": password})

    assert response.status_code == 302
    assert "_auth_user_id" not in client.session
    assert Authenticator.objects.filter(user_id=editor.pk, type="totp").exists()
    login(client, editor, password)
    assert "_auth_user_id" not in client.session


def test_mfa_expires_and_removed_device_invalidates_proof(client: Client, editor: User) -> None:
    code = enable_totp(editor)
    login(client, editor)
    client.post(reverse("mfa_authenticate"), {"code": code})
    session = client.session
    records = session["account_authentication_methods"]
    for record in records:
        record["at"] -= 13 * 60 * 60
    session["account_authentication_methods"] = records
    session.save()
    response = client.get("/admin/")
    assert urlsplit(response["Location"]).path == reverse("mfa_reauthenticate")
    Authenticator.objects.filter(user_id=editor.pk).delete()
    response = client.get("/admin/")
    assert urlsplit(response["Location"]).path == reverse("mfa_activate_totp")


def test_nonstaff_is_denied_even_with_mfa(client: Client, editor: User) -> None:
    code = enable_totp(editor)
    login(client, editor)
    client.post(reverse("mfa_authenticate"), {"code": code})
    editor.is_staff = False
    editor.save()
    assert client.get("/admin/").status_code == 403


def test_totp_cannot_be_disabled(client: Client, editor: User) -> None:
    code = enable_totp(editor)
    login(client, editor)
    client.post(reverse("mfa_authenticate"), {"code": code})

    response = client.post(reverse("mfa_deactivate_totp"))

    assert response.status_code == 200
    assert Authenticator.objects.filter(user_id=editor.pk, type="totp").exists()


def test_first_login_enrolls_totp_and_generates_recovery_codes(
    client: Client, editor: User
) -> None:
    login(client, editor)
    assert client.get(reverse("mfa_activate_totp")).status_code == 200
    secret = client.session["mfa.totp.secret"]
    code = format_hotp_value(hotp_value(secret, int(time.time()) // 30))

    response = client.post(reverse("mfa_activate_totp"), {"code": code})

    assert response.status_code == 302
    assert Authenticator.objects.filter(user_id=editor.pk, type="totp").exists()
    assert Authenticator.objects.filter(user_id=editor.pk, type="recovery_codes").exists()
    response = client.post(reverse("mfa_reauthenticate"), {"code": code, "next": "/admin/"})
    assert response.status_code == 302
    assert client.get("/admin/").status_code == 200


def test_superuser_can_preview_choose_and_upload_only_after_mfa(
    client: Client, editor: User
) -> None:
    editor.is_superuser = True
    editor.save()
    root = Page.get_first_root_node()
    assert root is not None
    page = GuidePage(title="Draft", slug="auth-draft", summary="Private preview")
    root.add_child(instance=page)
    page.save_revision()
    code = enable_totp(editor)
    login(client, editor)
    paths = [
        reverse("wagtailadmin_pages:view_draft", args=[page.pk]),
        reverse("wagtailadmin_choose_page"),
        reverse("wagtailimages:add"),
        reverse("wagtaildocs:add"),
    ]
    for path in paths:
        assert client.get(path).status_code == 302

    client.post(reverse("mfa_authenticate"), {"code": code})

    for path in paths:
        response = client.get(path)
        assert response.status_code == 200
        assert "no-store" in response["Cache-Control"]


def test_login_csrf_and_external_next_are_not_bypasses(editor: User) -> None:
    client = Client(enforce_csrf_checks=True)
    assert (
        client.post(
            reverse("account_login"), {"login": editor.email, "password": PASSWORD}
        ).status_code
        == 403
    )
    client = Client()
    code = enable_totp(editor)
    response = client.post(
        reverse("account_login"),
        {"login": editor.email, "password": PASSWORD, "next": "https://example.org/steal"},
    )
    assert response.status_code == 302
    response = client.post(reverse("mfa_authenticate"), {"code": code})
    assert response.status_code == 302
    assert not urlsplit(response["Location"]).netloc
