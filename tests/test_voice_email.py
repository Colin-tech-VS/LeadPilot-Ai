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


def _drive_account_flow(app, answer_fn, prefill=None, max_turns=15):
    """Run the account sub-flow with a caller who answers via answer_fn."""
    from app.services.voice import customer_account as vca

    with app.app_context():
        handler = TwilioVoiceHandler()
        state = ConversationState(call_id="c", tenant_id="t", caller_phone="+33600000000")
        state.extracted_lead_data = {"issue_type": "leak", "urgency_level": "high"}
        state.account_flow = vca.default_account_flow()
        if prefill:
            state.account_flow.update(prefill)

        for _ in range(max_turns):
            nxt = handler._next_account_question(state)
            if nxt is None:
                return state
            slot, _prompt = nxt
            if slot not in state.asked_slots:
                state.asked_slots.append(slot)
            if slot.startswith("account:"):
                counts = state.account_flow.setdefault("ask_counts", {})
                counts[slot] = counts.get(slot, 0) + 1
            answer = answer_fn(slot)
            state.append_transcript("user", answer)
            handler._update_account_flow(state, answer)
        raise AssertionError("account flow did not terminate — infinite loop")


def test_account_flow_terminates_on_unclear_answers(app):
    # Caller never gives a clear yes/no nor a usable e-mail.
    state = _drive_account_flow(app, lambda slot: "euh je sais pas")
    assert state.account_flow.get("account_done") is True


def test_account_flow_terminates_when_email_unparseable(app):
    # Caller says they have an account but can never spell a valid e-mail.
    def answer(slot):
        return "oui" if slot == "account:has_account" else "euh blabla"

    state = _drive_account_flow(app, answer)
    assert state.account_flow.get("account_done") is True


def test_guest_email_captured_from_dictation(app):
    def answer(slot):
        if slot == "account:has_account":
            return "non"
        if slot == "account:create_pitch":
            return "non merci"
        if slot == "account:guest_email":
            return "jean point dupont arobase gmail point com"
        return "euh"

    state = _drive_account_flow(app, answer)
    assert state.extracted_lead_data.get("email") == "jean.dupont@gmail.com"
    assert state.account_flow.get("account_done") is True
