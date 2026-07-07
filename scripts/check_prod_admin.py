"""Deep smoke-check for PilotCore admin production."""
import json
import sys
import urllib.error
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


def expect_login_redirect(path):
    code, loc, body = fetch(path)
    if code in (302, 303) and "/admin/login" in loc:
        ok(f"{path} -> admin login")
    elif code == 200 and b"admin/login" in body.lower():
        ok(f"{path} -> login page (inline)")
    elif code == 500:
        fail(f"{path} returns 500")
    else:
        fail(f"{path}: {code} -> {loc}")


# Public admin pages
code, _, body = fetch("/admin/login", follow=True)
if code != 200:
    fail(f"/admin/login status {code}")
elif b"password" not in body.lower() or b"username" not in body.lower():
    fail("/admin/login missing login form")
elif b"internal server error" in body.lower():
    fail("/admin/login shows 500")
else:
    ok(f"/admin/login ({len(body)} bytes)")

code, _, _ = fetch("/admin/manifest.webmanifest", follow=True)
if code != 200:
    fail(f"/admin/manifest.webmanifest {code}")
else:
    ok("/admin/manifest.webmanifest")

# Protected routes -> login
for path in [
    "/admin",
    "/admin/",
    "/admin/traffic",
    "/admin/gsc",
    "/admin/clients",
    "/admin/emails",
    "/admin/twilio",
    "/admin/diagnostics",
    "/admin/database",
    "/admin/logs",
    "/admin/studio",
    "/admin/offers",
    "/admin/pages",
    "/admin/social",
    "/admin/api/analytics",
    "/admin/api/traffic",
    "/admin/api/logs",
]:
    expect_login_redirect(path)

# GSC OAuth connect should also require admin
expect_login_redirect("/admin/gsc/connect")

# Logout without session -> login (302)
code, loc, _ = fetch("/admin/logout")
if code in (302, 303) and "login" in loc:
    ok("/admin/logout -> login")
else:
    fail(f"/admin/logout: {code} -> {loc}")

# Inbound email webhook — must not 500 without auth
code, _, body = fetch("/admin/email/inbound", method="POST", data=b"from=a@b.com")
if code == 500:
    fail("POST /admin/email/inbound returns 500")
elif code in (401, 403, 503):
    ok(f"POST /admin/email/inbound guarded ({code})")
else:
    ok(f"POST /admin/email/inbound {code}")

# Admin static assets
for path in ["/static/admin/admin.css", "/static/admin.webmanifest"]:
    code, _, _ = fetch(path, follow=True)
    if code == 200:
        ok(path)
    else:
        fail(f"{path} {code}")

# Robots should disallow admin
code, _, body = fetch("/robots.txt", follow=True)
if code == 200 and b"/admin" in body:
    ok("robots.txt disallows /admin")
else:
    fail("robots.txt missing admin disallow")

print()
if ISSUES:
    print(f"{len(ISSUES)} issue(s):")
    for i in ISSUES:
        print(f"  - {i}")
    sys.exit(1)
print("All admin production checks passed.")
