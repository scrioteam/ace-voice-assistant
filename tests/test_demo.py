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

        async def search(self, q="", category="", min_price=None, max_price=None, limit=12, page=1):
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


def test_product_search_can_report_live_source(monkeypatch):
    class FakeLiveCatalog:
        enabled = True

        async def search(self, q="", category="", min_price=None, max_price=None, limit=12, page=1):
            return [server.Product(sku="LIVE-ACE-2", title="מוצר חי", url="https://www.ace.co.il/LIVE-ACE-2")]

        async def get(self, sku):
            return None

    monkeypatch.setattr(server, "live_catalog", FakeLiveCatalog())
    response = client.get("/api/products/search", params={"q": "מוצר", "include_meta": "true", "live_only": "true"})
    assert response.status_code == 200
    data = response.json()
    assert data["source"] == "live"
    assert data["fallback_used"] is False
    assert data["live_only"] is True
    assert data["products"][0]["sku"] == "LIVE-ACE-2"


def test_live_only_search_does_not_fallback_to_demo_catalog():
    response = client.get("/api/products/search", params={"q": "ספה נפתחת", "live_only": "true", "include_meta": "true"})
    assert response.status_code == 200
    data = response.json()
    assert data["source"] == "live"
    assert data["fallback_used"] is False
    assert data["products"] == []


def test_product_details_can_report_fallback_source():
    response = client.get("/api/products/601125", params={"include_meta": "true"})
    assert response.status_code == 200
    data = response.json()
    assert data["source"] == "fallback"
    assert data["fallback_used"] is True
    assert data["product"]["sku"] == "601125"


def test_live_only_product_details_does_not_fallback_to_demo_catalog():
    response = client.get("/api/products/601125", params={"live_only": "true", "include_meta": "true"})
    assert response.status_code == 404


def test_realtime_product_tools_are_described_as_live_only():
    tools = {tool["name"]: tool for tool in server.TOOLS}
    assert "ישירות בקטלוג ACE החי" in tools["search_products"]["description"]
    assert "לא מוצרי fallback" in tools["search_products"]["description"]
    assert "page" in tools["search_products"]["description"]
    assert "page" in tools["search_products"]["parameters"]["properties"]
    assert "browse_products" in tools
    assert "סייטמאפ" in tools["browse_products"]["description"]
    assert "page" in tools["browse_products"]["parameters"]["properties"]
    assert "ישירות מדף מוצר חי" in tools["get_product_details"]["description"]
    assert "לא משתמש ב-fallback" in tools["get_product_details"]["description"]
    assert "SKU-ים שהתקבלו מתוצאות live" in tools["show_on_screen"]["description"]
    assert "לא ב-fallback" in tools["show_on_screen"]["description"]


def test_system_prompt_requires_live_catalog_products():
    assert "תוצאות live של כלי search_products או get_product_details בלבד" in server.SYSTEM_PROMPT
    assert "page גדול ב-1" in server.SYSTEM_PROMPT
    assert "fallback_used=true" in server.SYSTEM_PROMPT
    assert "source שאינו live" in server.SYSTEM_PROMPT
    assert "רק עם SKU-ים שחזרו מתוצאות live" in server.SYSTEM_PROMPT


def test_customer_realtime_product_calls_request_live_only_catalog():
    js = (server.STATIC_DIR / "customer.js").read_text(encoding="utf-8")
    assert 'params.set("include_meta", "true")' in js
    assert 'params.set("live_only", "true")' in js
    assert 'params.set("page", args.page)' in js
    assert "page: data.page" in js
    assert 'name === "browse_products"' in js
    assert 'fetch("/api/products/browse?" + params.toString())' in js
    assert '?include_meta=true&live_only=true' in js
    assert "fallback_used" in js


def test_paged_url_adds_or_replaces_page_param():
    assert server.paged_url("https://www.ace.co.il/catalogsearch/result/?q=x", 1).endswith("?q=x")
    assert server.paged_url("https://www.ace.co.il/catalogsearch/result/?q=x", 2).endswith("?q=x&p=2")
    assert server.paged_url("https://www.ace.co.il/furniture?p=4&product_list_order=price", 3).endswith(
        "?product_list_order=price&p=3"
    )


