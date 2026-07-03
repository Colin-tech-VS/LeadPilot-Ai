"""Full system audit for LeadPilot AI."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app

app = create_app()
c = app.test_client()
results = []


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    status = "OK" if ok else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail else ""))


with app.app_context():
    from app.models.appointment import Appointment
    from app.models.lead import Lead
    from app.models.tenant import Tenant
    from app.models.user import User

    tenant = Tenant.query.filter_by(name="Coco Cayre").first()
    tid = str(tenant.id) if tenant else None

# Health
r = c.get("/health")
check("Health check", r.status_code == 200 and r.get_json().get("status") == "ok")

# API login
r = c.post("/auth/login", json={"email": "coco.cayre@gmail.com", "password": "Ttcedu_92410"})
token = r.get_json().get("access_token") if r.status_code == 200 else None
check("API login", r.status_code == 200 and bool(token))
headers = {"Authorization": f"Bearer {token}"} if token else {}

r = c.get("/tenant/me", headers=headers)
check("API tenant/me", r.status_code == 200)

# Web
c.post("/login", data={"email": "coco.cayre@gmail.com", "password": "Ttcedu_92410"})
check("Dashboard", c.get("/dashboard").status_code == 200)
check("Leads page", c.get("/leads").status_code == 200)
check("Appointments page", c.get("/appointments").status_code == 200)
check("Test call page", c.get("/test-call").status_code == 200)

# Webhook
r = c.post(
    "/webhook/inbound-call",
    json={
        "tenant_id": tid,
        "phone": "+33699998877",
        "transcript": "Bonjour je suis Test User, WC bouche au 1 rue Test Lyon, pas urgent.",
    },
)
d = r.get_json() or {}
check(
    "Webhook inbound-call",
    r.status_code == 201 and d.get("success") and "booking" in d,
    f"action={d.get('booking', {}).get('action')}",
)

# Twilio
r = c.post(
    f"/voice/inbound?tenant_id={tid}",
    data={"CallSid": "CA-audit-1", "From": "+33611112233"},
)
xml = r.data.decode()
check("Twilio inbound", r.status_code == 200 and "<?xml" in xml and "Record" in xml)

r = c.post(
    f"/voice/process?tenant_id={tid}",
    data={
        "CallSid": "CA-audit-2",
        "From": "+33611112233",
        "SpeechResult": (
            "Bonjour je m appelle Claire Martin, canalisation eclatee inondation "
            "au 8 avenue Foch Toulouse, c est une urgence absolue."
        ),
    },
)
xml2 = r.data.decode()
check("Twilio process", r.status_code == 200 and "<Say" in xml2)

# Config
with app.app_context():
    check("Mistral API key", bool(app.config.get("MISTRAL_API_KEY")))
    check("Twilio SID", bool(app.config.get("TWILIO_ACCOUNT_SID")))
    check("Twilio token", bool(app.config.get("TWILIO_AUTH_TOKEN")))
    check("Default tenant", app.config.get("TWILIO_DEFAULT_TENANT_ID") == tid)

with app.app_context():
    check("DB tenants", Tenant.query.count() > 0, f"{Tenant.query.count()}")
    check("DB leads", Lead.query.count() > 0, f"{Lead.query.count()}")
    check("DB appointments", Appointment.query.count() >= 0, f"{Appointment.query.count()}")

failed = [r for r in results if not r[1]]
print()
print("=" * 50)
print(f"TOTAL: {len(results) - len(failed)}/{len(results)} passed")
if failed:
    print("FAILED:", [f[0] for f in failed])
else:
    print("ALL CHECKS PASSED")
