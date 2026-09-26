from allauth.mfa.models import Authenticator

class DefaultMFAAdapter:
    def can_delete_authenticator(self, authenticator: Authenticator) -> bool: ...