def test_product_search_forwards_page_to_live_catalog(monkeypatch):
    class FakeLiveCatalog:
        enabled = True

        async def search(self, q="", category="", min_price=None, max_price=None, limit=12, page=1):
            assert page == 2
            return [server.Product(sku="PAGE-2", title="מוצר עמוד שני", url="https://www.ace.co.il/PAGE-2")]

        async def get(self, sku):
            return None

    monkeypatch.setattr(server, "live_catalog", FakeLiveCatalog())
    response = client.get(
        "/api/products/search",
        params={"q": "ספה", "page": 2, "include_meta": "true", "live_only": "true"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["page"] == 2
    assert data["products"][0]["sku"] == "PAGE-2"


def test_product_browse_pages_live_sitemap_products(monkeypatch):
    class FakeLiveCatalog:
        async def browse_sitemap_products(self, page=1, limit=12, query=""):
            assert page == 3
            assert limit == 2
            assert query == "ספה"
            return [
                server.Product(sku="BROWSE-1", title="מוצר סייטמאפ", url="https://www.ace.co.il/BROWSE-1")
            ]

        async def load_sitemap_products(self):
            return [
                server.Product(sku="A", title="ספה אחת", url="https://www.ace.co.il/A"),
                server.Product(sku="B", title="ארון", url="https://www.ace.co.il/B"),
            ]

    monkeypatch.setattr(server, "live_catalog", FakeLiveCatalog())
    response = client.get(
        "/api/products/browse",
        params={"q": "ספה", "page": 3, "limit": 2, "include_meta": "true"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["source"] == "live_sitemap"
    assert data["fallback_used"] is False
    assert data["live_only"] is True
    assert data["page"] == 3
    assert data["limit"] == 2
    assert data["total"] == 1
    assert data["products"][0]["sku"] == "BROWSE-1"


def test_customer_panel_minimize_uses_visible_launcher():
    js = (server.STATIC_DIR / "customer.js").read_text(encoding="utf-8")
    assert 'assistantPanel.classList.toggle("is-hidden", value === "closed")' in js
    assert 'assistantLauncher.hidden = value !== "closed"' in js
    assert 'value === "minimized" ? "פתח" : "ACE"' in js


def test_customer_voice_transcript_rejects_foreign_script_before_hebrew():
    js = (server.STATIC_DIR / "customer.js").read_text(encoding="utf-8")
    css = (server.STATIC_DIR / "app.css").read_text(encoding="utf-8")
    assert "if (hasStrongForeignScript(value)) return false;" in js
    assert "if (containsHebrewText(value)) return true;" in js
    assert js.index("if (hasStrongForeignScript(value)) return false;") < js.index("if (containsHebrewText(value)) return true;")
    assert r"return /[\u0400-\u04ff\u0600-\u06ff]/.test(value);" in js
    assert 'node.lang = "he-IL"' in js
    assert 'node.dir = "rtl"' in js
    assert "unicode-bidi: plaintext;" in css


def test_catalog_status_reports_live_catalog(monkeypatch):
    class FakeLiveCatalog:
        async def status(self, refresh=False):
            return {
                "enabled": True,
                "category_count": 860,
                "sitemap_product_count": 33458,
                "sitemap_loaded": True,
                "cache": {
                    "mode": "memory_only",
                    "persisted": False,
                    "disk_path": None,
                    "source_url": server.ACE_SITEMAP_URL,
                },
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
    assert data["live_catalog"]["cache"]["mode"] == "memory_only"
    assert data["live_catalog"]["cache"]["persisted"] is False
    assert data["live_catalog"]["refresh"] is True


def test_live_catalog_cache_metadata_is_memory_only():
    live = server.AceLiveCatalog()
    live.sitemap_products = [
        server.Product(sku="4440328", title="פוף Matera", url="https://www.ace.co.il/4440328")
    ]
    live.sitemap_loaded_at = server.now()
    metadata = live.cache_metadata()
    assert metadata["mode"] == "memory_only"
    assert metadata["persisted"] is False
    assert metadata["disk_path"] is None
    assert metadata["source_url"] == server.ACE_SITEMAP_URL
    assert metadata["product_count"] == 1
    assert metadata["loaded"] is True


def test_catalog_readiness_reports_healthy_live_path(monkeypatch):
    class FakeLiveCatalog:
        enabled = True

        async def audit(self, sample_size=3, strategy="spread", refresh=False, verify_search=True, search_pages=3, **kwargs):
            return {
                "sitemap_product_count": 33393,
                "sample_size": sample_size,
                "sampled_indexes": [0, 16696, 33392][:sample_size],
                "resolved_count": sample_size,
                "failed_count": 0,
                "search_matched_count": sample_size,
                "search_failed_count": 0,
                "search_pages": search_pages,
            }

        async def search(self, q="", category="", min_price=None, max_price=None, limit=12, page=1):
            return [server.Product(sku="4440328", title="פוף Matera", url="https://www.ace.co.il/4440328")]

        async def get(self, sku):
            return None

        def cache_metadata(self):
            return {
                "mode": "memory_only",
                "persisted": False,
                "disk_path": None,
                "source_url": server.ACE_SITEMAP_URL,
            }

    monkeypatch.setattr(server, "live_catalog", FakeLiveCatalog())
    response = client.get("/api/catalog/readiness", params={"sample_size": 1, "query": "פוף Matera"})
    assert response.status_code == 200
    data = response.json()
    assert data["ready"] is True
    assert all(data["checks"].values())
    assert data["live_only_search"]["source"] == "live"
    assert data["live_only_search"]["fallback_used"] is False
    assert data["fallback"]["used_for_readiness"] is False
    assert data["live_cache"]["mode"] == "memory_only"
    assert data["live_cache"]["persisted"] is False
    assert data["audit"]["search_pages"] == 3


def test_catalog_readiness_fails_when_samples_are_not_searchable(monkeypatch):
    class FakeLiveCatalog:
        enabled = True

        async def audit(self, sample_size=3, strategy="spread", refresh=False, verify_search=True, search_pages=3, **kwargs):
            return {
                "sitemap_product_count": 33393,
                "sample_size": sample_size,
                "sampled_indexes": [0],
                "resolved_count": sample_size,
                "failed_count": 0,
                "search_matched_count": 0,
                "search_failed_count": sample_size,
                "search_pages": search_pages,
            }

        async def search(self, q="", category="", min_price=None, max_price=None, limit=12, page=1):
            return [server.Product(sku="4440328", title="פוף Matera", url="https://www.ace.co.il/4440328")]

        async def get(self, sku):
            return None

        def cache_metadata(self):
            return {
                "mode": "memory_only",
                "persisted": False,
                "disk_path": None,
                "source_url": server.ACE_SITEMAP_URL,
            }

    monkeypatch.setattr(server, "live_catalog", FakeLiveCatalog())
    response = client.get("/api/catalog/readiness", params={"sample_size": 1})
    assert response.status_code == 200
    data = response.json()
    assert data["ready"] is False
    assert data["checks"]["sampled_products_searchable"] is False


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


def test_live_catalog_alphanumeric_query_uses_product_details(monkeypatch):
    calls = []
    live = server.AceLiveCatalog()

    async def fake_get(sku):
        calls.append(sku)
        return server.Product(
            sku="4498960T",
            title="מוצר לפי מקט אלפאנומרי",
            url=f"https://www.ace.co.il/{sku}",
            price=42,
        )

    monkeypatch.setattr(live, "get", fake_get)
    products = asyncio.run(live.search("4498960t"))
    assert calls == ["4498960t"]
    assert products[0].sku == "4498960T"


def test_live_catalog_get_enriches_product_page_with_render_info(monkeypatch):
    live = server.AceLiveCatalog()

    async def fake_fetch_text(url):
        return '''
        <script>
        context.setProduct({"sku":"4497137","name":"כותרת מדף מוצר","canonicalUrl":"https://www.ace.co.il/4497137","mainImageUrl":"https://www.ace.co.il/media/page.jpg","pricing":{"regularPrice":999}})
        </script>
        '''

    async def fake_render_info(skus):
        assert skus == ["4497137"]
        return [
            server.Product(
                sku="4497137",
                title="כותרת render-info",
                url="https://www.ace.co.il/4497137",
                image_url="https://www.ace.co.il/media/render.jpg",
                price=100,
                regular_price=120,
                available=False,
                sales_notes=["פרטי מוצר מ-ACE products-render-info."],
            )
        ]

    monkeypatch.setattr(live, "fetch_text", fake_fetch_text)
    monkeypatch.setattr(live, "render_info_products", fake_render_info)
    product = asyncio.run(live.get("4497137"))
    assert product.title == "כותרת render-info"
    assert product.price == 100
    assert product.regular_price == 120
    assert product.available is False
    assert product.image_url.endswith("render.jpg")


def test_live_catalog_get_keeps_product_page_when_render_info_is_unavailable(monkeypatch):
    live = server.AceLiveCatalog()

    async def fake_fetch_text(url):
        return '''
        <script>
        context.setProduct({"sku":"4497137","name":"כותרת מדף מוצר","canonicalUrl":"https://www.ace.co.il/4497137","mainImageUrl":"https://www.ace.co.il/media/page.jpg","pricing":{"regularPrice":999}})
        </script>
        '''

    async def no_render_info(skus):
        return []

    monkeypatch.setattr(live, "fetch_text", fake_fetch_text)
    monkeypatch.setattr(live, "render_info_products", no_render_info)
    product = asyncio.run(live.get("4497137"))
    assert product.title == "כותרת מדף מוצר"
    assert product.price == 999
    assert product.image_url.endswith("page.jpg")


def test_sku_like_live_search_miss_falls_back_to_regular_search(monkeypatch):
    live = server.AceLiveCatalog()
    calls = []

    async def fake_get(sku):
        calls.append(("get", sku))
        return None

    async def no_urls(*args, **kwargs):
        return []

    async def page_products(urls, query, limit):
        calls.append(("products_from_urls", query))
        return [
            server.Product(
                sku="1111111",
                title="תוצאה מחיפוש רגיל",
                url="https://www.ace.co.il/1111111",
            )
        ]

    async def no_sitemap_products(*args, **kwargs):
        return []

    monkeypatch.setattr(live, "get", fake_get)
    monkeypatch.setattr(live, "category_result_urls", no_urls)
    monkeypatch.setattr(live, "autocomplete_result_urls", no_urls)
    monkeypatch.setattr(live, "products_from_urls", page_products)
    monkeypatch.setattr(live, "sitemap_products_for_query", no_sitemap_products)
    products = asyncio.run(live.search("abc123"))
    assert calls == [("get", "abc123"), ("products_from_urls", "abc123")]
    assert [product.sku for product in products] == ["1111111"]


def test_live_search_requests_requested_page_for_search_urls(monkeypatch):
    live = server.AceLiveCatalog()
    seen_urls = []

    async def category_urls(*args, **kwargs):
        return ["https://www.ace.co.il/furniture/living-room-furniture/living-room-sofas"]

    async def autocomplete_urls(*args, **kwargs):
        return ["https://www.ace.co.il/catalogsearch/result/?q=alternate&p=7"]

    async def page_products(urls, query, limit):
        seen_urls.extend(urls)
        return [server.Product(sku="2222222", title="ספה מעמוד שני", url="https://www.ace.co.il/2222222")]

    async def no_sitemap_products(*args, **kwargs):
        return []

    monkeypatch.setattr(live, "category_result_urls", category_urls)
    monkeypatch.setattr(live, "autocomplete_result_urls", autocomplete_urls)
    monkeypatch.setattr(live, "products_from_urls", page_products)
    monkeypatch.setattr(live, "sitemap_products_for_query", no_sitemap_products)
    products = asyncio.run(live.search("ספה", limit=3, page=2))
    assert products[0].sku == "2222222"
    assert seen_urls == [
        "https://www.ace.co.il/furniture/living-room-furniture/living-room-sofas?p=2",
        "https://www.ace.co.il/catalogsearch/result/?q=%D7%A1%D7%A4%D7%94&p=2",
        "https://www.ace.co.il/catalogsearch/result/?q=alternate&p=2",
    ]


def test_live_search_uses_sitemap_only_to_fill_page_gaps(monkeypatch):
    live = server.AceLiveCatalog()
    sitemap_called = False

    async def no_urls(*args, **kwargs):
        return []

    async def full_page_products(*args, **kwargs):
        return [
            server.Product(sku="1111111", title="ספה אחת", url="https://www.ace.co.il/1111111"),
            server.Product(sku="2222222", title="ספה שתיים", url="https://www.ace.co.il/2222222"),
        ]

    async def sitemap_products(*args, **kwargs):
        nonlocal sitemap_called
        sitemap_called = True
        return [server.Product(sku="3333333", title="ספה מסייטמאפ", url="https://www.ace.co.il/3333333")]

    monkeypatch.setattr(live, "category_result_urls", no_urls)
    monkeypatch.setattr(live, "autocomplete_result_urls", no_urls)
    monkeypatch.setattr(live, "products_from_urls", full_page_products)
    monkeypatch.setattr(live, "sitemap_products_for_query", sitemap_products)
    products = asyncio.run(live.search("ספה", limit=2, page=2))
    assert sitemap_called is False
    assert [product.sku for product in products] == ["1111111", "2222222"]


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

    async def passthrough_enrichment(products):
        return products

    monkeypatch.setattr(live, "autocomplete_result_urls", fake_autocomplete)
    monkeypatch.setattr(live, "fetch_text", fake_fetch_text)
    monkeypatch.setattr(live, "enrich_products_with_render_info", passthrough_enrichment)
    products = asyncio.run(live.search("בדיקה", limit=2))
    assert {product.sku for product in products} == {"1111111", "2222222"}


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

    async def passthrough_enrichment(products):
        return products

    monkeypatch.setattr(live, "autocomplete_result_urls", fake_autocomplete)
    monkeypatch.setattr(live, "fetch_text", fake_fetch_text)
    monkeypatch.setattr(live, "enrich_products_with_render_info", passthrough_enrichment)
    products = asyncio.run(live.search("כלי עבודה", limit=2))
    assert {product.sku for product in products} == {"1111111", "3333333"}


def test_product_from_render_info_maps_ace_structured_payload():
    product = server.product_from_render_info({
        "url": "https://www.ace.co.il/4497137",
        "name": "סלון פינתי מודולרי קומפקטי",
        "is_salable": "1",
        "currency_code": "ILS",
        "price_info": {"final_price": 1008.53, "regular_price": 1190},
        "images": [{"url": "https://www.ace.co.il/media/catalog/product/4/4/4497137_0.jpeg"}],
    })
    assert product.sku == "4497137"
    assert product.price == 1008.53
    assert product.regular_price == 1190
    assert product.available is True
    assert product.image_url.endswith("4497137_0.jpeg")
    assert "products-render-info" in " ".join(product.sales_notes)


def test_render_info_products_uses_ace_storefront_endpoint(monkeypatch):
    live = server.AceLiveCatalog()
    calls = []

    async def fake_fetch_json(url, params):
        calls.append((url, params))
        return {
            "items": [
                {
                    "sku": "4497137",
                    "url": "https://www.ace.co.il/4497137",
                    "name": "מוצר מ-render-info",
                    "price_info": {"final_price": 100},
                    "images": [],
                    "is_salable": "1",
                    "currency_code": "ILS",
                }
            ]
        }

    monkeypatch.setattr(live, "fetch_json", fake_fetch_json)
    products = asyncio.run(live.render_info_products(["4497137", "4497137"]))
    assert [product.sku for product in products] == ["4497137"]
    assert calls[0][0] == server.ACE_PRODUCTS_RENDER_INFO_URL
    assert calls[0][1]["searchCriteria[filterGroups][0][filters][0][field]"] == "sku"
    assert calls[0][1]["searchCriteria[filterGroups][0][filters][0][value]"] == "4497137"
    assert calls[0][1]["storeId"] == 5
    assert calls[0][1]["currencyCode"] == "ILS"


def test_products_from_urls_enriches_cards_with_render_info(monkeypatch):
    live = server.AceLiveCatalog()

    async def fake_fetch_text(url):
        return '''
        <li class="item product product-item">
          <a href="https://www.ace.co.il/4497137" class="product photo product-item-photo">
            <img class="product-image-photo" src="https://www.ace.co.il/media/old.jpg" />
          </a>
          <strong class="product name product-item-name">כותרת HTML</strong>
          <span class="priceNum">999</span>
        </li>
        '''

    async def fake_render_info(skus):
        assert skus == ["4497137"]
        return [
            server.Product(
                sku="4497137",
                title="כותרת render-info",
                url="https://www.ace.co.il/4497137",
                image_url="https://www.ace.co.il/media/new.jpg",
                price=100,
                regular_price=120,
                available=False,
                sales_notes=["פרטי מוצר מ-ACE products-render-info."],
            )
        ]

    monkeypatch.setattr(live, "fetch_text", fake_fetch_text)
    monkeypatch.setattr(live, "render_info_products", fake_render_info)
    products = asyncio.run(live.products_from_urls(["https://www.ace.co.il/catalogsearch/result/?q=x"], "x", 3))
    assert products[0].title == "כותרת render-info"
    assert products[0].price == 100
    assert products[0].regular_price == 120
    assert products[0].available is False
    assert products[0].image_url.endswith("new.jpg")


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


def test_parse_sitemap_products_excludes_numeric_category_urls():
    xml = '''
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>https://www.ace.co.il/perfumes-pharm/114030000</loc></url>
      <url>
        <loc>https://www.ace.co.il/4440328</loc>
        <image:image xmlns:image="http://www.google.com/schemas/sitemap-image/1.1">
          <image:loc>https://www.ace.co.il/media/catalog/product/4/4/4440328.jpg</image:loc>
          <image:title>פוף דגם Matera בגוון אפור בהיר</image:title>
        </image:image>
      </url>
    </urlset>
    '''
    products = server.parse_sitemap_products(xml)
    assert [product.sku for product in products] == ["4440328"]


def test_parse_sitemap_products_accepts_alphanumeric_product_skus():
    xml = '''
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url>
        <loc>https://www.ace.co.il/4498960t</loc>
        <image:image xmlns:image="http://www.google.com/schemas/sitemap-image/1.1">
          <image:loc>https://www.ace.co.il/media/catalog/product/4/4/4498960t.jpg</image:loc>
          <image:title>מוצר ACE עם מקט אלפאנומרי</image:title>
        </image:image>
      </url>
    </urlset>
    '''
    products = server.parse_sitemap_products(xml)
    assert [product.sku for product in products] == ["4498960t"]
    assert products[0].image_url.endswith("4498960t.jpg")


def test_parse_sitemap_products_accepts_alphanumeric_skus_without_image_metadata():
    xml = '''
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url>
        <loc>https://www.ace.co.il/abc1234</loc>
      </url>
    </urlset>
    '''
    products = server.parse_sitemap_products(xml)
    assert [product.sku for product in products] == ["abc1234"]
    assert products[0].title == "abc1234"
    assert products[0].image_url == ""


def test_parse_sitemap_products_keeps_numeric_no_image_urls_excluded():
    xml = '''
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url>
        <loc>https://www.ace.co.il/1234567</loc>
      </url>
    </urlset>
    '''
    assert server.parse_sitemap_products(xml) == []


def test_parse_sitemap_products_regex_fallback_accepts_alphanumeric_without_image_metadata():
    xml = '''
    <url>
      <loc>https://www.ace.co.il/abc1234</loc>
    </url>
    '''
    products = server.parse_sitemap_products(xml)
    assert [product.sku for product in products] == ["abc1234"]


def test_live_sitemap_load_validates_no_image_alphanumeric_candidates(monkeypatch):
    live = server.AceLiveCatalog()

    async def fake_fetch_text(url):
        if url == server.ACE_SITEMAP_URL:
            return '''
            <sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
              <sitemap><loc>https://www.ace.co.il/media/sitemap-test.xml</loc></sitemap>
            </sitemapindex>
            '''
        return '''
        <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
          <url><loc>https://www.ace.co.il/abc1234</loc></url>
          <url><loc>https://www.ace.co.il/furniture</loc></url>
        </urlset>
        '''

    async def fake_get(sku):
        if sku == "abc1234":
            return server.Product(
                sku="abc1234",
                title="מוצר חי תקין",
                url="https://www.ace.co.il/abc1234",
                price=99,
            )
        return server.Product(sku=sku, title=sku, url=f"https://www.ace.co.il/{sku}")

    monkeypatch.setattr(live, "fetch_text", fake_fetch_text)
    monkeypatch.setattr(live, "get", fake_get)
    products = asyncio.run(live.load_sitemap_products())
    assert [product.sku for product in products] == ["abc1234"]


def test_live_sitemap_load_prefers_image_backed_duplicate_over_no_image_candidate(monkeypatch):
    live = server.AceLiveCatalog()

    async def fake_fetch_text(url):
        if url == server.ACE_SITEMAP_URL:
            return '''
            <sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
              <sitemap><loc>https://www.ace.co.il/media/sitemap-test.xml</loc></sitemap>
            </sitemapindex>
            '''
        return '''
        <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
          <url><loc>https://www.ace.co.il/abc1234</loc></url>
          <url>
            <loc>https://www.ace.co.il/abc1234</loc>
            <image:image xmlns:image="http://www.google.com/schemas/sitemap-image/1.1">
              <image:loc>https://www.ace.co.il/media/catalog/product/a/b/abc1234.jpg</image:loc>
              <image:title>מוצר עם תמונה</image:title>
            </image:image>
          </url>
        </urlset>
        '''

    async def fake_get(sku):
        raise AssertionError("image-backed duplicate should not need no-image validation")

    monkeypatch.setattr(live, "fetch_text", fake_fetch_text)
    monkeypatch.setattr(live, "get", fake_get)
    products = asyncio.run(live.load_sitemap_products())
    assert [product.sku for product in products] == ["abc1234"]
    assert products[0].image_url.endswith("abc1234.jpg")


def test_no_image_sitemap_validation_has_short_timeout(monkeypatch):
    live = server.AceLiveCatalog()

    async def slow_get(sku):
        await asyncio.sleep(0.05)
        return server.Product(sku=sku, title="מוצר איטי", url=f"https://www.ace.co.il/{sku}", price=99)

    monkeypatch.setattr(server, "NO_IMAGE_SITEMAP_VALIDATION_SECONDS", 0.001)
    monkeypatch.setattr(live, "get", slow_get)
    products = [
        server.Product(sku="abc1234", title="abc1234", url="https://www.ace.co.il/abc1234"),
    ]
    assert asyncio.run(live.validate_no_image_sitemap_products(products)) == []


def test_parse_sitemap_index_accepts_namespaced_ace_urls():
    xml = '''
    <sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <sitemap><loc>https://www.ace.co.il/media/sitemap-5-1.xml</loc></sitemap>
      <sitemap><loc>https://www.ace.co.il/media/sitemap-5-2.xml</loc></sitemap>
    </sitemapindex>
    '''
    assert server.parse_sitemap_index(xml) == [
        "https://www.ace.co.il/media/sitemap-5-1.xml",
        "https://www.ace.co.il/media/sitemap-5-2.xml",
    ]


def test_catalog_audit_resolves_sitemap_products(monkeypatch):
    class FakeLiveCatalog:
        async def audit(self, sample_size=10, offset=0, refresh=False, strategy="slice", verify_search=False, search_pages=1):
            return {
                "enabled": True,
                "sitemap_product_count": 33393,
                "sample_size": sample_size,
                "offset": offset,
                "strategy": strategy,
                "sampled_indexes": [0],
                "resolved_count": sample_size,
                "failed_count": 0,
                "search_verified": verify_search,
                "search_pages": search_pages,
                "search_matched_count": sample_size if verify_search else 0,
                "search_failed_count": 0,
                "resolved_products": [{"sku": "5750243", "resolved": True, "product": {"sku": "5750243"}}],
                "failed_products": [],
                "search_results": [],
                "refresh": refresh,
            }

    monkeypatch.setattr(server, "live_catalog", FakeLiveCatalog())
    response = client.get(
        "/api/catalog/audit",
        params={
            "sample_size": 1,
            "offset": 4,
            "refresh": "true",
            "strategy": "spread",
            "verify_search": "true",
            "search_pages": 4,
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["sitemap_product_count"] == 33393
    assert data["resolved_count"] == 1
    assert data["failed_count"] == 0
    assert data["offset"] == 4
    assert data["strategy"] == "spread"
    assert data["search_verified"] is True
    assert data["search_pages"] == 4
    assert data["search_matched_count"] == 1
    assert data["refresh"] is True


def test_catalog_audit_can_verify_sample_is_searchable(monkeypatch):
    live = server.AceLiveCatalog()
    live.sitemap_products = [
        server.Product(sku="4440328", title="פוף Matera", url="https://www.ace.co.il/4440328", tags=["פוף", "matera"])
    ]
    live.sitemap_loaded_at = server.now()

    async def fake_get(sku):
        return server.Product(sku=sku, title="פוף Matera חי", url=f"https://www.ace.co.il/{sku}")

    async def fake_search(query, category="", min_price=None, max_price=None, limit=12, page=1):
        return [server.Product(sku="4440328", title="פוף Matera חי", url="https://www.ace.co.il/4440328")]

    monkeypatch.setattr(live, "get", fake_get)
    monkeypatch.setattr(live, "search", fake_search)
    result = asyncio.run(live.audit(sample_size=1, verify_search=True))
    assert result["resolved_count"] == 1
    assert result["search_verified"] is True
    assert result["search_matched_count"] == 1
    assert result["search_failed_count"] == 0
    assert result["search_results"][0]["search_query"] == "פוף Matera"
    assert result["search_results"][0]["search_result_skus"] == ["4440328"]
    assert result["search_results"][0]["search_matched_page"] == 1


def test_catalog_audit_can_verify_sample_on_later_search_page(monkeypatch):
    live = server.AceLiveCatalog()
    live.sitemap_products = [
        server.Product(sku="TARGET", title="מוצר מדף", url="https://www.ace.co.il/TARGET")
    ]
    live.sitemap_loaded_at = server.now()

    async def fake_get(sku):
        return server.Product(sku=sku, title="מוצר מדף חי", url=f"https://www.ace.co.il/{sku}")

    async def fake_search(query, category="", min_price=None, max_price=None, limit=12, page=1):
        if page == 1:
            return [server.Product(sku="OTHER", title="מוצר אחר", url="https://www.ace.co.il/OTHER")]
        return [server.Product(sku="TARGET", title="מוצר מדף חי", url="https://www.ace.co.il/TARGET")]

    monkeypatch.setattr(live, "get", fake_get)
    monkeypatch.setattr(live, "search", fake_search)
    result = asyncio.run(live.audit(sample_size=1, verify_search=True, search_pages=2))
    assert result["search_pages"] == 2
    assert result["search_matched_count"] == 1
    assert result["search_failed_count"] == 0
    assert result["search_results"][0]["search_matched_page"] == 2
    assert result["search_results"][0]["search_result_skus_by_page"] == {
        "1": ["OTHER"],
        "2": ["TARGET"],
    }


def test_catalog_audit_matches_alphanumeric_skus_case_insensitively(monkeypatch):
    live = server.AceLiveCatalog()
    live.sitemap_products = [
        server.Product(sku="4498960t", title="מזוודת SwissBrand", url="https://www.ace.co.il/4498960t")
    ]
    live.sitemap_loaded_at = server.now()

    async def fake_get(sku):
        return server.Product(sku="4498960T", title="מזוודת SwissBrand", url="https://www.ace.co.il/4498960T")

    async def fake_search(query, category="", min_price=None, max_price=None, limit=12, page=1):
        return [server.Product(sku="4498960T", title="מזוודת SwissBrand", url="https://www.ace.co.il/4498960T")]

    monkeypatch.setattr(live, "get", fake_get)
    monkeypatch.setattr(live, "search", fake_search)
    result = asyncio.run(live.audit(sample_size=1, verify_search=True))
    assert result["resolved_count"] == 1
    assert result["search_matched_count"] == 1
    assert result["search_failed_count"] == 0


def test_catalog_audit_indexes_can_spread_across_catalog():
    assert server.catalog_audit_indexes(10, 4, strategy="spread") == [0, 3, 6, 9]
    assert server.catalog_audit_indexes(10, 4, offset=8, strategy="slice") == [8, 9, 0, 1]


def test_concurrent_sitemap_loads_share_one_fetch(monkeypatch):
    live = server.AceLiveCatalog()
    calls = []

    async def fake_fetch_text(url):
        calls.append(url)
        await asyncio.sleep(0)
        if url == server.ACE_SITEMAP_URL:
            return '''
            <sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
              <sitemap><loc>https://www.ace.co.il/media/sitemap-5-1.xml</loc></sitemap>
            </sitemapindex>
            '''
        return '''
        <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
          <url>
            <loc>https://www.ace.co.il/4440328</loc>
            <image:image xmlns:image="http://www.google.com/schemas/sitemap-image/1.1">
              <image:loc>https://www.ace.co.il/media/catalog/product/4/4/4440328.jpg</image:loc>
              <image:title>פוף דגם Matera בגוון אפור בהיר</image:title>
            </image:image>
          </url>
        </urlset>
        '''

    async def run_two_loads():
        first, second = await asyncio.gather(live.load_sitemap_products(), live.load_sitemap_products())
        return first, second

    monkeypatch.setattr(live, "fetch_text", fake_fetch_text)
    first, second = asyncio.run(run_two_loads())
    assert [product.sku for product in first] == ["4440328"]
    assert second is first
    assert calls == [server.ACE_SITEMAP_URL, "https://www.ace.co.il/media/sitemap-5-1.xml"]


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


def test_live_search_deduplicates_skus_case_insensitively(monkeypatch):
    live = server.AceLiveCatalog()
    live.sitemap_products = [
        server.Product(sku="4498960t", title="מזוודת SwissBrand", url="https://www.ace.co.il/4498960t")
    ]
    live.sitemap_loaded_at = server.now()

    async def no_urls(*args, **kwargs):
        return []

    async def duplicate_products(*args, **kwargs):
        return [server.Product(sku="4498960T", title="מזוודת SwissBrand", url="https://www.ace.co.il/4498960T")]

    async def fake_get(sku):
        return server.Product(sku="4498960T", title="מזוודת SwissBrand חיה", url=f"https://www.ace.co.il/{sku}")

    monkeypatch.setattr(live, "category_result_urls", no_urls)
    monkeypatch.setattr(live, "autocomplete_result_urls", no_urls)
    monkeypatch.setattr(live, "products_from_urls", duplicate_products)
    monkeypatch.setattr(live, "get", fake_get)
    products = asyncio.run(live.search("SwissBrand", limit=3))
    assert [server.sku_key(product.sku) for product in products] == ["4498960t"]


def test_sitemap_exact_match_can_outrank_page_results(monkeypatch):
    live = server.AceLiveCatalog()
    live.sitemap_products = [
        server.Product(sku="4440328", title="פוף Matera", url="https://www.ace.co.il/4440328", tags=["פוף", "matera"])
    ]
    live.sitemap_loaded_at = server.now()

    async def no_urls(*args, **kwargs):
        return []

    async def broad_page_products(*args, **kwargs):
        return [server.Product(sku="1111111", title="מוצר כללי", url="https://www.ace.co.il/1111111", price=10)]

    async def fake_get(sku):
        return server.Product(sku=sku, title="פוף Matera חי", url=f"https://www.ace.co.il/{sku}", price=129)

    monkeypatch.setattr(live, "category_result_urls", no_urls)
    monkeypatch.setattr(live, "autocomplete_result_urls", no_urls)
    monkeypatch.setattr(live, "products_from_urls", broad_page_products)
    monkeypatch.setattr(live, "get", fake_get)
    products = asyncio.run(live.search("פוף Matera", limit=2))
    assert [product.sku for product in products] == ["4440328", "1111111"]


def test_sitemap_products_for_query_slices_by_page(monkeypatch):
    live = server.AceLiveCatalog()
    live.sitemap_products = [
        server.Product(sku=f"SKU-{index}", title=f"ספה דגם {index}", url=f"https://www.ace.co.il/SKU-{index}", tags=["ספה"])
        for index in range(1, 7)
    ]
    live.sitemap_loaded_at = server.now()

    async def passthrough(products):
        return products

    monkeypatch.setattr(live, "enrich_sitemap_products", passthrough)
    products = asyncio.run(live.sitemap_products_for_query("ספה", limit=2, page=2))
    assert [product.sku for product in products] == ["SKU-3", "SKU-4"]


def test_browse_sitemap_products_pages_complete_index(monkeypatch):
    live = server.AceLiveCatalog()
    live.sitemap_products = [
        server.Product(sku=f"SKU-{index}", title=f"מוצר {index}", url=f"https://www.ace.co.il/SKU-{index}")
        for index in range(1, 6)
    ]
    live.sitemap_loaded_at = server.now()

    async def passthrough(products):
        return products

    monkeypatch.setattr(live, "enrich_sitemap_products", passthrough)
    products = asyncio.run(live.browse_sitemap_products(page=2, limit=2))
    assert [product.sku for product in products] == ["SKU-3", "SKU-4"]


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
    assert data["source"] == "fallback"
    assert data["fallback_used"] is True
    assert data["live_only"] is False


def test_show_can_use_live_only_product_sources(monkeypatch):
    class FakeLiveCatalog:
        enabled = True

        async def search(self, q="", category="", min_price=None, max_price=None, limit=12, page=1):
            return []

        async def get(self, sku):
            return server.Product(sku=sku, title="מוצר חי למסך", url=f"https://www.ace.co.il/{sku}")

    monkeypatch.setattr(server, "live_catalog", FakeLiveCatalog())
    response = client.post(
        "/api/show",
        json={"session_id": "session-live-screen", "product_ids": ["4440328"], "live_only": True},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["source"] == "live"
    assert data["sources"] == ["live"]
    assert data["fallback_used"] is False
    assert data["live_only"] is True
    assert data["products"][0]["sku"] == "4440328"


def test_show_live_only_does_not_fallback_to_demo_products():
    response = client.post(
        "/api/show",
        json={"session_id": "session-live-screen", "product_ids": ["601125"], "live_only": True},
    )
    assert response.status_code == 404


def test_customer_realtime_show_on_screen_requests_live_only():
    js = (server.STATIC_DIR / "customer.js").read_text(encoding="utf-8")
    assert "live_only: true" in js


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


def test_demo_chat_more_options_advances_live_catalog_page(monkeypatch):
    calls = []

    async def fake_search_catalog(q="", category="", min_price=None, max_price=None, limit=12, page=1):
        calls.append({"q": q, "max_price": max_price, "limit": limit, "page": page})
        return [
            server.Product(
                sku=f"PAGE-{page}",
                title=f"ספה מעמוד {page}",
                url=f"https://www.ace.co.il/PAGE-{page}",
                price=1000 + page,
                department="furniture",
            )
        ]

    monkeypatch.setattr(server, "search_catalog", fake_search_catalog)
    first = client.post("/api/demo/chat", json={"session_id": "chat-more", "text": "אני מחפש ספה לסלון"})
    assert first.status_code == 200
    second = client.post("/api/demo/chat", json={"session_id": "chat-more", "text": "עד 3000 שקל, נפתחת למיטה"})
    assert second.status_code == 200
    assert second.json()["page"] == 1

    more = client.post("/api/demo/chat", json={"session_id": "chat-more", "text": "תראה לי עוד אפשרויות"})
    assert more.status_code == 200
    data = more.json()
    assert data["page"] == 2
    assert data["products"][0]["sku"] == "PAGE-2"
    assert "עמוד 2" in data["message"]
    assert calls == [
        {"q": "ספה נפתחת מיטה", "max_price": 3000.0, "limit": 3, "page": 1},
        {"q": "ספה נפתחת מיטה", "max_price": 3000.0, "limit": 3, "page": 2},
    ]


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
