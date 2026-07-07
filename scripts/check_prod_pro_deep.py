"""Deep smoke-check for PilotCore pro production."""
import json
import sys
import urllib.error
import urllib.request

BASE = "https://www.pilotcore.fr"
ISSUES = []


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def fetch(path, method="GET", data=None, follow=False):
    opener = (
        urllib.request.build_opener()
        if follow
        else urllib.request.build_opener(NoRedirect)
    )
    req = urllib.request.Request(BASE + path, method=method, data=data)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with opener.open(req, timeout=25) as resp:
            return resp.status, resp.headers.get("Location", ""), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Location", ""), e.read()


def ok(msg):
    print(f"  OK  {msg}")


def fail(msg):
    print(f"  FAIL {msg}")
    ISSUES.append(msg)


# Auth guards
for path in [
    "/dashboard",
    "/leads",
    "/appointments",
    "/quotes",
    "/marketing",
    "/billing",
    "/settings",
    "/chatbot",
    "/test-call",
]:
    code, loc, _ = fetch(path)
    if code in (302, 303) and "login" in loc:
        ok(f"{path} -> login ({code})")
    elif code in (302, 303) and path == "/test-call" and "chatbot" in loc:
        ok(f"{path} -> chatbot redirect")
    else:
        fail(f"{path}: {code} -> {loc}")

# Public pages
for path, needles in [
    ("/login", [b"auth-field__input", b"Connexion"]),
    ("/register", [b"auth-field__input", b"company_name"]),
    ("/pro", [b"PilotCore", b"Starter"]),
    ("/forgot-password", [b"email", b"forgot"]),
]:
    code, _, body = fetch(path, follow=True)
    if code != 200:
        fail(f"{path} status {code}")
        continue
    for n in needles:
        if n not in body:
            fail(f"{path} missing {n.decode()}")
    if b"internal server error" in body.lower():
        fail(f"{path} shows 500 page")
    ok(f"{path} content ({len(body)} bytes)")

# Assets cache-bust
_, _, login_html = fetch("/login", follow=True)
if b"auth-pro.css?v=2" not in login_html:
    fail("login missing auth-pro.css?v=2 cache bust")
else:
    ok("login loads auth-pro.css v2")

# Infra
code, _, body = fetch("/health")
if code != 200:
    fail(f"/health {code}")
else:
    ok("/health")

code, _, body = fetch("/health/ready")
try:
    data = json.loads(body.decode())
    if data.get("database") != "connected":
        fail(f"database not connected: {data}")
    else:
        ok("database connected")
except Exception as exc:
    fail(f"/health/ready parse error: {exc}")

code, _, _ = fetch("/billing/webhook", method="POST", data=b"{}")
if code == 500:
    fail("POST /billing/webhook returns 500")
elif code in (400, 401, 403, 405, 202):
    ok(f"POST /billing/webhook {code} (no crash)")
else:
    ok(f"POST /billing/webhook {code}")

code, _, _ = fetch("/sw.js", follow=True)
if code != 200:
    fail(f"/sw.js {code}")
else:
    ok("/sw.js")

print()
if ISSUES:
    print(f"{len(ISSUES)} issue(s):")
    for i in ISSUES:
        print(f"  - {i}")
    sys.exit(1)
print("All deep pro production checks passed.")
