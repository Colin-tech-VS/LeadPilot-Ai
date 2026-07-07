import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import ForeignKey, Uuid

from app.core.extensions import db


def utcnow():
    return datetime.now(timezone.utc)


class BlogPost(db.Model):
    """SEO-optimised blog article served at ``/blog/<slug>``."""

    __tablename__ = "blog_posts"

    id = db.Column(Uuid, primary_key=True, default=uuid.uuid4)
    slug = db.Column(db.String(160), unique=True, nullable=False, index=True)
    title = db.Column(db.String(220), nullable=False, default="")
    excerpt = db.Column(db.String(400), nullable=True)
    meta_description = db.Column(db.String(300), nullable=True)
    meta_keywords = db.Column(db.String(400), nullable=True)
    body_html = db.Column(db.Text, nullable=True)
    category_id = db.Column(Uuid, ForeignKey("blog_categories.id"), nullable=True, index=True)
    status = db.Column(db.String(20), nullable=False, default="draft", index=True)
    featured = db.Column(db.Boolean, default=False, nullable=False)
    reading_time_min = db.Column(db.Integer, nullable=True)
    faq_json = db.Column(db.Text, nullable=True)
    published_at = db.Column(db.DateTime(timezone=True), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = db.Column(
        db.DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    category = db.relationship("BlogCategory", back_populates="posts")

    @property
    def is_published(self):
        return self.status == "published"

    def get_faq(self) -> list[dict]:
        if not self.faq_json:
            return []
        try:
            data = json.loads(self.faq_json)
        except (json.JSONDecodeError, TypeError):
            return []
        if not isinstance(data, list):
            return []
        return [item for item in data if isinstance(item, dict) and item.get("question")]

    def set_faq(self, items: list[dict]) -> None:
        cleaned = [
            {"question": (i.get("question") or "").strip(), "answer": (i.get("answer") or "").strip()}
            for i in (items or [])
            if (i.get("question") or "").strip()
        ]
        self.faq_json = json.dumps(cleaned, ensure_ascii=False) if cleaned else None
