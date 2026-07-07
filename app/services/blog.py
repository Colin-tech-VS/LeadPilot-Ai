"""Blog listing, categories and default seed data."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import or_

from app.core.extensions import db
from app.models.blog_category import BlogCategory
from app.models.blog_post import BlogPost

DEFAULT_CATEGORIES = (
    {
        "slug": "conseils-artisans",
        "name": "Conseils artisans",
        "description": "Gestion d'activité, relation client et productivité pour les pros du bâtiment.",
        "sort_order": 10,
    },
    {
        "slug": "depannage-maison",
        "name": "Dépannage & maison",
        "description": "Guides pratiques pour les particuliers : plomberie, électricité, urgences.",
        "sort_order": 20,
    },
    {
        "slug": "telephonie-ia",
        "name": "Téléphonie & IA",
        "description": "Standard téléphonique, assistant vocal et innovation pour artisans.",
        "sort_order": 30,
    },
    {
        "slug": "actualites-pilotcore",
        "name": "Actualités PilotCore",
        "description": "Nouveautés produit, annuaire et vie de la plateforme.",
        "sort_order": 40,
    },
)


def ensure_default_categories() -> None:
    """Idempotent seed of predefined blog categories."""
    for item in DEFAULT_CATEGORIES:
        existing = BlogCategory.query.filter_by(slug=item["slug"]).first()
        if existing:
            continue
        db.session.add(
            BlogCategory(
                name=item["name"],
                slug=item["slug"],
                description=item["description"],
                sort_order=item["sort_order"],
            )
        )
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise


def list_categories():
    return BlogCategory.query.order_by(BlogCategory.sort_order, BlogCategory.name).all()


def get_category_by_slug(slug: str) -> BlogCategory | None:
    return BlogCategory.query.filter_by(slug=(slug or "").strip()).first()


def published_posts_query(*, category_id=None, exclude_id=None):
    q = BlogPost.query.filter_by(status="published")
    if category_id:
        q = q.filter(BlogPost.category_id == category_id)
    if exclude_id:
        q = q.filter(BlogPost.id != exclude_id)
    return q.order_by(
        BlogPost.featured.desc(),
        BlogPost.published_at.desc().nullslast(),
        BlogPost.updated_at.desc(),
    )


def list_published_posts(limit=50, *, category_id=None):
    return published_posts_query(category_id=category_id).limit(limit).all()


def get_published_post(slug: str) -> BlogPost | None:
    from sqlalchemy.orm import joinedload

    return (
        BlogPost.query.options(joinedload(BlogPost.category))
        .filter_by(slug=(slug or "").strip(), status="published")
        .first()
    )


def featured_post():
    return (
        published_posts_query()
        .filter(BlogPost.featured.is_(True))
        .first()
        or published_posts_query().first()
    )


def related_posts(post: BlogPost, limit=3):
    if not post:
        return []
    if not post.category_id:
        return published_posts_query(exclude_id=post.id).limit(limit).all()
    return published_posts_query(category_id=post.category_id, exclude_id=post.id).limit(limit).all()


def admin_list_posts():
    return (
        BlogPost.query.outerjoin(BlogCategory)
        .order_by(BlogPost.updated_at.desc())
        .all()
    )


def search_posts_public(q: str, limit=20):
    term = f"%{(q or '').strip()}%"
    if not term.strip("%"):
        return []
    return (
        BlogPost.query.filter(
            BlogPost.status == "published",
            or_(
                BlogPost.title.ilike(term),
                BlogPost.excerpt.ilike(term),
                BlogPost.body_html.ilike(term),
            ),
        )
        .order_by(BlogPost.published_at.desc().nullslast())
        .limit(limit)
        .all()
    )


def touch_published_at(post: BlogPost, *, publishing: bool) -> None:
    if publishing and not post.published_at:
        post.published_at = datetime.now(timezone.utc)


def category_post_counts() -> dict:
    """Return {category_id: post_count} for admin UI."""
    from sqlalchemy import func

    rows = (
        db.session.query(BlogPost.category_id, func.count(BlogPost.id))
        .group_by(BlogPost.category_id)
        .all()
    )
    return {category_id: count for category_id, count in rows if category_id}


def ensure_blog_schema() -> None:
    """Idempotent blog tables — matches SQLAlchemy ``Uuid`` on PostgreSQL."""
    from sqlalchemy import inspect, text

    inspector = inspect(db.engine)
    dialect = db.engine.dialect.name
    ts_type = "TIMESTAMP WITH TIME ZONE" if dialect == "postgresql" else "DATETIME"
    id_type = "UUID" if dialect == "postgresql" else "VARCHAR(36)"
    bool_type = "BOOLEAN" if dialect == "postgresql" else "INTEGER"
    false_lit = "FALSE" if dialect == "postgresql" else "0"

    with db.engine.begin() as conn:
        conn.execute(
            text(
                f"""
                CREATE TABLE IF NOT EXISTS blog_categories (
                    id {id_type} PRIMARY KEY,
                    name VARCHAR(120) NOT NULL,
                    slug VARCHAR(120) NOT NULL UNIQUE,
                    description VARCHAR(400),
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    created_at {ts_type}
                )
                """
            )
        )
        conn.execute(
            text(
                f"""
                CREATE TABLE IF NOT EXISTS blog_posts (
                    id {id_type} PRIMARY KEY,
                    slug VARCHAR(160) NOT NULL UNIQUE,
                    title VARCHAR(220) NOT NULL DEFAULT '',
                    excerpt VARCHAR(400),
                    meta_description VARCHAR(300),
                    meta_keywords VARCHAR(400),
                    body_html TEXT,
                    category_id {id_type},
                    status VARCHAR(20) NOT NULL DEFAULT 'draft',
                    featured {bool_type} NOT NULL DEFAULT {false_lit},
                    reading_time_min INTEGER,
                    faq_json TEXT,
                    published_at {ts_type},
                    created_at {ts_type},
                    updated_at {ts_type},
                    FOREIGN KEY(category_id) REFERENCES blog_categories(id)
                )
                """
            )
        )

    if dialect != "postgresql" or "blog_categories" not in inspector.get_table_names():
        return

    id_col = next((c for c in inspector.get_columns("blog_categories") if c["name"] == "id"), None)
    id_type_name = str(id_col["type"]).upper() if id_col else ""
    if "VARCHAR" not in id_type_name and "CHARACTER VARYING" not in id_type_name:
        return

    with db.engine.begin() as conn:
        conn.execute(text("ALTER TABLE blog_posts DROP CONSTRAINT IF EXISTS blog_posts_category_id_fkey"))
        conn.execute(text("ALTER TABLE blog_categories ALTER COLUMN id TYPE UUID USING id::uuid"))
        if "blog_posts" in inspector.get_table_names():
            conn.execute(text("ALTER TABLE blog_posts ALTER COLUMN id TYPE UUID USING id::uuid"))
            conn.execute(
                text(
                    "ALTER TABLE blog_posts ALTER COLUMN category_id TYPE UUID "
                    "USING NULLIF(category_id, '')::uuid"
                )
            )
            conn.execute(
                text(
                    "ALTER TABLE blog_posts ADD CONSTRAINT blog_posts_category_id_fkey "
                    "FOREIGN KEY (category_id) REFERENCES blog_categories(id)"
                )
            )
