def test_api_register_rate_limit(client):
    for i in range(6):
        r = client.post(
            "/auth/register",
            json={"email": f"user{i}@example.com", "password": "password123"},
        )
    assert r.status_code == 429


def test_demo_simulate_rate_limit(client):
    payload = {"transcript": "Bonjour, fuite sous l'évier", "phone": "+33600000000"}
    for _ in range(16):
        r = client.post("/demo/simulate", json=payload)
    assert r.status_code == 429
