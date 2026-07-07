"""Chatbot account flow — same steps as voice IA."""

from app.services.chat_account_flow import ChatAccountFlow
from app.services.voice import customer_account as vca


def test_chat_account_flow_guest_after_double_no():
    flow = ChatAccountFlow(vca.default_account_flow(), [])
    lead = {"summary": "fuite sous l evier"}

    slot, question = flow.next_question(lead)
    assert slot == "account:has_account"
    flow.asked_slots.append(slot)

    flow.update_from_message("non", lead)
    slot, question = flow.next_question(lead)
    assert slot == "account:create_pitch"
    flow.asked_slots.append(slot)

    flow.update_from_message("non", lead)
    slot, question = flow.next_question(lead)
    assert slot == "account:guest_email"
    assert "e-mail" in question.lower()
    flow.asked_slots.append(slot)

    flow.update_from_message("marie.martin@orange.fr", lead)
    assert flow.account_flow["account_done"] is True
    assert lead["email"] == "marie.martin@orange.fr"
    assert flow.account_flow["guest_email"] == "marie.martin@orange.fr"


def test_accumulate_lead_data_keeps_guest_email():
    from app.services.chatbot import _accumulate_lead_data

    flow = vca.default_account_flow()
    flow["guest_email"] = "famillejoossencayre@gmail.com"
    merged = _accumulate_lead_data(
        {},
        [{"role": "user", "text": "fuite baignoire"}],
        "0659555664",
        flow,
        None,
    )
    assert merged["email"] == "famillejoossencayre@gmail.com"
    assert merged["phone"] == "0659555664"


def test_chat_account_flow_create_pitch_yes():
    af = vca.default_account_flow()
    af["has_account"] = False
    flow = ChatAccountFlow(af, ["account:has_account", "account:create_pitch"])

    flow.update_from_message("oui", {})
    slot, _ = flow.next_question({})
    assert slot == "account:create_name"


def test_process_chat_turn_asks_account_question(app):
    from app.models.tenant import Tenant
    from app.services.chatbot import process_chat_turn

    with app.app_context():
        tenant = Tenant.query.first()
        assert tenant is not None

        result = process_chat_turn(
            tenant_id=str(tenant.id),
            history=[],
            message="J ai une fuite d eau sous l evier",
        )

        assert "compte client PilotCore" in result["reply"]
        assert result["lead_captured"] is False
        assert "account:has_account" in result["asked_slots"]
        assert result["account_flow"]["has_account"] is None
