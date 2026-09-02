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


def test_scalingo_app_json_routing():
    manifest = json.loads((ROOT / ".scalingo" / "app.json").read_text(encoding="utf-8"))
    assert manifest["port"] == 5000
    assert manifest["routes"]["/"] == "backend:5000"
    assert manifest["routes"]["/api/health"] == "backend:5000/api/health"
    assert manifest["routes"]["/admin"] == "backend:5000/admin"
    for key in ("SCALINGO_PROJECT", "DATABASE_URL", "SECRET_KEY"):
        assert key in manifest["env"]
        assert "value" in manifest["env"][key]
