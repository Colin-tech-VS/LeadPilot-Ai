"""Customer account dashboard — profile and password change."""
import uuid

from app.core.extensions import db
from app.models.user import User
from app.services.signup_service import register_customer


def _register_customer(app):
    with app.app_context():
        email = f"customer-pwd-{uuid.uuid4().hex[:8]}@example.com"
        user = register_customer(
            email=email,
            password="oldpassword1",
            first_name="Marie",
            last_name="Dupont",
            phone="+33601020304",
        )
        db.session.commit()
        return str(user.id)


def test_customer_can_change_password(client, app):
    user_id = _register_customer(app)

    with client.session_transaction() as sess:
        sess["user_id"] = user_id
        sess["role"] = "customer"

    response = client.post(
        "/client/profile",
        data={
            "first_name": "Marie",
            "last_name": "Dupont",
            "phone": "+33601020304",
            "new_password": "newpassword2",
            "confirm_password": "newpassword2",
        },
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert "password=ok" in response.location

    with app.app_context():
        updated = db.session.get(User, uuid.UUID(user_id))
        assert updated.check_password("newpassword2")
        assert not updated.check_password("oldpassword1")


def test_customer_password_mismatch_shows_error(client, app):
    user_id = _register_customer(app)

    with client.session_transaction() as sess:
        sess["user_id"] = user_id
        sess["role"] = "customer"

    response = client.post(
        "/client/profile",
        data={
            "first_name": "Marie",
            "new_password": "newpassword2",
            "confirm_password": "different1",
        },
    )
    assert response.status_code == 200
    assert b"ne correspondent pas" in response.data or b"do not match" in response.data
