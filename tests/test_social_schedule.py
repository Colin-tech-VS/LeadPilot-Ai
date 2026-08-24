"""Artisan-peak Facebook slots (Europe/Paris)."""
from datetime import datetime, timedelta, timezone

from app.services.social_schedule import (
    PARIS,
    is_aligned,
    next_publish_at,
    prefers_pro_topic,
    slot_reason,
)


def _paris(year, month, day, hour, minute):
    return datetime(year, month, day, hour, minute, tzinfo=PARIS)


def test_weekday_lunch_is_the_24h_slot():
    now = _paris(2026, 8, 24, 10, 0)  # Monday
    got = next_publish_at(now, interval=24).astimezone(PARIS)
    assert (got.hour, got.minute) == (12, 15)
    assert got.date() == now.date()
    assert "déjeuner" in slot_reason(got)


def test_after_lunch_24h_waits_until_next_day():
    now = _paris(2026, 8, 24, 13, 0)
    got = next_publish_at(now, interval=24).astimezone(PARIS)
    assert got.date() > now.date()
    assert (got.hour, got.minute) == (12, 15)


def test_friday_evening_and_saturday_morning():
    friday_afternoon = _paris(2026, 8, 28, 19, 0)
    got = next_publish_at(friday_afternoon, interval=24).astimezone(PARIS)
    assert got.weekday() == 5
    assert (got.hour, got.minute) == (9, 30)

    saturday_noon = _paris(2026, 8, 29, 12, 0)
    got = next_publish_at(saturday_noon, interval=24).astimezone(PARIS)
    assert got.weekday() == 6
    assert (got.hour, got.minute) == (18, 30)


def test_never_schedules_in_the_dead_of_night():
    now = _paris(2026, 8, 24, 2, 15)
    for interval in (6, 12, 24):
        got = next_publish_at(now, interval=interval).astimezone(PARIS)
        assert 7 <= got.hour <= 20


def test_12h_uses_lunch_then_evening():
    morning = _paris(2026, 8, 24, 9, 0)
    lunch = next_publish_at(morning, interval=12).astimezone(PARIS)
    assert (lunch.hour, lunch.minute) == (12, 15)
    evening = next_publish_at(lunch + timedelta(minutes=5), interval=12, last_published=lunch).astimezone(PARIS)
    assert (evening.hour, evening.minute) == (18, 45)


def test_6h_hits_end_of_chantier():
    after_lunch = _paris(2026, 8, 24, 12, 20)
    got = next_publish_at(after_lunch, interval=6).astimezone(PARIS)
    assert (got.hour, got.minute) == (17, 45)


def test_evening_prefers_pro_signup_cta():
    assert prefers_pro_topic(_paris(2026, 8, 24, 18, 45)) is True
    assert prefers_pro_topic(_paris(2026, 8, 29, 9, 30)) is True  # Saturday
    assert prefers_pro_topic(_paris(2026, 8, 24, 12, 15)) is False


def test_aligned_detects_peak_minutes():
    slot = _paris(2026, 8, 24, 12, 15).astimezone(timezone.utc)
    assert is_aligned(slot, 24) is True
    night = _paris(2026, 8, 24, 3, 0).astimezone(timezone.utc)
    assert is_aligned(night, 24) is False
