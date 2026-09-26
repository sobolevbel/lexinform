from allauth.account.adapter import DefaultAccountAdapter
from allauth.mfa.adapter import DefaultMFAAdapter
from allauth.mfa.models import Authenticator
from django.http import HttpRequest


class StaffAccountAdapter(DefaultAccountAdapter):
    def is_open_for_signup(self, request: HttpRequest) -> bool:
        return False


class StaffMFAAdapter(DefaultMFAAdapter):
    def can_delete_authenticator(self, authenticator: Authenticator) -> bool:
        return False
