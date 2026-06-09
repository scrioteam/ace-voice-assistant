import asyncio

from fastapi.testclient import TestClient

import server


client = TestClient(server.app)


def setup_function():
    server.screens.reset()
    server.session_memory.clear()
    server.live_catalog.enabled = False


def test_sofa_search_uses_fallback_catalog():
    response = client.get("/api/products/search", params={"q": "ספה נפתחת", "max_price": 3000})
    assert response.status_code == 200
    products = response.json()
    assert products
    assert products[0]["sku"] == "601125"
    assert products[0]["available"] is True
    assert products[0]["image"].startswith("/api/products/601125/image")


def test_product_search_prefers_live_catalog(monkeypatch):
    class FakeLiveCatalog:
        enabled = True

        async def search(self, q="", category="", min_price=None, max_price=None, limit=12):
            return [
                server.Product(
                    sku="LIVE-ACE-1",
                    title="מוצר חי מאתר ACE",
                    url="https://www.ace.co.il/LIVE-ACE-1",
                    image_url="https://www.ace.co.il/media/live.jpg",
                    price=99,
                    available=True,
                )
            ]

        async def get(self, sku):
            return None

    monkeypatch.setattr(server, "live_catalog", FakeLiveCatalog())
    response = client.get("/api/products/search", params={"q": "מקדחה"})
    assert response.status_code == 200
    products = response.json()
    assert products[0]["sku"] == "LIVE-ACE-1"
    assert products[0]["url"] == "https://www.ace.co.il/LIVE-ACE-1"


def test_catalog_status_reports_live_catalog(monkeypatch):
    class FakeLiveCatalog:
        async def status(self, refresh=False):
            return {
                "enabled": True,
                "category_count": 860,
                "sitemap_product_count": 33458,
                "sitemap_loaded": True,
                "sample_products": [],
                "refresh": refresh,
            }

    monkeypatch.setattr(server, "live_catalog", FakeLiveCatalog())
    response = client.get("/api/catalog/status", params={"refresh": "true"})
    assert response.status_code == 200
    data = response.json()
    assert data["local_fallback_products"] >= 1
    assert data["live_catalog"]["category_count"] == 860
    assert data["live_catalog"]["sitemap_product_count"] == 33458
    assert data["live_catalog"]["refresh"] is True


def test_live_catalog_numeric_query_uses_product_details(monkeypatch):
    calls = []
    live = server.AceLiveCatalog()

    async def fake_get(sku):
        calls.append(sku)
        return server.Product(
            sku=sku,
            title="מוצר לפי מקט",
            url=f"https://www.ace.co.il/{sku}",
            price=42,
        )

    monkeypatch.setattr(live, "get", fake_get)
    products = asyncio.run(live.search("4498444"))
    assert calls == ["4498444"]
    assert products[0].sku == "4498444"


def test_autocomplete_urls_are_used_to_extend_live_results(monkeypatch):
    live = server.AceLiveCatalog()

    async def fake_autocomplete(query):
        return ["https://www.ace.co.il/catalogsearch/result/?q=alternate"]

    async def fake_fetch_text(url):
        sku = "1111111" if "alternate" not in url else "2222222"
        title = "מוצר ראשון" if sku == "1111111" else "מוצר נוסף"
        return f'''
        <ol class="products list items product-items">
          <li class="item product product-item">
            <a href="https://www.ace.co.il/{sku}" class="product photo product-item-photo">
              <img class="product-image-photo" src="https://www.ace.co.il/media/{sku}.jpg" />
            </a>
            <strong class="product name product-item-name">{title}</strong>
            <span class="priceNum">99</span>
          </li>
        </ol>
        '''

    monkeypatch.setattr(live, "autocomplete_result_urls", fake_autocomplete)
    monkeypatch.setattr(live, "fetch_text", fake_fetch_text)
    products = asyncio.run(live.search("בדיקה", limit=2))
    assert [product.sku for product in products] == ["1111111", "2222222"]


def test_parse_category_links_from_ace_menu_html():
    html = '''
    <a href="https://www.ace.co.il/tools-paint-affixing/work-tools" class="item-link">
      <span class="item-title">כלי עבודה</span>
    </a>
    <a href="https://www.ace.co.il/tools-paint-affixing/work-tools" class="item-link">
      <span class="item-title">כלי עבודה</span>
    </a>
    '''
    links = server.parse_category_links(html)
    assert links == [{"title": "כלי עבודה", "url": "https://www.ace.co.il/tools-paint-affixing/work-tools"}]


