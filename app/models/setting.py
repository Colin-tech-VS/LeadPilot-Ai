from datetime import datetime, timezone

from app.core.extensions import db


def utcnow():
    return datetime.now(timezone.utc)


class SiteSetting(db.Model):
    """Generic key/value store for admin-editable configuration (Facebook page
    credentials, feature toggles…). Schema-light on purpose so new settings can
    be added without a migration."""

    __tablename__ = "site_settings"

    key = db.Column(db.String(80), primary_key=True)
    value = db.Column(db.Text, nullable=True)
    updated_at = db.Column(
        db.DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )
