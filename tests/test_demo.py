from fastapi.testclient import TestClient

import server


client = TestClient(server.app)


def setup_function():
    server.screens.reset()
    server.session_memory.clear()


def test_sofa_search_uses_fallback_catalog():
    response = client.get("/api/products/search", params={"q": "ספה נפתחת", "max_price": 3000})
    assert response.status_code == 200
    products = response.json()
    assert products
    assert products[0]["sku"] == "601125"
    assert products[0]["available"] is True
    assert products[0]["image"].startswith("/api/products/601125/image")


def test_show_assigns_only_available_plumbing_screen_by_default():
    response = client.post(
        "/api/show",
        json={
            "session_id": "session-a",
            "product_ids": ["601125", "4493620"],
            "headline": "בדיקת דמו",
            "department": "furniture",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["screen"]["id"] == "plumbing"
    assert data["screen"]["status"] == "leased"
    assert data["screen"]["location_label"] == "ליד מחלקת האינסטלציה"


def test_screen_availability_and_reset_flow():
    busy = client.post("/api/screens/plumbing/availability", json={"available": False})
    assert busy.status_code == 200
    response = client.post("/api/show", json={"session_id": "session-b", "product_ids": ["601125"]})
    assert response.status_code == 409

    reset = client.post("/api/demo/reset")
    assert reset.status_code == 200
    response = client.post("/api/show", json={"session_id": "session-b", "product_ids": ["601125"]})
    assert response.status_code == 200
    assert response.json()["screen"]["id"] == "plumbing"


def test_idle_showcase_uses_cached_products():
    response = client.get("/api/idle-showcase", params={"department": "plumbing", "limit": 4})
    assert response.status_code == 200
    data = response.json()
    assert data["products"]
    assert data["website_path"] == "/"
    assert data["products"][0]["image"].startswith("/api/products/")


def test_demo_chat_qualifies_then_recommends_and_displays():
    first = client.post("/api/demo/chat", json={"session_id": "chat-a", "text": "אני מחפש ספה לסלון"})
    assert first.status_code == 200
    assert "תקציב" in first.json()["message"]
    assert first.json()["stage"] == "qualified"

    second = client.post(
        "/api/demo/chat",
        json={"session_id": "chat-a", "text": "עד 3000 שקל, עדיף נפתחת למיטה"},
    )
    assert second.status_code == 200
    data = second.json()
    assert data["products"]
    assert data["screen"]["id"] == "plumbing"
    assert "האינסטלציה" in data["message"]
    assert "חלופה זולה" not in data["message"]
    assert "יקר" not in data["message"]

    comparison = client.post(
        "/api/demo/chat",
        json={"session_id": "chat-a", "text": "תשווה לי את האפשרויות"},
    )
    assert comparison.status_code == 200
    assert "התאמה" in comparison.json()["message"]
    assert "יקר" not in comparison.json()["message"]


def test_pages_load():
    assert client.get("/").status_code == 200
    assert client.get("/demo").status_code == 200
    assert client.get("/screen/plumbing").status_code == 200


def test_healthz_for_deployment():
    response = client.get("/healthz")
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["products"] >= 1
    assert data["screens"] == 5
