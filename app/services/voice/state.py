"""Twilio call state — Redis-ready in-memory store per CallSid."""

from app.services.voice.conversation_state import (
    ConversationState,
    ConversationStore,
    conversation_store,
)

__all__ = ["ConversationState", "ConversationStore", "conversation_store", "get_call_state"]


def get_call_state(call_sid: str, tenant_id: str, caller_phone: str) -> ConversationState:
    return conversation_store.get_or_create(call_sid, tenant_id, caller_phone)
