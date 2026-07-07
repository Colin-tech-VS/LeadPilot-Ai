"""Cached IP → geo lookups (shared across workers, rate-limit friendly)."""
from datetime import datetime, timezone

from app.core.extensions import db


def utcnow():
    return datetime.now(timezone.utc)


class IpGeoCache(db.Model):
    __tablename__ = "ip_geo_cache"

    ip_hash = db.Column(db.String(64), primary_key=True)
    country_code = db.Column(db.String(2), nullable=True, index=True)
    country = db.Column(db.String(80), nullable=True)
    region = db.Column(db.String(100), nullable=True)
    city = db.Column(db.String(100), nullable=True, index=True)
    postal_code = db.Column(db.String(20), nullable=True)
    latitude = db.Column(db.Float, nullable=True)
    longitude = db.Column(db.Float, nullable=True)
    looked_up_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)

    def as_dict(self):
        return {
            "country_code": self.country_code,
            "country": self.country,
            "region": self.region,
            "city": self.city,
            "postal_code": self.postal_code,
            "latitude": self.latitude,
            "longitude": self.longitude,
        }
