"""End-to-end: Stripe Connect deposit flow (mocked Stripe API)."""

import json
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from app.core.extensions import db
from app.models.quote import DOC_DEVIS, STATUS_ACCEPTED, STATUS_SENT, Quote
from app.models.tenant import Tenant
from app.models.user import User
from app.services import billing, quote_payment, stripe_connect


def _seed_connect_artisan(app):
    with app.app_context():
        tid = uuid.uuid4()
        uid = uuid.uuid4()
        tenant = Tenant(
            id=tid,
            name="Plombier Connect Test",
            latitude=48.8566,
            longitude=2.3522,
            address="Paris",
            city="Paris",
            plan="pro",
            stripe_connect_account_id="acct_e2e_test",
            stripe_connect_charges_enabled=True,
            iban="FR7612345678901234567890123",
        )
        user = User(
            id=uid,
            tenant_id=tid,
            email=f"e2e-{uid}@example.com",
            password_hash="test",
            role="owner",
        )
        quote = Quote(
            id=uuid.uuid4(),
            tenant_id=tid,
            doc_type=DOC_DEVIS,
            number="DEV-E2E-001",
            client_name="Marie Client",
            client_email="marie@example.com",
            client_phone="+33601020304",
            title="Réparation fuite",
            deposit_percent=30,
            status=STATUS_SENT,
            public_token="tok-e2e-connect",
        )
        quote.set_items(
            [{"label": "Intervention", "quantity": 1, "unit_price": 200, "tva_rate": 10}]
        )
        quote.ensure_token()
        db.session.add_all([tenant, user, quote])
        db.session.commit()
        return uid, tid, quote.id, quote.public_token


def test_e2e_connect_onboarding_redirect(client, app):
    uid, tid, _, _ = _seed_connect_artisan(app)
    mock_link = MagicMock(url="https://connect.stripe.com/setup/e/test")

    with app.app_context():
        app.config["STRIPE_SECRET_KEY"] = "sk_test_e2e"
    with patch("app.services.stripe_connect._client") as mock_client:
        mock_client.return_value.AccountLink.create.return_value = mock_link
        with client.session_transaction() as sess:
            sess["user_id"] = str(uid)
            sess["tenant_id"] = str(tid)
        resp = client.post("/settings/stripe-connect", follow_redirects=False)

    assert resp.status_code in (302, 303)
    assert resp.headers["Location"] == "https://connect.stripe.com/setup/e/test"


def test_e2e_public_quote_shows_card_when_connect_ready(client, app):
    _, _, qid, token = _seed_connect_artisan(app)
    with app.app_context():
        app.config["STRIPE_SECRET_KEY"] = "sk_test_e2e"

    resp = client.get(f"/quotes/public/{qid}/{token}")
    assert resp.status_code == 200
    body = resp.data.decode("utf-8")
    assert "Carte bancaire" in body or "Bank card" in body
    assert "Virement" in body or "transfer" in body.lower()


def test_e2e_card_acceptance_creates_destination_checkout(client, app):
    _, tid, qid, token = _seed_connect_artisan(app)
    with app.app_context():
        app.config["STRIPE_SECRET_KEY"] = "sk_test_e2e"
        tenant = db.session.get(Tenant, tid)
        quote = db.session.get(Quote, qid)

    mock_session = MagicMock()
    mock_session.id = "cs_e2e_test"
    mock_session.url = "https://checkout.stripe.com/c/pay/e2e"

    with patch("app.services.quote_payment._stripe") as mock_stripe:
        mock_stripe.return_value.checkout.Session.create.return_value = mock_session
        resp = client.post(
            f"/quotes/public/{qid}/{token}/decision",
            data={
                "decision": "accept",
                "client_email": "marie@example.com",
                "client_signed_name": "Marie Client",
                "payment_method": "card",
            },
            follow_redirects=False,
        )

    assert resp.status_code == 302
    assert resp.headers["Location"] == "https://checkout.stripe.com/c/pay/e2e"

    create_kwargs = mock_stripe.return_value.checkout.Session.create.call_args.kwargs
    assert create_kwargs["payment_intent_data"]["transfer_data"]["destination"] == "acct_e2e_test"
    assert create_kwargs["metadata"]["kind"] == "quote_deposit"


def test_e2e_webhook_completes_deposit_and_accepts_quote(app):
    _, tid, qid, _ = _seed_connect_artisan(app)
    with app.app_context():
        quote = db.session.get(Quote, qid)
        quote.client_signed_name = "Marie Client"
        quote.client_signed_at = datetime.now(timezone.utc)
        db.session.commit()

        session_obj = {
            "id": "cs_e2e_webhook",
            "payment_status": "paid",
            "metadata": {
                "kind": "quote_deposit",
                "quote_id": str(qid),
                "tenant_id": str(tid),
            },
        }
        ok = billing.apply_event("checkout.session.completed", session_obj)
        assert ok is True

        db.session.refresh(quote)
        assert quote.deposit_paid_at is not None
        assert quote.status == STATUS_ACCEPTED


def test_e2e_send_quote_blocked_without_payment_method(client, app):
    uid, tid, qid, _ = _seed_connect_artisan(app)
    with app.app_context():
        tenant = db.session.get(Tenant, tid)
        tenant.stripe_connect_charges_enabled = False
        tenant.stripe_connect_account_id = None
        tenant.iban = None
        db.session.commit()

    with client.session_transaction() as sess:
        sess["user_id"] = str(uid)
        sess["tenant_id"] = str(tid)

    resp = client.post(f"/quotes/{qid}/send", data={"channel": "email"}, follow_redirects=False)
    assert resp.status_code == 302
    assert "no_payment_method" in resp.headers["Location"]


def test_e2e_account_updated_webhook_enables_connect(app):
    acct_id = f"acct_wh_{uuid.uuid4().hex[:12]}"
    with app.app_context():
        app.config["STRIPE_SECRET_KEY"] = "sk_test_e2e"
        tid = uuid.uuid4()
        tenant = Tenant(
            id=tid,
            name="Webhook Artisan",
            stripe_connect_account_id=acct_id,
            stripe_connect_charges_enabled=False,
        )
        db.session.add(tenant)
        db.session.commit()

        ok = stripe_connect.handle_account_updated(
            {"id": acct_id, "charges_enabled": True}
        )
        assert ok is True
        db.session.refresh(tenant)
        assert tenant.stripe_connect_charges_enabled is True
        assert stripe_connect.connect_ready(tenant) is True
