from collections.abc import Callable

from allauth.account.authentication import get_authentication_records
from allauth.mfa.models import Authenticator
from django.conf import settings
from django.contrib.auth.views import redirect_to_login
from django.http import HttpRequest, HttpResponse, HttpResponseForbidden
from django.urls import reverse
from django.utils import timezone
from django.utils.cache import add_never_cache_headers


class StaffMFAMiddleware:
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        protected = request.path_info == "/admin" or request.path_info.startswith("/admin/")
        account = request.path_info.startswith("/accounts/")
        if protected:
            response = self.check_access(request)
            if response is None:
                response = self.get_response(request)
        else:
            response = self.get_response(request)
        if protected or account:
            add_never_cache_headers(response)
        return response

    def check_access(self, request: HttpRequest) -> HttpResponse | None:
        legacy_routes = {
            "/admin/login/": "account_login",
            "/admin/logout/": "account_logout",
        }
        if route := legacy_routes.get(request.path_info):
            return redirect_to_login("/admin/", reverse(route))
        if request.path_info.startswith("/admin/password_reset/"):
            return redirect_to_login("/admin/", reverse("account_reset_password"))
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path(), reverse("account_login"))
        if not request.user.is_active or not request.user.is_staff:
            return HttpResponseForbidden("Доступ разрешён только сотрудникам редакции.")
        authenticators = Authenticator.objects.filter(user_id=request.user.pk)
        if not authenticators.filter(type="totp").exists():
            return redirect_to_login(request.get_full_path(), reverse("mfa_activate_totp"))
        now = timezone.now().timestamp()
        for record in get_authentication_records(request):
            authenticator_id = record.get("id")
            if (
                record.get("method") == "mfa"
                and authenticator_id is not None
                and 0 <= now - record["at"] < settings.STAFF_MFA_MAX_AGE_SECONDS
                and authenticators.filter(pk=authenticator_id, type=record.get("type")).exists()
            ):
                return None
        return redirect_to_login(request.get_full_path(), reverse("mfa_reauthenticate"))
