from django.http import HttpRequest
from django.urls import reverse
from django.utils.translation import get_language, override

from lexinform_web.languages import SUPPORTED_LANGUAGES


def navigation(request: HttpRequest) -> dict[str, object]:
    language = get_language()
    links = []
    for code, label in SUPPORTED_LANGUAGES:
        with override(code):
            links.append({"code": code, "label": label, "url": reverse("home")})
    return {"language_homes": links, "section_language": language}
