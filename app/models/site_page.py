import uuid
from datetime import datetime, timezone

from sqlalchemy import Uuid

from app.core.extensions import db


def utcnow():
    return datetime.now(timezone.utc)


class SitePage(db.Model):
    """A custom marketing/content page authored from the admin console (manually
    or generated with Mistral). Published pages are served publicly at
    ``/p/<slug>``; drafts are only visible through the admin preview."""

    __tablename__ = "site_pages"

    id = db.Column(Uuid, primary_key=True, default=uuid.uuid4)
    slug = db.Column(db.String(120), unique=True, nullable=False, index=True)
    title = db.Column(db.String(200), nullable=False, default="")
    meta_description = db.Column(db.String(300), nullable=True)
    body_html = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(20), nullable=False, default="draft", index=True)  # draft|published
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    @property
    def is_published(self):
        return self.status == "published"
