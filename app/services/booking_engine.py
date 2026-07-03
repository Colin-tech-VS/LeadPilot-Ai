import uuid
from datetime import datetime, timedelta, timezone

from app.core.extensions import db
from app.models.tenant import Tenant
from app.services.availability import find_next_available_slot
from app.services.service_zone import check_service_zone

HIGH_PRIORITY_ISSUES = {"leak", "burst_pipe", "no_water", "flooding", "pipe_issue"}

ACTION_BOOK_NOW = "BOOK_NOW"
ACTION_CALL_BACK = "CALL_BACK"
ACTION_SEND_QUOTE = "SEND_QUOTE"
ACTION_OUT_OF_ZONE = "OUT_OF_ZONE"


class BookingEngine:
    """Score leads and suggest booking actions to convert calls into jobs."""

    def process_lead(self, lead: dict, tenant) -> dict:
        if isinstance(tenant, str):
            tenant = db.session.get(Tenant, uuid.UUID(tenant))

        score, score_reasons = self._compute_priority_score(lead)
        zone = check_service_zone(lead, tenant) if tenant else {"in_zone": True}

        if not zone.get("in_zone"):
            status = zone.get("status")
            if status == "address_unverified":
                action = ACTION_CALL_BACK
                reason = (
                    f"Priority score {score}/100. Address could not be verified — "
                    "callback required before booking."
                )
            else:
                action = ACTION_OUT_OF_ZONE
                reason = zone.get("reason", "Outside service area.")

            return {
                "priority_score": score,
                "action": action,
                "suggested_slot": None,
                "reason": reason,
                "out_of_zone": action == ACTION_OUT_OF_ZONE,
                "zone_status": status,
                "distance_km": zone.get("distance_km"),
                "service_radius_km": zone.get("service_radius_km"),
                "service_area_label": zone.get("service_area_label"),
            }

        action = self._determine_action(score)
        suggested_slot = None
        slot_unavailable = False

        if action == ACTION_BOOK_NOW and tenant:
            suggested_slot = find_next_available_slot(tenant.id)
            if not suggested_slot:
                action = ACTION_CALL_BACK
                slot_unavailable = True

        reason = self._build_reason(
            action, score, score_reasons, suggested_slot, slot_unavailable
        )

        return {
            "priority_score": score,
            "action": action,
            "suggested_slot": suggested_slot.isoformat() if suggested_slot else None,
            "reason": reason,
            "out_of_zone": False,
            "slot_unavailable": slot_unavailable,
            "zone_status": zone.get("status"),
            "distance_km": zone.get("distance_km"),
            "service_radius_km": zone.get("service_radius_km"),
            "service_area_label": zone.get("service_area_label"),
        }

    def _compute_priority_score(self, lead: dict) -> tuple[int, list[str]]:
        score = 0
        reasons = []

        urgency = (lead.get("urgency_level") or "").lower()
        if urgency == "high":
            score += 50
            reasons.append("high urgency")

        issue_type = (lead.get("issue_type") or "").lower()
        if issue_type in HIGH_PRIORITY_ISSUES:
            score += 30
            reasons.append(f"critical issue ({issue_type})")

        if lead.get("address"):
            score += 20
            reasons.append("address provided")

        if lead.get("phone"):
            score += 10
            reasons.append("phone provided")

        return min(score, 100), reasons

    def _determine_action(self, score: int) -> str:
        if score >= 70:
            return ACTION_BOOK_NOW
        if score >= 40:
            return ACTION_CALL_BACK
        return ACTION_SEND_QUOTE

    def _build_reason(
        self,
        action: str,
        score: int,
        score_reasons: list[str],
        suggested_slot: datetime | None,
        slot_unavailable: bool = False,
    ) -> str:
        factors = ", ".join(score_reasons) if score_reasons else "minimal data"
        if action == ACTION_BOOK_NOW:
            slot_text = suggested_slot.strftime("%Y-%m-%d %H:%M %Z") if suggested_slot else "soon"
            return (
                f"Priority score {score}/100 ({factors}). "
                f"Available slot confirmed — {slot_text}."
            )
        if action == ACTION_CALL_BACK and slot_unavailable:
            return (
                f"Priority score {score}/100 ({factors}). "
                "No available slots in the next 14 days — callback required."
            )
        if action == ACTION_CALL_BACK:
            return (
                f"Priority score {score}/100 ({factors}). "
                "Schedule a callback within 24 hours."
            )
        return (
            f"Priority score {score}/100 ({factors}). "
            "Send a quote before booking."
        )
