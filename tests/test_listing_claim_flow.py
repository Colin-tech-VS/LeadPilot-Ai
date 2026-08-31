"""The public claim flow, end to end: form → admin decision → artisan account.

``test_listing_link`` already covers the silent path (a SIREN typed at signup
attaches the fiche without review). This file covers the other one, the only
route open to an artisan who has no account yet: the claim form on their own
fiche, and the human decision that turns it into an account.
"""
import uuid

from app.core.extensions import db
from app.models.listing_claim import (
    STATUS_APPROVED,
    STATUS_PENDING,
    STATUS_REJECTED,
    ListingClaim,
)
from app.models.registry_listing import (
    STATUS_CLAIMED,
    STATUS_LISTED,
    STATUS_OPTED_OUT,
    RegistryListing,
)
from app.models.tenant import Tenant


def _listing(**overrides):
    row = RegistryListing(
        siren=overrides.pop("siren", str(uuid.uuid4().int)[:9]),
        name=overrides.pop("name", "PLOMBERIE DU CENTRE"),
        city=overrides.pop("city", "Compiègne"),
        city_slug=overrides.pop("city_slug", "compiegne"),
        postal_code=overrides.pop("postal_code", "60200"),
        dept_code=overrides.pop("dept_code", "60"),
        trade_key=overrides.pop("trade_key", "plombier"),
        address=overrides.pop("address", "4 rue Solférino"),
        siret=overrides.pop("siret", None),
        status=overrides.pop("status", STATUS_LISTED),
        **overrides,
    )
    db.session.add(row)
    db.session.commit()
    return row


def _claim_form(**overrides):
    data = {
        "contact_name": "Jean Dupont",
        "email": f"artisan-{uuid.uuid4().hex[:10]}@example.com",
        "phone": "+33601020304",
    }
    data.update(overrides)
    return data


def _submit(client, siren, form):
    """POST the claim form as a fresh visitor.

    The form is rate-limited to ten submissions an hour per IP, and the limiter
    is per-process, so without a distinct address every test in this file would
    be spending one shared budget and the later ones would silently get a 429.
    """
    return client.post(
        f"/artisans/revendiquer/{siren}",
        data=form,
        headers={"X-Forwarded-For": f"203.0.113.{uuid.uuid4().int % 250 + 1}"},
        environ_base={"REMOTE_ADDR": f"198.51.100.{uuid.uuid4().int % 250 + 1}"},
    )


def _login_admin(client):
    with client.session_transaction() as sess:
        sess["admin_authenticated"] = True
        sess["admin_username"] = "admin"


def test_the_fiche_offers_the_claim_link(client, app):
    """The loop only starts if the artisan can see the way in from their fiche."""
    with app.app_context():
        siren = _listing().siren

    html = client.get(f"/artisans/entreprise/{siren}").get_data(as_text=True)
    assert f"/artisans/revendiquer/{siren}" in html


def test_the_claim_form_is_served_for_a_listed_fiche(client, app):
    with app.app_context():
        siren = _listing(name="MENUISERIE BERTIN", trade_key="menuisier").siren

    response = client.get(f"/artisans/revendiquer/{siren}")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "MENUISERIE BERTIN" in html
    assert 'name="contact_name"' in html
    assert 'name="email"' in html


def test_a_submitted_claim_is_recorded_and_the_fiche_stays_public(client, app):
    """A claim is a request, not a transfer: nothing moves until a human says so."""
    with app.app_context():
        siren = _listing().siren
    form = _claim_form()

    response = _submit(client, siren, form)
    assert response.status_code == 200
    assert "Demande enregistrée" in response.get_data(as_text=True)

    with app.app_context():
        claim = ListingClaim.query.filter_by(email=form["email"]).one()
        assert claim.siren == siren
        assert claim.status == STATUS_PENDING
        assert claim.contact_name == "Jean Dupont"
        assert claim.listing is not None
        # Untouched: the fiche is still the public one until the claim is decided.
        assert RegistryListing.query.filter_by(siren=siren).one().status == STATUS_LISTED


