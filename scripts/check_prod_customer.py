"""Deep smoke-check for PilotCore customer (particulier) production."""
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://www.pilotcore.fr"
ISSUES = []


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def fetch(path, method="GET", data=None, follow=False, headers=None):
    opener = (
        urllib.request.build_opener()
        if follow
        else urllib.request.build_opener(NoRedirect)
    )
    req = urllib.request.Request(BASE + path, method=method, data=data)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
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


# --- Public client pages ---
for path, needles in [
    ("/", [b"PilotCore", b"artisans"]),
    ("/artisans", [b"artisans", b"directory"]),
    ("/client/login", [b"auth-field__input", b"Connexion", b"client"]),
    ("/client/register", [b"auth-field__input", b"Pr", b"client"]),
]:
    code, _, body = fetch(path, follow=True)
    if code != 200:
        fail(f"{path} status {code}")
        continue
    low = body.lower()
    if b"internal server error" in low:
        fail(f"{path} shows 500")
    for n in needles:
        if n.lower() not in low:
            fail(f"{path} missing {n.decode(errors='ignore')}")
    ok(f"{path} ({len(body)} bytes)")

# CSS versions
_, _, login = fetch("/client/login", follow=True)
if b"auth-pro.css?v=2" not in login:
    fail("client login missing auth-pro.css v2")
else:
    ok("client login auth-pro.css v2")

if b"auth-customer.css" not in login:
    fail("client login missing auth-customer.css")
else:
    ok("client login auth-customer.css")

_, _, home = fetch("/", follow=True)
if b"client-doctolib.css" not in home:
    fail("home missing client-doctolib.css")
else:
    ok("home client-doctolib.css")

# --- Auth guards ---
for path in ["/client/account", "/client/book/complete"]:
    code, loc, _ = fetch(path)
    if code in (302, 303) and "login" in loc:
        ok(f"{path} -> login")
    else:
        fail(f"{path}: {code} -> {loc}")

# --- Public API ---
code, _, body = fetch("/api/public/artisans/search?q=plombier")
if code != 200:
    fail(f"artisan search API {code}")
else:
    try:
        data = json.loads(body.decode())
        ok(f"search API ({len(data.get('artisans', data) if isinstance(data, dict) else data)} results)")
    except Exception as exc:
        fail(f"search API JSON: {exc}")

# Artisan profile — pick first from search
slug = None
try:
    payload = json.loads(body.decode())
    items = payload.get("artisans") or payload.get("results") or []
    if isinstance(payload, list):
        items = payload
    if items:
        slug = items[0].get("slug") or items[0].get("public_slug")
except Exception:
    pass

if slug:
    code, _, prof = fetch(f"/artisans/{slug}", follow=True)
    if code != 200:
        fail(f"/artisans/{slug} status {code}")
    elif b"internal server error" in prof.lower():
        fail(f"profile {slug} shows 500")
    else:
        ok(f"/artisans/{slug} profile ({len(prof)} bytes)")

    code, _, slots = fetch(f"/api/public/artisans/{slug}/slots")
    if code != 200:
        fail(f"slots API {code}")
    else:
        ok(f"slots API for {slug}")
else:
    ok("no public artisans in search (skip profile test)")

# --- Legal / footer links from client zone ---
for path in ["/mentions-legales", "/confidentialite", "/cookies"]:
    code, _, _ = fetch(path, follow=True)
    if code == 200:
        ok(path)
    else:
        fail(f"{path} {code}")

# --- Public chat route pattern (404 ok without valid tenant) ---
code, _, _ = fetch("/chat/00000000-0000-0000-0000-000000000000")
if code in (404, 302):
    ok(f"invalid chat tenant returns {code} (no 500)")
elif code == 500:
    fail("chat route 500")
else:
    ok(f"chat probe {code}")

print()
if ISSUES:
    print(f"{len(ISSUES)} issue(s):")
    for i in ISSUES:
        print(f"  - {i}")
    sys.exit(1)
print("All customer (particulier) production checks passed.")
