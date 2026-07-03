import threading
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


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
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class ConversationStore:
    """In-memory conversation store (Redis-ready interface)."""

    def __init__(self):
        self._sessions: dict[str, ConversationState] = {}
        self._lock = threading.Lock()

    def get(self, call_id: str) -> ConversationState | None:
        with self._lock:
            session = self._sessions.get(call_id)
            return deepcopy(session) if session else None

    def get_or_create(
        self, call_id: str, tenant_id: str, caller_phone: str
    ) -> ConversationState:
        with self._lock:
            if call_id not in self._sessions:
                self._sessions[call_id] = ConversationState(
                    call_id=call_id,
                    tenant_id=tenant_id,
                    caller_phone=caller_phone,
                )
            return self._sessions[call_id]

    def save(self, state: ConversationState):
        with self._lock:
            state.updated_at = datetime.now(timezone.utc).isoformat()
            self._sessions[state.call_id] = state

    def delete(self, call_id: str):
        with self._lock:
            self._sessions.pop(call_id, None)

    def dump_all(self) -> dict[str, Any]:
        with self._lock:
            return {k: v.to_dict() for k, v in self._sessions.items()}


conversation_store = ConversationStore()
