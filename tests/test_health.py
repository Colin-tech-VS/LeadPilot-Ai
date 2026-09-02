import json
from pathlib import Path

from flask import Flask

ROOT = Path(__file__).resolve().parents[1]


def test_health_liveness(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.get_json()["status"] == "ok"


def test_api_health_liveness(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.get_json() == {"status": "ok"}


def test_health_ready_database(client):
    r = client.get("/health/ready")
    assert r.status_code == 200
    body = r.get_json()
    assert body["status"] == "ok"
    assert body["database"] == "connected"


def test_main_module_is_importable():
    """Scalingo starts gunicorn main:app — the WSGI entrypoint must import."""
    import main

    assert isinstance(main.app, Flask)


def test_scalingo_pythonpath_is_set_where_pytest_and_gunicorn_start():
    """``pytest`` as a script does not put the repo root on sys.path — that
    was the ModuleNotFoundError: app that failed Deploy to Scalingo. gunicorn
    and alembic need the same root on Scalingo's cwd-less cron/web dynos."""
    pytest_ini = (ROOT / "pytest.ini").read_text(encoding="utf-8")
    assert "pythonpath" in pytest_ini.lower()
    procfile = (ROOT / "Procfile").read_text(encoding="utf-8")
    assert "PYTHONPATH=." in procfile
    assert "gunicorn main:app" in procfile
    workflow = (ROOT / ".github" / "workflows" / "deploy-scalingo.yml").read_text(
        encoding="utf-8"
    )
    assert "python -m pytest" in workflow
    assert "PYTHONPATH:" in workflow


def test_scalingo_app_json_routing():
    manifest = json.loads((ROOT / ".scalingo" / "app.json").read_text(encoding="utf-8"))
    assert manifest["port"] == 5000
    assert manifest["website"] == "https://pilotcore.fr"
    # Public HTTPS targets only — never docker-style hosts like backend:5000
    # (those resolve as RFC1918 / internes and the healthcheck refuses the redirect).
    for target in manifest["routes"].values():
        assert target.startswith("https://www.pilotcore.fr")
        assert "backend" not in target
        assert "127.0.0.1" not in target
        assert "localhost" not in target
    assert manifest["routes"]["/"] == "https://www.pilotcore.fr/"
    assert manifest["routes"]["/api/health"] == "https://www.pilotcore.fr/api/health"
    assert manifest["routes"]["/admin"] == "https://www.pilotcore.fr/admin"
    for key in ("SCALINGO_PROJECT", "DATABASE_URL", "SECRET_KEY"):
        assert key in manifest["env"]
        assert "value" in manifest["env"][key]