def test_a_claim_without_a_usable_contact_is_refused(client, app):
    with app.app_context():
        siren = _listing().siren

    no_email = _submit(client, siren, _claim_form(email="pas-un-email"))
    assert "e-mail valide" in no_email.get_data(as_text=True)

    no_name = _submit(client, siren, _claim_form(contact_name="  "))
    assert "votre nom" in no_name.get_data(as_text=True)

    with app.app_context():
        assert ListingClaim.query.filter_by(siren=siren).count() == 0


def test_a_withdrawn_or_already_claimed_fiche_cannot_be_claimed(client, app):
    """Both are 404: a fiche someone owns is not up for grabs, and a withdrawn
    one must not even confirm that it was ever in the directory."""
    with app.app_context():
        gone = _listing(status=STATUS_OPTED_OUT).siren
        taken = _listing(status=STATUS_CLAIMED).siren

    assert client.get(f"/artisans/revendiquer/{gone}").status_code == 404
    assert client.get(f"/artisans/revendiquer/{taken}").status_code == 404
    assert _submit(client, taken, _claim_form()).status_code == 404


def test_the_admin_sees_the_pending_claim(client, app):
    with app.app_context():
        siren = _listing(name="COUVERTURE MARTIN").siren
    form = _claim_form()
    _submit(client, siren, form)

    _login_admin(client)
    html = client.get("/admin/revendications").get_data(as_text=True)
    assert "COUVERTURE MARTIN" in html
    assert form["email"] in html
    assert siren in html


def test_approving_creates_the_account_and_transfers_the_fiche(client, app):
    with app.app_context():
        siren = _listing(name="ELEC DUBOIS", trade_key="electricien").siren
    form = _claim_form()
    _submit(client, siren, form)

    _login_admin(client)
    with app.app_context():
        claim_id = ListingClaim.query.filter_by(email=form["email"]).one().id

    response = client.post(f"/admin/revendications/{claim_id}/approve", data={"note": "Kbis reçu"})
    assert response.status_code == 302

    with app.app_context():
        from app.models.user import User

        claim = db.session.get(ListingClaim, claim_id)
        assert claim.status == STATUS_APPROVED
        assert claim.decided_at is not None
        assert claim.created_tenant_id is not None

        listing = RegistryListing.query.filter_by(siren=siren).one()
        assert listing.status == STATUS_CLAIMED
        assert listing.claimed_tenant_id == claim.created_tenant_id

        user = User.query.filter_by(email=form["email"]).one()
        assert user.tenant_id == claim.created_tenant_id
        tenant = db.session.get(Tenant, claim.created_tenant_id)
        # The register fills the blanks the artisan never had to type.
        assert tenant.city == "Compiègne"
        assert tenant.address == "4 rue Solférino"


def test_approving_mails_the_artisan_a_way_in_rather_than_a_password(client, app):
    """We never invent a password for someone and send it in the clear."""
    with app.app_context():
        siren = _listing(name="PEINTURE VIDAL", trade_key="peintre").siren
    form = _claim_form()
    _submit(client, siren, form)

    _login_admin(client)
    with app.app_context():
        claim_id = ListingClaim.query.filter_by(email=form["email"]).one().id

    client.post(f"/admin/revendications/{claim_id}/approve")

    with app.app_context():
        from app.models.email_message import EmailMessage

        mail = (
            EmailMessage.query.filter_by(to_addr=form["email"])
            .order_by(EmailMessage.created_at.desc())
            .first()
        )
        assert mail is not None
        body = (mail.html_body or "") + (mail.body or "")
        assert "forgot-password" in body


