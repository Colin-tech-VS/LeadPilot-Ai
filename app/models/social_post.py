import uuid
from datetime import datetime, timezone

from sqlalchemy import Uuid

from app.core.extensions import db


def utcnow():
    return datetime.now(timezone.utc)


class SocialPost(db.Model):
    """A social-media post composed in the admin console (manually or generated
    with Mistral) and published to a connected network (currently Facebook
    Pages via the Graph API)."""

    __tablename__ = "social_posts"

    id = db.Column(Uuid, primary_key=True, default=uuid.uuid4)
    platform = db.Column(db.String(20), nullable=False, default="facebook", index=True)
    message = db.Column(db.Text, nullable=True)
    link = db.Column(db.String(500), nullable=True)
    image_path = db.Column(db.String(300), nullable=True)  # static/uploads/social/…
    image_blob = db.Column(db.LargeBinary, nullable=True)
    status = db.Column(db.String(20), nullable=False, default="draft", index=True)  # draft|queued|published|failed|skipped
    external_id = db.Column(db.String(120), nullable=True)   # Facebook post id
    permalink = db.Column(db.String(500), nullable=True)
    error = db.Column(db.String(500), nullable=True)
    generated_by_ai = db.Column(db.Boolean, default=False, nullable=False)
    target_key = db.Column(db.String(40), nullable=True)
    scheduled_for = db.Column(db.DateTime(timezone=True), nullable=True, index=True)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False, index=True)
    published_at = db.Column(db.DateTime(timezone=True), nullable=True)

    def preview(self, length=140):
        text = (self.message or "").strip().replace("\n", " ")
        return text[:length] + ("…" if len(text) > length else "")
