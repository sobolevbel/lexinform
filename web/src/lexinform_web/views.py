from datetime import date

from django.conf import settings
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_safe

from lexinform_web.gallery import DEMO_DAY, FixedClock, gallery_samples


@require_safe
def home(request: HttpRequest) -> HttpResponse:
    return render(request, "home.html")


@require_safe
def language_home(request: HttpRequest) -> HttpResponse:
    return redirect("home")


@require_safe
def components_gallery(request: HttpRequest) -> HttpResponse:
    if not settings.WEBSITE_DEMO_ENABLED:
        raise Http404
    day, error = DEMO_DAY, ""
    if requested := request.GET.get("on"):
        try:
            day = date.fromisoformat(requested)
        except ValueError:
            error = "Введите дату в формате ГГГГ-ММ-ДД."
    context = {"day": day, "day_error": error, "samples": gallery_samples(FixedClock(day))}
    return render(request, "components_gallery.html", context, status=400 if error else 200)