def test_approving_a_claim_from_an_existing_account_links_it(client, app):
    """No duplicate tenant: the fiche joins the account the e-mail already has."""
    from app.services.signup_service import register_plumber

    email = f"deja-client-{uuid.uuid4().hex[:8]}@example.com"
    with app.app_context():
        _user, tenant = register_plumber(
            email=email,
            password="password1",
            company_name="SARL DEJA CLIENT",
            phone="+33601020304",
            city="Compiègne",
            trade_type="plombier",
        )
        tenant_id = tenant.id
        siren = _listing(name="SARL DEJA CLIENT").siren

    _submit(client, siren, _claim_form(email=email))

    _login_admin(client)
    with app.app_context():
        claim_id = ListingClaim.query.filter_by(email=email).one().id

    client.post(f"/admin/revendications/{claim_id}/approve")

    with app.app_context():
        from app.models.user import User

        assert User.query.filter_by(email=email).count() == 1
        claim = db.session.get(ListingClaim, claim_id)
        assert claim.status == STATUS_APPROVED
        assert claim.created_tenant_id == tenant_id
        listing = RegistryListing.query.filter_by(siren=siren).one()
        assert listing.status == STATUS_CLAIMED
        assert listing.claimed_tenant_id == tenant_id


def test_rejecting_leaves_the_fiche_where_it_was(client, app):
    with app.app_context():
        siren = _listing().siren
    form = _claim_form()
    _submit(client, siren, form)

    _login_admin(client)
    with app.app_context():
        claim_id = ListingClaim.query.filter_by(email=form["email"]).one().id

    assert client.post(
        f"/admin/revendications/{claim_id}/reject", data={"note": "Aucun lien avec l'entreprise"}
    ).status_code == 302

    with app.app_context():
        from app.models.user import User

        claim = db.session.get(ListingClaim, claim_id)
        assert claim.status == STATUS_REJECTED
        assert claim.decision_note == "Aucun lien avec l'entreprise"
        assert RegistryListing.query.filter_by(siren=siren).one().status == STATUS_LISTED
        assert User.query.filter_by(email=form["email"]).first() is None


def test_a_decided_claim_cannot_be_decided_again(client, app):
    """Double-submitting « Valider » must not create a second account."""
    with app.app_context():
        siren = _listing().siren
    form = _claim_form()
    _submit(client, siren, form)

    _login_admin(client)
    with app.app_context():
        claim_id = ListingClaim.query.filter_by(email=form["email"]).one().id

    client.post(f"/admin/revendications/{claim_id}/approve")
    client.post(f"/admin/revendications/{claim_id}/approve")

    with app.app_context():
        from app.models.user import User

        assert User.query.filter_by(email=form["email"]).count() == 1


def test_deciding_a_claim_requires_an_admin(client, app):
    with app.app_context():
        siren = _listing().siren
    form = _claim_form()
    _submit(client, siren, form)
    with app.app_context():
        claim_id = ListingClaim.query.filter_by(email=form["email"]).one().id

    assert client.post(f"/admin/revendications/{claim_id}/approve").status_code in (302, 401, 403)
    with app.app_context():
        assert db.session.get(ListingClaim, claim_id).status == STATUS_PENDING


def _pending_claim(listing, email):
    """A claim recorded straight against a fiche, whatever its current status.

    The public form only serves listed fiches, so this is how a claim that was
    filed while the fiche was still free is put back in front of the admin after
    the fiche has moved on.
    """
    claim = ListingClaim(
        listing_id=listing.id, siren=listing.siren, contact_name="Jean Dupont", email=email
    )
    db.session.add(claim)
    db.session.commit()
    return claim


