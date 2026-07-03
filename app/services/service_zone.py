from app.models.tenant import Tenant
from app.utils.geocoding import geocode_address
from app.utils.geo import haversine_km

DEFAULT_SERVICE_RADIUS_KM = 30


def check_service_zone(lead: dict, tenant: Tenant) -> dict:
    """
    Check whether a lead's address falls within the tenant's service area.
    Returns a dict with in_zone, status, distance_km, service_radius_km, reason.
    """
    radius = tenant.service_radius_km or DEFAULT_SERVICE_RADIUS_KM
    service_label = tenant.city or tenant.name or "votre zone"

    if tenant.latitude is None or tenant.longitude is None:
        return {
            "in_zone": True,
            "status": "no_tenant_location",
            "service_radius_km": radius,
            "service_area_label": service_label,
            "reason": "Tenant location not configured — zone check skipped.",
        }

    address = (lead.get("address") or "").strip()
    if not address:
        return {
            "in_zone": True,
            "status": "no_lead_address",
            "service_radius_km": radius,
            "service_area_label": service_label,
            "reason": "Lead address not yet provided.",
        }

    lead_lat = lead.get("latitude")
    lead_lng = lead.get("longitude")
    if lead_lat is None or lead_lng is None:
        coords = geocode_address(address)
        if coords:
            lead_lat, lead_lng = coords
            lead["latitude"] = lead_lat
            lead["longitude"] = lead_lng
        else:
            return {
                "in_zone": False,
                "status": "address_unverified",
                "service_radius_km": radius,
                "service_area_label": service_label,
                "reason": (
                    f"Cannot verify address location for service area ({service_label}, {radius} km)."
                ),
            }

    distance = haversine_km(tenant.latitude, tenant.longitude, lead_lat, lead_lng)
    distance = round(distance, 1)

    if distance > radius:
        return {
            "in_zone": False,
            "status": "out_of_zone",
            "distance_km": distance,
            "service_radius_km": radius,
            "service_area_label": service_label,
            "reason": (
                f"Address is {distance} km away — outside the {radius} km service area "
                f"around {service_label}."
            ),
        }

    return {
        "in_zone": True,
        "status": "in_zone",
        "distance_km": distance,
        "service_radius_km": radius,
        "service_area_label": service_label,
        "reason": f"Address is {distance} km from base — within service area.",
    }
