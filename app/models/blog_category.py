import uuid
from datetime import datetime, timezone

from sqlalchemy import Uuid

from app.core.extensions import db


def utcnow():
    return datetime.now(timezone.utc)


class BlogCategory(db.Model):
    """Blog section (e.g. Conseils artisans, Dépannage & maison)."""

    __tablename__ = "blog_categories"

    id = db.Column(Uuid, primary_key=True, default=uuid.uuid4)
    name = db.Column(db.String(120), nullable=False)
    slug = db.Column(db.String(120), unique=True, nullable=False, index=True)
    description = db.Column(db.String(400), nullable=True)
    sort_order = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)

    posts = db.relationship("BlogPost", back_populates="category", lazy="dynamic")

    def __repr__(self):
        return f"<BlogCategory {self.slug}>"
