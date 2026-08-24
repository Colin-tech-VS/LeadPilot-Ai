"""When to auto-publish Facebook posts for PilotCore.

The page is aimed at *artisans* (signup on /pro), not late-night scrollers.
Slots are Europe/Paris local time, aligned with chantier rhythm:

- 07:30  café / trajet — coup d'œil avant le premier client
- 12:15  pause déjeuner — pic Facebook, 10 min pour ouvrir un lien
- 17:45  fin de chantier, téléphone dans le camion
- 18:45–20:15  rentrée / canapé — meilleur créneau pour *s'inscrire*
- samedi 09:15  matinée admin (devis, paperasse, outils)
- dimanche 18:30  préparation de la semaine

Nothing goes out between 21:00 and 07:00.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

PARIS = ZoneInfo("Europe/Paris")

# weekday 0=Mon … 6=Sun → list of (hour, minute)
_SLOTS_6: dict[int, list[tuple[int, int]]] = {
    **{d: [(7, 30), (12, 15), (17, 45), (20, 15)] for d in range(5)},
    5: [(9, 15), (12, 30), (18, 0)],
    6: [(18, 30)],
}
_SLOTS_12: dict[int, list[tuple[int, int]]] = {
    **{d: [(12, 15), (18, 45)] for d in range(5)},
    5: [(9, 30), (18, 0)],
    6: [(18, 30)],
}
_SLOTS_24: dict[int, list[tuple[int, int]]] = {
    0: [(12, 15)],
    1: [(12, 15)],
    2: [(12, 15)],
    3: [(12, 15)],
    4: [(18, 30)],  # vendredi soir : on ouvre l'ordi, on s'inscrit
    5: [(9, 30)],
    6: [(18, 30)],
}


def slots_for_interval(hours: int) -> dict[int, list[tuple[int, int]]]:
    if hours <= 6:
        return _SLOTS_6
    if hours <= 12:
        return _SLOTS_12
    return _SLOTS_24


def _aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def next_publish_at(
    now: datetime | None = None,
    *,
    interval: int = 24,
    last_published: datetime | None = None,
) -> datetime:
    """Return the next artisan-peak slot as UTC datetime."""
    now = _aware(now or datetime.now(timezone.utc))
    earliest = now + timedelta(minutes=8)
    if last_published is not None:
        last_published = _aware(last_published)
        # Keep a floor so two ticks cannot share the same window.
        earliest = max(earliest, last_published + timedelta(hours=2, minutes=30))

    slots = slots_for_interval(interval)
    local_now = earliest.astimezone(PARIS)
    start_day = local_now.date()

    for day_offset in range(0, 16):
        day = start_day + timedelta(days=day_offset)
        for hour, minute in slots.get(day.weekday(), ()):
            candidate = datetime(day.year, day.month, day.day, hour, minute, tzinfo=PARIS)
            if candidate <= local_now:
                continue
            return candidate.astimezone(timezone.utc)

    # Fallback should never hit; keep a sane daytime Paris slot.
    fallback = datetime(start_day.year, start_day.month, start_day.day, 12, 15, tzinfo=PARIS)
    return (fallback + timedelta(days=1)).astimezone(timezone.utc)


def is_aligned(dt: datetime, interval: int = 24) -> bool:
    local = _aware(dt).astimezone(PARIS)
    return (local.hour, local.minute) in slots_for_interval(interval).get(local.weekday(), ())


def slot_reason(dt: datetime) -> str:
    local = _aware(dt).astimezone(PARIS)
    hour = local.hour
    weekday = local.weekday()
    if weekday == 5 and hour < 13:
        return "samedi matin · moment admin (devis, outils, inscription)"
    if weekday == 6:
        return "dimanche soir · préparation de la semaine"
    if weekday == 4 and hour >= 17:
        return "vendredi soir · on s'inscrit avant le week-end"
    if 11 <= hour <= 14:
        return "pause déjeuner · pic Facebook artisans"
    if 17 <= hour <= 19:
        return "fin de chantier · téléphone dans le camion"
    if hour >= 20:
        return "soirée · scroll Facebook, temps d'ouvrir /pro"
    if hour < 9:
        return "avant le chantier · café / trajet"
    return "créneau artisans"


def prefers_pro_topic(dt: datetime) -> bool:
    """Evening and weekend slots convert better on the Pro trial CTA."""
    local = _aware(dt).astimezone(PARIS)
    return local.weekday() >= 5 or local.hour >= 17 or local.hour < 9
