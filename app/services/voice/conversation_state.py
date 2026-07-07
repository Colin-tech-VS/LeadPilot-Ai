import json
import logging
import re
import threading
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from flask import current_app, has_app_context

logger = logging.getLogger(__name__)


@dataclass
class ConversationState:
    call_id: str
    tenant_id: str
    caller_phone: str
    transcripts: list[dict] = field(default_factory=list)
    extracted_lead_data: dict = field(default_factory=dict)
    urgency_score: int = 0
    booking_status: str = "pending"
    booking_action: str | None = None
    booking_result: dict | None = None
    lead_id: str | None = None
    appointment_id: str | None = None
    last_ai_response: str | None = None
    last_intent: str | None = None
    turn_count: int = 0
    failure_count: int = 0
    failsafe_mode: bool = False
    urgency_ack_done: bool = False
    asked_slots: list = field(default_factory=list)
    account_flow: dict = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def append_transcript(self, role: str, text: str):
        self.transcripts.append({
            "role": role,
            "text": text,
            "at": datetime.now(timezone.utc).isoformat(),
        })
        self.turn_count += 1
        self.updated_at = datetime.now(timezone.utc).isoformat()

    def full_transcript(self) -> str:
        lines = []
        for entry in self.transcripts:
            prefix = "Client" if entry["role"] == "user" else "Réceptionniste"
            lines.append(f"{prefix}: {entry['text']}")
        return "\n".join(lines)

    def user_transcript(self) -> str:
        """Only what the caller said — used for lead extraction so the
        receptionist's own questions never pollute the parsed data."""
        return "\n".join(
            entry["text"] for entry in self.transcripts if entry["role"] == "user"
        )

    def to_dict(self) -> dict:
        return {
            "call_id": self.call_id,
            "tenant_id": self.tenant_id,
            "caller_phone": self.caller_phone,
            "transcripts": self.transcripts,
            "extracted_lead_data": self.extracted_lead_data,
            "urgency_score": self.urgency_score,
            "booking_status": self.booking_status,
            "booking_action": self.booking_action,
            "booking_result": self.booking_result,
            "lead_id": self.lead_id,
            "appointment_id": self.appointment_id,
            "last_ai_response": self.last_ai_response,
            "last_intent": self.last_intent,
            "turn_count": self.turn_count,
            "failure_count": self.failure_count,
            "failsafe_mode": self.failsafe_mode,
            "urgency_ack_done": self.urgency_ack_done,
            "asked_slots": self.asked_slots,
            "account_flow": self.account_flow,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ConversationState":
        return cls(
            call_id=data["call_id"],
            tenant_id=data["tenant_id"],
            caller_phone=data.get("caller_phone") or "",
            transcripts=data.get("transcripts") or [],
            extracted_lead_data=data.get("extracted_lead_data") or {},
            urgency_score=int(data.get("urgency_score") or 0),
            booking_status=data.get("booking_status") or "pending",
            booking_action=data.get("booking_action"),
            booking_result=data.get("booking_result"),
            lead_id=data.get("lead_id"),
            appointment_id=data.get("appointment_id"),
            last_ai_response=data.get("last_ai_response"),
            last_intent=data.get("last_intent"),
            turn_count=int(data.get("turn_count") or 0),
            failure_count=int(data.get("failure_count") or 0),
            failsafe_mode=bool(data.get("failsafe_mode")),
            urgency_ack_done=bool(data.get("urgency_ack_done")),
            asked_slots=list(data.get("asked_slots") or []),
            account_flow=dict(data.get("account_flow") or {}),
            created_at=data.get("created_at") or datetime.now(timezone.utc).isoformat(),
            updated_at=data.get("updated_at") or datetime.now(timezone.utc).isoformat(),
        )


def _use_db_store() -> bool:
    if not has_app_context():
        return False
    return current_app.config.get("ENV") == "production"


def _load_state_from_db(call_id: str) -> ConversationState | None:
    from sqlalchemy import text

    from app.core.extensions import db

    try:
        row = db.session.execute(
            text("SELECT state_json FROM voice_call_sessions WHERE call_id = :cid"),
            {"cid": call_id},
        ).first()
        if not row or not row[0]:
            return None
        return ConversationState.from_dict(json.loads(row[0]))
    except Exception:
        logger.exception("Failed to load voice session call_id=%s", call_id)
        return None


def _save_state_to_db(state: ConversationState) -> None:
    from sqlalchemy import text

    from app.core.extensions import db

    payload = json.dumps(state.to_dict(), ensure_ascii=False)
    now = datetime.now(timezone.utc)
    try:
        if db.engine.dialect.name == "postgresql":
            db.session.execute(
                text(
                    """
                    INSERT INTO voice_call_sessions
                        (call_id, tenant_id, caller_phone, state_json, updated_at)
                    VALUES (:cid, :tid, :phone, :state, :updated)
                    ON CONFLICT (call_id) DO UPDATE SET
                        state_json = EXCLUDED.state_json,
                        updated_at = EXCLUDED.updated_at
                    """
                ),
                {
                    "cid": state.call_id,
                    "tid": state.tenant_id,
                    "phone": state.caller_phone,
                    "state": payload,
                    "updated": now,
                },
            )
        else:
            existing = db.session.execute(
                text("SELECT 1 FROM voice_call_sessions WHERE call_id = :cid"),
                {"cid": state.call_id},
            ).first()
            if existing:
                db.session.execute(
                    text(
                        "UPDATE voice_call_sessions SET state_json = :state, updated_at = :updated "
                        "WHERE call_id = :cid"
                    ),
                    {"cid": state.call_id, "state": payload, "updated": now},
                )
            else:
                db.session.execute(
                    text(
                        "INSERT INTO voice_call_sessions "
                        "(call_id, tenant_id, caller_phone, state_json, updated_at) "
                        "VALUES (:cid, :tid, :phone, :state, :updated)"
                    ),
                    {
                        "cid": state.call_id,
                        "tid": state.tenant_id,
                        "phone": state.caller_phone,
                        "state": payload,
                        "updated": now,
                    },
                )
        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.exception("Failed to persist voice session call_id=%s", state.call_id)


class ConversationStore:
    """Conversation store — in-memory with PostgreSQL backing in production."""

    def __init__(self):
        self._sessions: dict[str, ConversationState] = {}
        self._lock = threading.Lock()

    def get(self, call_id: str) -> ConversationState | None:
        with self._lock:
            session = self._sessions.get(call_id)
            if session:
                return deepcopy(session)
        if _use_db_store():
            loaded = _load_state_from_db(call_id)
            return deepcopy(loaded) if loaded else None
        return None

    def get_or_create(
        self, call_id: str, tenant_id: str, caller_phone: str
    ) -> ConversationState:
        with self._lock:
            if call_id in self._sessions:
                return self._sessions[call_id]

        if _use_db_store():
            loaded = _load_state_from_db(call_id)
            if loaded:
                with self._lock:
                    self._sessions[call_id] = loaded
                    return loaded

        with self._lock:
            if call_id not in self._sessions:
                self._sessions[call_id] = ConversationState(
                    call_id=call_id,
                    tenant_id=tenant_id,
                    caller_phone=caller_phone,
                )
            return self._sessions[call_id]

    def save(self, state: ConversationState):
        state.updated_at = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self._sessions[state.call_id] = state
        if _use_db_store():
            _save_state_to_db(state)

    def delete(self, call_id: str):
        with self._lock:
            self._sessions.pop(call_id, None)
        if _use_db_store():
            from sqlalchemy import text

            from app.core.extensions import db

            try:
                db.session.execute(
                    text("DELETE FROM voice_call_sessions WHERE call_id = :cid"),
                    {"cid": call_id},
                )
                db.session.commit()
            except Exception:
                db.session.rollback()
                logger.exception("Failed to delete voice session call_id=%s", call_id)

    def dump_all(self) -> dict[str, Any]:
        with self._lock:
            return {k: v.to_dict() for k, v in self._sessions.items()}


conversation_store = ConversationStore()
