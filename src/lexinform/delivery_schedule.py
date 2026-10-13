import datetime as dt
from zoneinfo import ZoneInfo

from lexinform.models import AttentionAssessment, DeliverySchedule, PublicationKind

LOCAL_TZ = ZoneInfo("Europe/Warsaw")
MAX_EDITORIAL_WAIT = dt.timedelta(hours=24)


def daily_schedule(
    assessment: AttentionAssessment | None,
    kind: PublicationKind,
    *,
    now: dt.datetime,
    release_hour: int,
    urgent_days: int,
) -> DeliverySchedule | None:
    if assessment is None or assessment.protected:
        return None
    if kind is PublicationKind.STATUS_UPDATE:
        route = "daily_updates"
    elif kind in (PublicationKind.AGENDA, PublicationKind.AGENDA_CANCELLED):
        route = "daily_sittings"
        if sitting_is_close(assessment, now=now, urgent_days=urgent_days):
            return None
    else:
        return None
    local = now.astimezone(LOCAL_TZ)
    release = dt.datetime.combine(local.date(), dt.time(release_hour), tzinfo=LOCAL_TZ)
    if release <= local:
        release = dt.datetime.combine(
            local.date() + dt.timedelta(days=1), dt.time(release_hour), tzinfo=LOCAL_TZ
        )
    # The elapsed-time cap stays 24 hours across Warsaw's daylight-saving transitions.
    limit = assessment.detected_at.astimezone(dt.UTC) + MAX_EDITORIAL_WAIT
    return DeliverySchedule(
        route=route,
        detected_at=assessment.detected_at,
        not_before=min(release.astimezone(dt.UTC), limit),
        deliver_by=limit,
    )


def sitting_is_close(
    assessment: AttentionAssessment, *, now: dt.datetime, urgent_days: int
) -> bool:
    return assessment.deadline is not None and assessment.deadline <= now.astimezone(
        LOCAL_TZ
    ).date() + dt.timedelta(days=urgent_days)
