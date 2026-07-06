import re
import unicodedata

from app.core.extensions import db
from app.models.tenant import Tenant


def slugify(value: str) -> str:
    if not value:
        return ""
    value = unicodedata.normalize("NFKD", value)
    value = value.encode("ascii", "ignore").decode("ascii")
    value = re.sub(r"[^\w\s-]", "", value).strip().lower()
    return re.sub(r"[-\s]+", "-", value)[:80].strip("-")


def unique_public_slug(base: str, tenant_id=None) -> str:
    """Return a slug unique among tenants.public_slug."""
    slug = slugify(base) or "artisan"
    candidate = slug
    n = 2
    while _slug_taken(candidate, tenant_id):
        candidate = f"{slug}-{n}"
        n += 1
    return candidate


def _slug_taken(slug: str, tenant_id=None) -> bool:
    q = Tenant.query.filter(Tenant.public_slug == slug)
    if tenant_id:
        q = q.filter(Tenant.id != tenant_id)
    return db.session.query(q.exists()).scalar()