def test_a_fiche_that_moved_on_is_not_transferred_twice(client, app):
    """A claim can sit in the queue for days, and the fiche can leave in the
    meantime — the owner signing up with their SIREN takes it silently. Approving
    then used to create an account, mail « votre fiche vous a été transférée »,
    and report success, while the fiche stayed with its owner."""
    from app.services.signup_service import register_plumber

    latecomer = f"tardif-{uuid.uuid4().hex[:8]}@example.com"
    with app.app_context():
        from app.services import listing_claims

        listing = _listing(name="PLOMBERIE PARTIE")
        siren = listing.siren
        claim_id = _pending_claim(listing, latecomer).id

        _user, owner = register_plumber(
            email=f"proprio-{uuid.uuid4().hex[:8]}@example.com",
            password="password1",
            company_name="PLOMBERIE PARTIE",
            phone="+33601020304",
            city="Compiègne",
            trade_type="plombier",
        )
        listing_claims.attach(siren, owner.id)
        owner_id = owner.id

    _login_admin(client)
    response = client.post(f"/admin/revendications/{claim_id}/approve", follow_redirects=True)
    assert "déjà rattachée" in response.get_data(as_text=True)

    with app.app_context():
        from app.models.user import User

        # The fiche stayed with its owner, no account was invented for the
        # claimant, and the demand is still there for a human to refuse.
        assert RegistryListing.query.filter_by(siren=siren).one().claimed_tenant_id == owner_id
        assert User.query.filter_by(email=latecomer).first() is None
        assert db.session.get(ListingClaim, claim_id).status == STATUS_PENDING


def test_approving_a_claim_the_account_already_owns_is_harmless(client, app):
    """The owner who signed up with their SIREN, then filed a claim as well:
    approving is a no-op on the fiche, not a refusal."""
    from app.services.signup_service import register_plumber

    email = f"proprio-{uuid.uuid4().hex[:8]}@example.com"
    with app.app_context():
        from app.services import listing_claims

        listing = _listing(name="PLOMBERIE PROPRIO")
        siren = listing.siren
        claim_id = _pending_claim(listing, email).id

        _user, owner = register_plumber(
            email=email,
            password="password1",
            company_name="PLOMBERIE PROPRIO",
            phone="+33601020304",
            city="Compiègne",
            trade_type="plombier",
        )
        listing_claims.attach(siren, owner.id)
        owner_id = owner.id

    _login_admin(client)
    client.post(f"/admin/revendications/{claim_id}/approve")

    with app.app_context():
        claim = db.session.get(ListingClaim, claim_id)
        assert claim.status == STATUS_APPROVED
        assert claim.created_tenant_id == owner_id
        assert RegistryListing.query.filter_by(siren=siren).one().claimed_tenant_id == owner_id


def test_a_withdrawn_fiche_is_never_handed_over(client, app):
    """Erasure wins over a claim filed before it."""
    with app.app_context():
        listing = _listing(name="RETIRÉE")
        siren = listing.siren
        email = f"tardif-{uuid.uuid4().hex[:8]}@example.com"
        claim_id = _pending_claim(listing, email).id

        from app.services import listing_claims

        listing_claims.opt_out(siren, reason="RGPD")

    _login_admin(client)
    response = client.post(f"/admin/revendications/{claim_id}/approve", follow_redirects=True)
    assert "retrait" in response.get_data(as_text=True)

    with app.app_context():
        from app.models.user import User

        assert User.query.filter_by(email=email).first() is None
        assert db.session.get(ListingClaim, claim_id).status == STATUS_PENDING
        assert RegistryListing.query.filter_by(siren=siren).one().status == STATUS_OPTED_OUT


def test_the_form_caps_submissions_without_closing_the_page(client, app):
    """Ten claims an hour per address is the limit; reading the page is not
    rationed, and the eleventh submission gets a sentence rather than a 429."""
    with app.app_context():
        siren = _listing(name="ATELIER LIMITE").siren
    one_address = {"X-Forwarded-For": "192.0.2.77"}

    for _ in range(11):
        last = client.post(
            f"/artisans/revendiquer/{siren}", data=_claim_form(), headers=one_address
        )

    assert last.status_code == 200
    assert "Trop de demandes" in last.get_data(as_text=True)
    assert client.get(f"/artisans/revendiquer/{siren}", headers=one_address).status_code == 200

    with app.app_context():
        assert ListingClaim.query.filter_by(siren=siren).count() == 10