def test_category_urls_are_used_to_extend_live_results(monkeypatch):
    live = server.AceLiveCatalog()
    live.category_index = [{"title": "כלי עבודה", "url": "https://www.ace.co.il/tools-paint-affixing/work-tools"}]
    live.category_index_loaded_at = server.now()

    async def fake_autocomplete(query):
        return []

    async def fake_fetch_text(url):
        sku = "3333333" if "work-tools" in url else "1111111"
        title = "מברגה מקטגוריה" if sku == "3333333" else "מוצר מחיפוש"
        return f'''
        <li class="item product product-item">
          <a href="https://www.ace.co.il/{sku}" class="product photo product-item-photo">
            <img class="product-image-photo" src="https://www.ace.co.il/media/{sku}.jpg" />
          </a>
          <strong class="product name product-item-name">{title}</strong>
          <span class="priceNum">99</span>
        </li>
        '''

    monkeypatch.setattr(live, "autocomplete_result_urls", fake_autocomplete)
    monkeypatch.setattr(live, "fetch_text", fake_fetch_text)
    products = asyncio.run(live.search("כלי עבודה", limit=2))
    assert [product.sku for product in products] == ["1111111", "3333333"]


def test_parse_sitemap_products_from_live_shape():
    xml = '''
    <url>
      <loc>https://www.ace.co.il/4440328</loc>
      <image:image>
        <image:loc>https://www.ace.co.il/media/catalog/product/4/4/4440328.jpg</image:loc>
        <image:title>פוף דגם Matera בגוון אפור בהיר</image:title>
      </image:image>
    </url>
    '''
    products = server.parse_sitemap_products(xml)
    assert products[0].sku == "4440328"
    assert products[0].title == "פוף דגם Matera בגוון אפור בהיר"
    assert products[0].image_url.endswith("4440328.jpg")


def test_sitemap_products_extend_live_search(monkeypatch):
    live = server.AceLiveCatalog()
    live.sitemap_products = [
        server.Product(
            sku="4440328",
            title="פוף דגם Matera בגוון אפור בהיר",
            url="https://www.ace.co.il/4440328",
            image_url="https://www.ace.co.il/media/4440328.jpg",
            tags=["פוף", "matera"],
        )
    ]
    live.sitemap_loaded_at = server.now()

    async def no_urls(*args, **kwargs):
        return []

    async def no_products(*args, **kwargs):
        return []

    async def fake_get(sku):
        return server.Product(
            sku=sku,
            title="פוף דגם Matera חי",
            url=f"https://www.ace.co.il/{sku}",
            price=129,
            regular_price=199,
        )

    monkeypatch.setattr(live, "category_result_urls", no_urls)
    monkeypatch.setattr(live, "autocomplete_result_urls", no_urls)
    monkeypatch.setattr(live, "products_from_urls", no_products)
    monkeypatch.setattr(live, "get", fake_get)
    products = asyncio.run(live.search("פוף Matera", limit=3))
    assert [product.sku for product in products] == ["4440328"]
    assert products[0].title == "פוף דגם Matera חי"
    assert products[0].price == 129


def test_live_search_deduplicates_products_from_multiple_sources(monkeypatch):
    live = server.AceLiveCatalog()
    live.sitemap_products = [
        server.Product(sku="4440328", title="פוף Matera", url="https://www.ace.co.il/4440328", tags=["פוף", "matera"])
    ]
    live.sitemap_loaded_at = server.now()

    async def no_urls(*args, **kwargs):
        return []

    async def duplicate_products(*args, **kwargs):
        return [server.Product(sku="4440328", title="פוף Matera", url="https://www.ace.co.il/4440328")]

    async def fake_get(sku):
        return server.Product(sku=sku, title="פוף Matera חי", url=f"https://www.ace.co.il/{sku}")

    monkeypatch.setattr(live, "category_result_urls", no_urls)
    monkeypatch.setattr(live, "autocomplete_result_urls", no_urls)
    monkeypatch.setattr(live, "products_from_urls", duplicate_products)
    monkeypatch.setattr(live, "get", fake_get)
    products = asyncio.run(live.search("פוף Matera", limit=3))
    assert [product.sku for product in products] == ["4440328"]


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
