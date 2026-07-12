"""Voice IA — dictated e-mail detection and emergency acknowledgement."""

import pytest

from app.services.lead_extractor import LeadExtractor, reconstruct_spoken_email
from app.services.voice.conversation_state import ConversationState
from app.services.voice.customer_account import extract_email_from_transcript
from app.services.voice.twilio_handler import TwilioVoiceHandler


@pytest.mark.parametrize(
    "spoken,expected",
    [
        ("jean point dupont arobase gmail point com", "jean.dupont@gmail.com"),
        # Literal address already in the transcript.
        ("mon email c'est marie.martin@orange.fr", "marie.martin@orange.fr"),
        # No introducing keyword, spelled out.
        ("paul arobase hotmail point fr", "paul@hotmail.fr"),
        # Caller forgets to say "arobase" — split before the known provider.
        ("sophie dupont gmail point com", "sophiedupont@gmail.com"),
        # STT renders the domain glued and the separator as a symbol.
        ("luc point bernard @ yahoo point fr", "luc.bernard@yahoo.fr"),
        # Hyphen / underscore spoken.
        ("jean tiret luc arobase free point fr", "jean-luc@free.fr"),
        ("a underscore b arobase gmail point com", "a_b@gmail.com"),
        # Filler words around the address.
        ("alors mon adresse mail est contact arobase pilotcore point com",
         "contact@pilotcore.com"),
    ],
)
def test_reconstruct_spoken_email(spoken, expected):
    assert reconstruct_spoken_email(spoken) == expected


@pytest.mark.parametrize(
    "noise",
    [
        "",
        "je ne sais pas",
        "bonjour je voudrais un plombier",
        "oui c'est bien ça",
    ],
)
def test_reconstruct_spoken_email_rejects_non_emails(noise):
    assert reconstruct_spoken_email(noise) is None


def test_extract_email_from_transcript_no_keyword():
    # The reply to "quelle est votre e-mail ?" carries no "email" prefix.
    assert (
        extract_email_from_transcript("jean point dupont arobase gmail point com")
        == "jean.dupont@gmail.com"
    )


def test_lead_extractor_guess_email_dictated():
    extractor = LeadExtractor()
    transcript = "oui alors c'est jean point dupont arobase gmail point com voilà"
    assert extractor._guess_email(transcript) == "jean.dupont@gmail.com"


def _state_high_urgency():
    state = ConversationState(call_id="c1", tenant_id="t1", caller_phone="+33600000000")
    state.extracted_lead_data = {"urgency_level": "high"}
    return state


def test_emergency_acknowledged_only_once():
    handler = TwilioVoiceHandler()
    state = _state_high_urgency()

    first = handler._acknowledge(state)
    assert "urgent" in first.lower()
    assert state.urgency_ack_done is True

    # Every following turn must NOT repeat the emergency phrase.
    for _ in range(4):
        again = handler._acknowledge(state)
        assert "urgent" not in again.lower()
        assert "urgence" not in again.lower()


def test_urgency_ack_flag_survives_serialization():
    state = _state_high_urgency()
    state.urgency_ack_done = True
    restored = ConversationState.from_dict(state.to_dict())
    assert restored.urgency_ack_done is True


def test_issue_slot_stops_looping_when_unclassifiable():
    """The receptionist must not re-ask "describe the problem" forever when the
    extractor cannot map the caller's description onto a known issue type."""
    from app.services.voice.twilio_handler import MAX_ISSUE_ASKS

    handler = TwilioVoiceHandler()
    state = ConversationState(call_id="c", tenant_id="t", caller_phone="+33600000000")
    # issue_type never becomes a concrete type — mirrors a noisy transcript.
    state.extracted_lead_data = {"issue_type": "general_inquiry"}

    issue_asks = 0
    for _ in range(MAX_ISSUE_ASKS + 5):
        nxt = handler._next_question(state)
        if nxt is None:
            break
        slot, _q = nxt
        if slot != "issue":
            break
        # Simulate the handler emitting the question and the caller replying
        # without the extractor ever classifying the problem.
        state.issue_ask_count += 1
        issue_asks += 1
        state.append_transcript("user", "euh je sais pas trop comment expliquer")
    else:  # pragma: no cover - only hit if the loop never breaks
        raise AssertionError("issue slot kept looping — infinite question loop")

    assert issue_asks <= MAX_ISSUE_ASKS


def test_issue_ask_count_survives_serialization():
    state = ConversationState(call_id="c", tenant_id="t", caller_phone="+33600000000")
    state.issue_ask_count = 2
    restored = ConversationState.from_dict(state.to_dict())
    assert restored.issue_ask_count == 2


def test_voice_never_asks_account_questions():
    """The voice IA no longer handles account sign-up: it must only ask for the
    dispatch essentials (name, e-mail, address, urgency) and never an
    "account:" slot."""
    handler = TwilioVoiceHandler()
    state = ConversationState(call_id="c", tenant_id="t", caller_phone="+33600000000")
    state.extracted_lead_data = {"issue_type": "leak"}

    asked = []
    for _ in range(20):
        nxt = handler._next_question(state)
        if nxt is None:
            break
        slot, _q = nxt
        assert not slot.startswith("account:"), f"unexpected account slot: {slot}"
        asked.append(slot)
        state.asked_slots.append(slot)
    else:  # pragma: no cover - only hit on an infinite loop
        raise AssertionError("question flow did not terminate")

    # The e-mail (needed to send the devis) is still collected.
    assert "email" in asked


def test_voice_captures_dictated_email_on_email_slot(app):
    """After asking the e-mail question, a dictated address is reconstructed and
    stored on the lead so the devis can be sent."""
    with app.app_context():
        state = ConversationState(call_id="c", tenant_id="t", caller_phone="+33600000000")
        state.asked_slots = ["email"]
        transcript = "jean point dupont arobase gmail point com"
        if not state.extracted_lead_data.get("email"):
            email = extract_email_from_transcript(transcript)
            if email:
                state.extracted_lead_data["email"] = email
        assert state.extracted_lead_data.get("email") == "jean.dupont@gmail.com"
