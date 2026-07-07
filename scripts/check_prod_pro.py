"""Smoke-check pro (artisan) routes on production."""
import sys
import urllib.error
import urllib.request

BASE = "https://www.pilotcore.fr"

PUBLIC = [
    ("/health", 200),
    ("/health/ready", 200),
    ("/pro", 200),
    ("/login", 200),
    ("/register", 200),
    ("/forgot-password", 200),
    ("/mentions-legales", 200),
    ("/cgu", 200),
]

PROTECTED = [
    "/dashboard",
    "/leads",
    "/appointments",
    "/quotes",
    "/marketing",
    "/billing",
    "/settings",
    "/test-call",
]

ASSETS = [
    "/static/css/main.css",
    "/static/css/auth-pro.css",
    "/static/js/dashboard.js",
]


def fetch(path, method="GET"):
    req = urllib.request.Request(f"{BASE}{path}", method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return resp.status, resp.geturl()
    except urllib.error.HTTPError as e:
        return e.code, e.geturl()
    except Exception as e:
        return None, str(e)


def main():
    failed = []
    print(f"Checking {BASE}\n")

    for path, expected in PUBLIC:
        code, info = fetch(path)
        ok = code == expected
        mark = "OK" if ok else "FAIL"
        print(f"  [{mark}] {code} {path} (expected {expected})")
        if not ok:
            failed.append((path, code, expected))

    for path in PROTECTED:
        code, info = fetch(path)
        ok = code in (302, 303) and "login" in (info or "").lower()
        mark = "OK" if ok else "WARN" if code in (302, 303) else "FAIL"
        print(f"  [{mark}] {code} {path} -> {info}")
        if mark == "FAIL":
            failed.append((path, code, "redirect to login"))

    for path in ASSETS:
        code, _ = fetch(path)
        ok = code == 200
        mark = "OK" if ok else "FAIL"
        print(f"  [{mark}] {code} {path}")
        if not ok:
            failed.append((path, code, 200))

    # Webhook should not 500 on GET (405 is fine)
    code, _ = fetch("/billing/webhook")
    ok = code in (405, 200, 302)
    print(f"  [{'OK' if ok else 'FAIL'}] {code} /billing/webhook (GET probe)")
    if not ok:
        failed.append(("/billing/webhook", code, "not 500"))

    print()
    if failed:
        print(f"FAILED: {len(failed)} issue(s)")
        for f in failed:
            print(f"  - {f}")
        sys.exit(1)
    print("All pro production smoke checks passed.")


if __name__ == "__main__":
    main()
