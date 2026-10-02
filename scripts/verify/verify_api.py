"""One-shot API smoke test: hits the running server, prints results, exits."""

import json

import httpx

BASE = "http://127.0.0.1:8000"

with httpx.Client(base_url=BASE, timeout=5) as c:
    r = c.get("/health")
    print("HEALTH:", r.status_code, json.dumps(r.json(), ensure_ascii=False))

    r = c.post(
        "/api/v1/fare/estimate",
        json={
            "taxi_type": "URBAN",
            "distance_km": "10",
            "waiting_min": "5",
            "tunnels": ["cross_harbour"],
            "crosses_harbour": True,
            "discount_percent": "15",
        },
    )
    print("ESTIMATE:", r.status_code)
    print(json.dumps(r.json(), ensure_ascii=False, indent=1))

    r = c.post(
        "/api/v1/fare/estimate",
        json={"taxi_type": "URBAN", "distance_km": "5", "crosses_harbour": True},
    )
    print("BIZ-ERR:", r.status_code, json.dumps(r.json(), ensure_ascii=False))

    r = c.post("/api/v1/fare/estimate", json={"taxi_type": "URBAN", "distance_km": "-1"})
    print("VALIDATION:", r.status_code, r.json()["code"])

    r = c.get("/api/v1/definitely-not-a-route")
    print("404:", r.status_code, r.json()["code"])
