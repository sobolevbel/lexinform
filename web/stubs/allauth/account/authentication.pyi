from typing import NotRequired, TypedDict

from django.http import HttpRequest

class AuthenticationRecord(TypedDict):
    method: str
    at: float
    id: NotRequired[int]
    type: NotRequired[str]

def get_authentication_records(request: HttpRequest) -> list[AuthenticationRecord]: ...
