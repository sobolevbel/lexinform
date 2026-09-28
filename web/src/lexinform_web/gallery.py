from dataclasses import dataclass
from datetime import date, datetime, time

from lexinform.ports import Clock
from lexinform_web.participation import (
    WARSAW,
    ParticipationDisplay,
    ParticipationWindow,
    participation_display,
)

DEMO_DAY = date(2026, 9, 21)


class FixedClock:
    """The gallery's clock: noon in Warsaw on the chosen day, so every render of a day agrees."""

    def __init__(self, day: date) -> None:
        self.day = day

    def now(self) -> datetime:
        return datetime.combine(self.day, time(12), tzinfo=WARSAW)


@dataclass(frozen=True)
class GallerySample:
    label: str
    display: ParticipationDisplay


SAMPLE_WINDOWS: tuple[tuple[str, ParticipationWindow], ...] = (
    (
        "Синтетика: консультация Сейма",
        ParticipationWindow(start=date(2026, 9, 1), end=date(2026, 9, 21), confirmed=True),
    ),
    (
        "Синтетика: письмо RCL",
        ParticipationWindow(start=date(2026, 9, 24), end=date(2026, 10, 8), confirmed=True),
    ),
    (
        "Синтетика: завершённая консультация",
        ParticipationWindow(start=date(2026, 8, 20), end=date(2026, 9, 10), confirmed=True),
    ),
    (
        "Синтетика: устаревшая запись",
        ParticipationWindow(
            start=date(2026, 9, 1),
            end=date(2026, 10, 1),
            confirmed=False,
            basis="Последняя успешная проверка источника была давно.",
        ),
    ),
    (
        "Синтетика: план без текста",
        ParticipationWindow(
            start=None,
            end=None,
            confirmed=False,
            basis="Источник не называет срока консультаций.",
        ),
    ),
)


def gallery_samples(clock: Clock) -> tuple[GallerySample, ...]:
    now = clock.now()
    return tuple(
        GallerySample(label=label, display=participation_display(window, now))
        for label, window in SAMPLE_WINDOWS
    )
