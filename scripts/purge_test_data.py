"""Remove pytest artefacts from the database (test users, fake page views).

Usage:
  python -m scripts.purge_test_data          # uses DATABASE_URL from env
  python -m scripts.purge_test_data --dry-run
"""
import sys
from datetime import datetime, timezone

from sqlalchemy import or_

from app import create_app
from app.core.extensions import db
from app.models.page_view import PageView
from app.models.user import User

DRY = "--dry-run" in sys.argv
FUTURE = datetime(2099, 1, 1, tzinfo=timezone.utc)

TEST_EMAIL_SUFFIXES = ("@test.com",)
TEST_VISITOR_PREFIXES = ("v1", "v2", "v3", "v4", "v-ch-", "v-ts-", "v-utm-")
TEST_SESSION_PREFIXES = ("s1", "s2", "s-ch-", "s-ts-", "s-utm-")

app = create_app()
with app.app_context():
    users = User.query.filter(
        or_(*[User.email.ilike(f"%{s}") for s in TEST_EMAIL_SUFFIXES])
    ).all()

    pv_q = PageView.query.filter(
        or_(
            PageView.visitor_id.in_(["v1", "v2", "v3", "v4"]),
            PageView.visitor_id.like("v-ch-%"),
            PageView.visitor_id.like("v-ts-%"),
            PageView.visitor_id.like("v-utm-%"),
            PageView.created_at >= FUTURE,
        )
    )
    page_views = pv_q.count()

    print(f"Users test (@test.com): {len(users)}")
    for u in users:
        print(f"  - {u.email} ({u.role})")
    print(f"Page views test: {page_views}")

    if DRY:
        print("DRY RUN — rien supprimé.")
        sys.exit(0)

    for u in users:
        db.session.delete(u)
    deleted_pv = pv_q.delete(synchronize_session=False)
    db.session.commit()
    print(f"Supprimé: {len(users)} user(s), {deleted_pv} page view(s).")
