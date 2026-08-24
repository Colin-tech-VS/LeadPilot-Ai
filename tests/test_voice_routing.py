"""The voice line must fail fast and loudly when a number points nowhere."""
import re
import uuid

import pytest

from app.core.extensions import db
from app.models.tenant import Tenant
from app.services.voice.twilio_handler import TwilioVoiceHandler


def _says(xml):
    return " ".join(re.sub(r"\s+", " ", s).strip() for s in re.findall(r"<Say[^>]*>(.*?)</Say>", xml, re.S))


@pytest.fixture
def handler(app):
    with app.test_request_context(base_url="https://www.pilotcore.fr"):
        yield TwilioVoiceHandler()


def test_greeting_names_the_business(app):
    """The branded receptionist is the product — it must introduce the artisan."""
    with app.test_request_context(base_url="https://www.pilotcore.fr"):
        tenant = Tenant(
            name="Plomberie Durand",
            trade_type="plombier",
            city="Chaville",
            plan="pro",
            ai_assistant_name="Léa",
            public_slug=f"d-{uuid.uuid4().hex[:8]}",
        )
        db.session.add(tenant)
        db.session.commit()
        xml = TwilioVoiceHandler().handle_inbound(str(tenant.id), "CA1", "+33600000000")
        said = _says(xml)
    assert "Léa" in said and "Plomberie Durand" in said
    assert "<Gather" in xml


def test_missing_tenant_ends_the_call_immediately(app):
    """Regression: a number wired to a deleted account greeted the caller, invited
    them to explain their problem, and only then — in /process — cut them off with
    "service temporairement indisponible". Every production line was doing this."""
    ghost = str(uuid.uuid4())
    with app.test_request_context(base_url="https://www.pilotcore.fr"):
        xml = TwilioVoiceHandler().handle_inbound(ghost, "CA2", "+33600000000")
    said = _says(xml)
    assert "<Hangup" in xml, "the call must end, not wait for speech"
    assert "<Gather" not in xml, "never invite speech nobody will receive"
    # And it must not promise a call-back there is nobody to make.
    assert "rappellera" not in said and "notée" not in said
    assert "pas rattaché" in said


def test_inbound_and_process_agree_about_a_missing_tenant(app):
    """The two legs disagreed, which is exactly why the failure was invisible."""
    ghost = str(uuid.uuid4())
    with app.test_request_context(base_url="https://www.pilotcore.fr"):
        h = TwilioVoiceHandler()
        inbound = h.handle_inbound(ghost, "CA3", "+33600000000")
        process = h.handle_process(ghost, "CA3", "+33600000000", speech_text="bonjour")
    for xml in (inbound, process):
        assert "<Hangup" in xml
        assert "<Gather" not in xml


def test_routing_probe_flags_a_dangling_default_tenant(app):
    from app.services import diagnostics

    with app.app_context():
        app.config["TWILIO_DEFAULT_TENANT_ID"] = str(uuid.uuid4())
        result = diagnostics.voice_routing_probe()
    assert result["ok"] is False
    assert any("ne correspond à aucun compte" in p for p in result["problems"])


def test_routing_probe_passes_for_a_real_tenant(app):
    from app.services import diagnostics

    with app.app_context():
        tenant = Tenant(
            name="Plomberie Reelle",
            trade_type="plombier",
            plan="pro",
            public_slug=f"r-{uuid.uuid4().hex[:8]}",
        )
        db.session.add(tenant)
        db.session.commit()
        app.config["TWILIO_DEFAULT_TENANT_ID"] = str(tenant.id)
        result = diagnostics.voice_routing_probe()
    assert result["ok"] is True, result["problems"]


def test_routing_probe_flags_a_missing_default(app):
    from app.services import diagnostics

    with app.app_context():
        app.config["TWILIO_DEFAULT_TENANT_ID"] = ""
        result = diagnostics.voice_routing_probe()
    assert result["ok"] is False
