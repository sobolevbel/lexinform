from django.http import HttpRequest

class DefaultAccountAdapter:
    def is_open_for_signup(self, request: HttpRequest) -> bool: ...
