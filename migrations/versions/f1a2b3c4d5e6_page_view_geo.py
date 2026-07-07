"""page_views geo + utm columns"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "f1a2b3c4d5e6"
down_revision: Union[str, None] = "e5f6a7b8c9d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "page_views" in inspector.get_table_names():
        cols = {c["name"] for c in inspector.get_columns("page_views")}
        patches = {
            "geo_country_code": sa.Column("geo_country_code", sa.String(2), nullable=True),
            "geo_country": sa.Column("geo_country", sa.String(80), nullable=True),
            "geo_region": sa.Column("geo_region", sa.String(100), nullable=True),
            "geo_city": sa.Column("geo_city", sa.String(100), nullable=True),
            "geo_postal_code": sa.Column("geo_postal_code", sa.String(20), nullable=True),
            "geo_latitude": sa.Column("geo_latitude", sa.Float(), nullable=True),
            "geo_longitude": sa.Column("geo_longitude", sa.Float(), nullable=True),
            "utm_source": sa.Column("utm_source", sa.String(80), nullable=True),
            "utm_medium": sa.Column("utm_medium", sa.String(80), nullable=True),
            "utm_campaign": sa.Column("utm_campaign", sa.String(120), nullable=True),
            "utm_content": sa.Column("utm_content", sa.String(120), nullable=True),
        }
        for name, col in patches.items():
            if name not in cols:
                op.add_column("page_views", col)

    if "ip_geo_cache" not in inspector.get_table_names():
        op.create_table(
            "ip_geo_cache",
            sa.Column("ip_hash", sa.String(64), primary_key=True),
            sa.Column("country_code", sa.String(2), nullable=True),
            sa.Column("country", sa.String(80), nullable=True),
            sa.Column("region", sa.String(100), nullable=True),
            sa.Column("city", sa.String(100), nullable=True),
            sa.Column("postal_code", sa.String(20), nullable=True),
            sa.Column("latitude", sa.Float(), nullable=True),
            sa.Column("longitude", sa.Float(), nullable=True),
            sa.Column("looked_up_at", sa.DateTime(timezone=True), nullable=False),
        )


def downgrade() -> None:
    op.drop_table("ip_geo_cache")
    for col in (
        "utm_content", "utm_campaign", "utm_medium", "utm_source",
        "geo_longitude", "geo_latitude", "geo_postal_code", "geo_city",
        "geo_region", "geo_country", "geo_country_code",
    ):
        try:
            op.drop_column("page_views", col)
        except Exception:
            pass
