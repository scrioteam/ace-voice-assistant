import asyncio
import html
import json
import os
import re
import socket
import time
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"
CATALOG_FILE = DATA_DIR / "ace_products.json"
FALLBACK_FILE = DATA_DIR / "fallback_products.json"
IMAGE_CACHE_DIR = DATA_DIR / "image_cache"

ACE_ORIGIN = "https://www.ace.co.il"
ACE_AUTOCOMPLETE_URL = f"{ACE_ORIGIN}/mageworx_searchsuiteautocomplete/ajax/index/"
ACE_SITEMAP_URL = f"{ACE_ORIGIN}/sitemap.xml"
REALTIME_MODEL = os.getenv("OPENAI_REALTIME_MODEL", "gpt-realtime-2")
TRANSCRIPTION_MODEL = os.getenv("OPENAI_TRANSCRIPTION_MODEL", "gpt-4o-mini-transcribe")
OPENAI_CLIENT_SECRETS_URL = "https://api.openai.com/v1/realtime/client_secrets"
OPENAI_TRANSCRIPTIONS_URL = "https://api.openai.com/v1/audio/transcriptions"
LEASE_SECONDS = 10 * 60
ACE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36 AceVoiceAssistantDemo/1.0",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "he-IL,he;q=0.9,en;q=0.8",
}


def now() -> float:
    return time.time()


def load_dotenv() -> None:
    env_path = BASE_DIR / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def read_template(name: str) -> str:
    return (TEMPLATES_DIR / name).read_text(encoding="utf-8")


def detect_lan_ip() -> Optional[str]:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return str(sock.getsockname()[0])
    except OSError:
        return None


def public_base_url(request: Request) -> str:
    configured = os.getenv("DEMO_PUBLIC_BASE_URL", "").strip().rstrip("/")
    if configured:
        return configured
    host = request.url.hostname or "127.0.0.1"
    if host in {"127.0.0.1", "localhost", "0.0.0.0", "::1"}:
        host = detect_lan_ip() or host
    port = f":{request.url.port}" if request.url.port else ""
    return f"{request.url.scheme}://{host}{port}"


def strip_html(value: str) -> str:
    value = html.unescape(value or "")
    value = re.sub(r"<script\b[^<]*(?:(?!</script>)<[^<]*)*</script>", " ", value, flags=re.I)
    value = re.sub(r"<style\b[^<]*(?:(?!</style>)<[^<]*)*</style>", " ", value, flags=re.I)
    value = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def normalize_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).casefold().strip()


def parse_price(value: Any) -> Optional[float]:
    if value is None:
        return None
    cleaned = re.sub(r"[^\d.]", "", str(value).replace(",", ""))
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def absolute_ace_url(value: str) -> str:
    url = html.unescape(str(value or "").strip())
    if not url:
        return ""
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("/"):
        return ACE_ORIGIN + url
    return url


def product_image_path(sku: str) -> Path:
    return IMAGE_CACHE_DIR / f"{sku}.img"


@dataclass
class Product:
    sku: str
    title: str
    url: str
    image_url: str = ""
    category: str = ""
    department: str = "general"
    brand: str = ""
    price: Optional[float] = None
    regular_price: Optional[float] = None
    currency: str = "ILS"
    available: bool = True
    promo: str = ""
    tags: List[str] = field(default_factory=list)
    specs: Dict[str, str] = field(default_factory=dict)
    sales_notes: List[str] = field(default_factory=list)
    last_seen: Optional[float] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Product":
        return cls(
            sku=str(data.get("sku") or data.get("id") or "").strip(),
            title=str(data.get("title") or data.get("name") or "").strip(),
            url=str(data.get("url") or "").strip(),
            image_url=str(data.get("image_url") or data.get("image") or "").strip(),
            category=str(data.get("category") or "").strip(),
            department=str(data.get("department") or infer_department(data)).strip(),
            brand=str(data.get("brand") or "").strip(),
            price=parse_price(data.get("price")),
            regular_price=parse_price(data.get("regular_price")),
            currency=str(data.get("currency") or "ILS"),
            available=bool(data.get("available", True)),
            promo=str(data.get("promo") or "").strip(),
            tags=[str(v) for v in data.get("tags", []) if str(v).strip()],
            specs={str(k): str(v) for k, v in (data.get("specs") or {}).items()},
            sales_notes=[str(v) for v in data.get("sales_notes", []) if str(v).strip()],
            last_seen=parse_price(data.get("last_seen")),
        )

    def public(self) -> Dict[str, Any]:
        item = asdict(self)
        item["image"] = f"/api/products/{urllib.parse.quote(self.sku)}/image"
        item["discount_percent"] = discount_percent(self.price, self.regular_price)
        item["availability_label"] = "זמין אונליין" if self.available else "לא זמין אונליין"
        return item

    def search_blob(self) -> str:
        parts: List[str] = [
            self.sku,
            self.title,
            self.category,
            self.department,
            self.brand,
            self.promo,
            " ".join(self.tags),
            " ".join(self.specs.values()),
        ]
        return normalize_text(" ".join(parts))


def discount_percent(price: Optional[float], regular: Optional[float]) -> Optional[int]:
    if not price or not regular or regular <= price:
        return None
    return round((regular - price) / regular * 100)


def infer_department(data: Dict[str, Any]) -> str:
    text = normalize_text(" ".join(str(data.get(k, "")) for k in ("title", "category", "url")))
    if any(v in text for v in ["ספה", "כורס", "ריהוט", "furniture", "sofa", "living-room"]):
        return "furniture"
    if any(v in text for v in ["חשמל", "תאורה", "appliance", "electric"]):
        return "electricity"
    if any(v in text for v in ["אינסטל", "ברז", "אמבט", "plumb", "bath"]):
        return "plumbing"
    if any(v in text for v in ["גינה", "garden", "outdoor"]):
        return "garden"
    return "general"


class Catalog:
    def __init__(self) -> None:
        self.products: List[Product] = []
        self.loaded_from = "none"
        self.loaded_at = 0.0
        self.load()

    def load(self) -> None:
        source = CATALOG_FILE if CATALOG_FILE.exists() else FALLBACK_FILE
        with source.open(encoding="utf-8") as f:
            raw = json.load(f)
        self.products = [Product.from_dict(item) for item in raw if item.get("sku") and item.get("title")]
        self.loaded_from = str(source)
        self.loaded_at = now()

    def save(self) -> None:
        DATA_DIR.mkdir(exist_ok=True)
        CATALOG_FILE.write_text(
            json.dumps([asdict(p) for p in self.products], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self.loaded_from = str(CATALOG_FILE)
        self.loaded_at = now()

    def get(self, sku: str) -> Product:
        sku = str(sku)
        for product in self.products:
            if product.sku == sku:
                return product
        raise HTTPException(status_code=404, detail="Product not found")

    def search(
        self,
        q: str = "",
        category: str = "",
        min_price: Optional[float] = None,
        max_price: Optional[float] = None,
        limit: int = 12,
    ) -> List[Product]:
        query = expand_query(q)
        category_filter = normalize_text(category)
        scored: List[tuple[int, Product]] = []
        for product in self.products:
            if min_price is not None and product.price is not None and product.price < min_price:
                continue
            if max_price is not None and product.price is not None and product.price > max_price:
                continue
            blob = product.search_blob()
            if category_filter and category_filter not in blob:
                continue
            score = score_product(product, query)
            if query and score <= 0:
                continue
            scored.append((score, product))
        scored.sort(key=lambda item: (-item[0], item[1].price if item[1].price is not None else 10**9))
        return [product for _, product in scored[: max(1, min(limit, 30))]]

    async def refresh_sofas(self) -> Dict[str, Any]:
        products = await scrape_category(f"{ACE_ORIGIN}/furniture/living-room-furniture/living-room-sofas")
        if products:
            existing = {p.sku: p for p in self.products}
            for product in products:
                existing[product.sku] = product
            self.products = list(existing.values())
            self.save()
        return {"added_or_updated": len(products), "total": len(self.products), "source": self.loaded_from}


class AceLiveCatalog:
    """Live access to ACE catalog pages.

    ACE currently renders product grids server-side on Magento search/category
    pages. We query those public pages on demand and parse the same product
    cards customers see, keeping the local JSON only as an outage fallback.
    """

    def __init__(self) -> None:
        self.enabled = os.getenv("ACE_LIVE_CATALOG_DISABLED", "").lower() not in {"1", "true", "yes"}
        self.last_error = ""
        self.category_index: List[Dict[str, str]] = []
        self.category_index_loaded_at = 0.0
        self.sitemap_products: List[Product] = []
        self.sitemap_loaded_at = 0.0
        self.sitemap_load_lock: Optional[asyncio.Lock] = None
        self.sitemap_load_lock_loop: Optional[asyncio.AbstractEventLoop] = None

    async def search(
        self,
        q: str = "",
        category: str = "",
        min_price: Optional[float] = None,
        max_price: Optional[float] = None,
        limit: int = 12,
    ) -> List[Product]:
        if not self.enabled:
            return []
        query = str(q or category or "").strip()
        if not query:
            return []
        try:
            if re.fullmatch(r"\d{5,10}", query):
                product = await self.get(query)
                return [product] if product else []
            urls = [f"{ACE_ORIGIN}/catalogsearch/result/?q={urllib.parse.quote(query)}"]
            urls.extend(await self.category_result_urls(query, category))
            urls.extend(await self.autocomplete_result_urls(query))
            products = await self.products_from_urls(urls, query, max(1, min(limit, 30)))
            products.extend(await self.sitemap_products_for_query(query, max(1, min(limit, 30))))
            filtered = filter_products_by_price(dedupe_products(products), min_price, max_price)
            return rank_products_for_query(filtered, query)[: max(1, min(limit, 30))]
        except (httpx.HTTPError, ValueError) as exc:
            self.last_error = str(exc)
            return []

    async def get(self, sku: str) -> Optional[Product]:
        if not self.enabled:
            return None
        clean_sku = str(sku or "").strip()
        if not clean_sku:
            return None
        try:
            html_text = await self.fetch_text(f"{ACE_ORIGIN}/{urllib.parse.quote(clean_sku)}")
            return parse_product_page(html_text, clean_sku)
        except (httpx.HTTPError, ValueError) as exc:
            self.last_error = str(exc)
            return None

    async def fetch_text(self, url: str) -> str:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True, headers=ACE_HEADERS) as client:
            response = await client.get(url)
        response.raise_for_status()
        if "text/html" not in response.headers.get("content-type", "") and not response.text:
            raise ValueError("ACE returned an empty catalog response")
        return response.text

    async def fetch_json(self, url: str, params: Dict[str, Any]) -> Dict[str, Any]:
        headers = {**ACE_HEADERS, "Accept": "application/json, text/javascript, */*; q=0.01", "X-Requested-With": "XMLHttpRequest"}
        async with httpx.AsyncClient(timeout=12, follow_redirects=True, headers=headers) as client:
            response = await client.get(url, params=params)
        response.raise_for_status()
        return response.json()

    async def autocomplete_result_urls(self, query: str) -> List[str]:
        try:
            payload = await self.fetch_json(ACE_AUTOCOMPLETE_URL, {"q": query})
        except (httpx.HTTPError, json.JSONDecodeError, ValueError):
            return []
        urls: List[str] = []
        for group in payload.get("result", []):
            if group.get("code") != "suggest":
                continue
            for item in group.get("data", [])[:5]:
                url = absolute_ace_url(str(item.get("url") or ""))
                if url and url not in urls:
                    urls.append(url)
        return urls

    async def category_result_urls(self, query: str, category: str = "") -> List[str]:
        terms = [term for term in expand_query(f"{query} {category}") if len(term) >= 2]
        if not terms:
            return []
        categories = await self.load_category_index()
        ranked: List[tuple[int, str]] = []
        for item in categories:
            title = normalize_text(item["title"])
            url = item["url"]
            score = 0
            for term in terms:
                if term in title:
                    score += 3
                if term in normalize_text(url):
                    score += 1
            if score:
                ranked.append((score, url))
        ranked.sort(key=lambda value: -value[0])
        urls: List[str] = []
        for _, url in ranked[:6]:
            if url not in urls:
                urls.append(url)
        return urls

    async def load_category_index(self) -> List[Dict[str, str]]:
        if self.category_index and now() - self.category_index_loaded_at < 60 * 60:
            return self.category_index
        try:
            html_text = await self.fetch_text(ACE_ORIGIN + "/")
            self.category_index = parse_category_links(html_text)
            self.category_index_loaded_at = now()
        except (httpx.HTTPError, ValueError):
            if not self.category_index:
                self.category_index = []
        return self.category_index

    async def sitemap_products_for_query(self, query: str, limit: int) -> List[Product]:
        terms = expand_query(query)
        if not terms:
            return []
        products = await self.load_sitemap_products()
        scored: List[tuple[int, Product]] = []
        for product in products:
            score = score_product(product, terms)
            if score > 0:
                scored.append((score, product))
        scored.sort(key=lambda item: (-item[0], item[1].title))
        candidates = [product for _, product in scored[: max(1, min(limit, 30))]]
        return await self.enrich_sitemap_products(candidates)

    async def enrich_sitemap_products(self, products: List[Product]) -> List[Product]:
        if not products:
            return []

        async def enrich(product: Product) -> Product:
            live_product = await self.get(product.sku)
            return live_product or product

        return await asyncio.gather(*(enrich(product) for product in products))

    def current_sitemap_load_lock(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        if self.sitemap_load_lock is None or self.sitemap_load_lock_loop is not loop:
            self.sitemap_load_lock = asyncio.Lock()
            self.sitemap_load_lock_loop = loop
        return self.sitemap_load_lock

    async def load_sitemap_products(self) -> List[Product]:
        if self.sitemap_products and now() - self.sitemap_loaded_at < 6 * 60 * 60:
            return self.sitemap_products
        async with self.current_sitemap_load_lock():
            if self.sitemap_products and now() - self.sitemap_loaded_at < 6 * 60 * 60:
                return self.sitemap_products
            try:
                index_text = await self.fetch_text(ACE_SITEMAP_URL)
                sitemap_urls = parse_sitemap_index(index_text)
                products: List[Product] = []
                seen: set[str] = set()
                for sitemap_url in sitemap_urls:
                    sitemap_text = await self.fetch_text(sitemap_url)
                    for product in parse_sitemap_products(sitemap_text):
                        if product.sku in seen:
                            continue
                        seen.add(product.sku)
                        products.append(product)
                if products:
                    self.sitemap_products = products
                    self.sitemap_loaded_at = now()
            except (httpx.HTTPError, ValueError):
                if not self.sitemap_products:
                    self.sitemap_products = []
        return self.sitemap_products

    async def status(self, refresh: bool = False) -> Dict[str, Any]:
        if refresh:
            self.sitemap_loaded_at = 0.0
            self.category_index_loaded_at = 0.0
        categories = await self.load_category_index()
        products = await self.load_sitemap_products() if refresh or self.sitemap_products else self.sitemap_products
        sample = products[:5]
        return {
            "enabled": self.enabled,
            "last_error": self.last_error,
            "sources": {
                "search_pages": f"{ACE_ORIGIN}/catalogsearch/result/?q=...",
                "autocomplete": ACE_AUTOCOMPLETE_URL,
                "categories": ACE_ORIGIN + "/",
                "sitemap": ACE_SITEMAP_URL,
                "product_pages": ACE_ORIGIN + "/{sku}",
            },
            "category_count": len(categories),
            "sitemap_product_count": len(products),
            "sitemap_loaded": bool(products),
            "sitemap_age_seconds": int(now() - self.sitemap_loaded_at) if self.sitemap_loaded_at else None,
            "sample_products": [product.public() for product in sample],
        }

    async def audit(
        self,
        sample_size: int = 10,
        offset: int = 0,
        refresh: bool = False,
        strategy: str = "slice",
        verify_search: bool = False,
    ) -> Dict[str, Any]:
        if refresh:
            self.sitemap_loaded_at = 0.0
        products = await self.load_sitemap_products()
        if not products:
            return {
                "enabled": self.enabled,
                "sitemap_product_count": 0,
                "sample_size": 0,
                "offset": 0,
                "strategy": normalize_audit_strategy(strategy),
                "sampled_indexes": [],
                "resolved_count": 0,
                "failed_count": 0,
                "search_verified": bool(verify_search),
                "search_matched_count": 0,
                "search_failed_count": 0,
                "resolved_products": [],
                "failed_products": [],
                "search_results": [],
            }
        size = max(1, min(sample_size, 25))
        start = max(0, min(offset, len(products) - 1))
        strategy_value = normalize_audit_strategy(strategy)
        indexes = catalog_audit_indexes(len(products), size, start, strategy_value)
        sample = [products[index] for index in indexes]

        async def resolve(product: Product) -> Dict[str, Any]:
            live_product = await self.get(product.sku)
            result = {
                "sku": product.sku,
                "sitemap_title": product.title,
                "resolved": bool(live_product),
                "product": live_product.public() if live_product else None,
            }
            if verify_search:
                query = product.title
                search_hits = await self.search(query, limit=5) if query else []
                result["search_query"] = query
                result["search_matched"] = any(hit.sku == product.sku for hit in search_hits)
                result["search_result_skus"] = [hit.sku for hit in search_hits]
            return result

        checked = await asyncio.gather(*(resolve(product) for product in sample))
        resolved = [item for item in checked if item["resolved"]]
        failed = [item for item in checked if not item["resolved"]]
        search_results = [item for item in checked if "search_matched" in item]
        search_failed = [item for item in search_results if not item["search_matched"]]
        return {
            "enabled": self.enabled,
            "sitemap_product_count": len(products),
            "sample_size": len(sample),
            "offset": start,
            "strategy": strategy_value,
            "sampled_indexes": indexes,
            "resolved_count": len(resolved),
            "failed_count": len(failed),
            "search_verified": bool(verify_search),
            "search_matched_count": len(search_results) - len(search_failed),
            "search_failed_count": len(search_failed),
            "resolved_products": resolved,
            "failed_products": failed,
            "search_results": search_results,
        }

    async def products_from_urls(self, urls: List[str], query: str, limit: int) -> List[Product]:
        products: List[Product] = []
        seen: set[str] = set()
        for url in urls:
            if len(products) >= limit:
                break
            html_text = await self.fetch_text(url)
            for product in parse_product_cards(html_text, category=query):
                if product.sku in seen:
                    continue
                seen.add(product.sku)
                products.append(product)
                if len(products) >= limit:
                    break
        return products


def filter_products_by_price(
    products: List[Product],
    min_price: Optional[float] = None,
    max_price: Optional[float] = None,
) -> List[Product]:
    filtered = []
    for product in products:
        if min_price is not None and product.price is not None and product.price < min_price:
            continue
        if max_price is not None and product.price is not None and product.price > max_price:
            continue
        filtered.append(product)
    return filtered


def dedupe_products(products: List[Product]) -> List[Product]:
    deduped: List[Product] = []
    seen: set[str] = set()
    for product in products:
        key = product.sku or product.url
        if key in seen:
            continue
        seen.add(key)
        deduped.append(product)
    return deduped


def rank_products_for_query(products: List[Product], query: str) -> List[Product]:
    terms = expand_query(query)
    if not terms:
        return products
    return [
        product for _, product in sorted(
            ((score_product(product, terms), product) for product in products),
            key=lambda item: (-item[0], item[1].price if item[1].price is not None else 10**9, item[1].title),
        )
    ]


def normalize_audit_strategy(strategy: str) -> str:
    return "spread" if normalize_text(strategy) == "spread" else "slice"


def catalog_audit_indexes(product_count: int, sample_size: int, offset: int = 0, strategy: str = "slice") -> List[int]:
    if product_count <= 0:
        return []
    size = max(1, min(sample_size, product_count))
    start = max(0, min(offset, product_count - 1))
    if normalize_audit_strategy(strategy) != "spread" or size == 1:
        return [(start + index) % product_count for index in range(size)]
    if size >= product_count:
        return list(range(product_count))
    usable = product_count - 1
    return sorted({round(index * usable / (size - 1)) for index in range(size)})


def parse_category_links(html_text: str) -> List[Dict[str, str]]:
    links: List[Dict[str, str]] = []
    seen: set[str] = set()
    pattern = r'<a href="(https://www\.ace\.co\.il/[^"]+)" class="item-link"[^>]*>.*?<span class="item-title">(.*?)</span>'
    for match in re.finditer(pattern, html_text or "", flags=re.I | re.S):
        url = absolute_ace_url(match.group(1)).split("#", 1)[0]
        title = strip_html(match.group(2))
        if not title or url in seen:
            continue
        if any(part in url for part in ["/customer/", "/checkout/", "/login/"]):
            continue
        seen.add(url)
        links.append({"title": title, "url": url})
    return links


def parse_sitemap_index(xml_text: str) -> List[str]:
    urls: List[str] = []
    try:
        root = ET.fromstring(xml_text or "")
        for loc in root.findall(".//{*}sitemap/{*}loc"):
            url = absolute_ace_url(loc.text or "")
            if url.startswith(f"{ACE_ORIGIN}/") and url.endswith(".xml") and url not in urls:
                urls.append(url)
    except ET.ParseError:
        for url in re.findall(r"<loc>(https://www\.ace\.co\.il/[^<]+\.xml)</loc>", xml_text or "", flags=re.I):
            resolved = absolute_ace_url(url)
            if resolved not in urls:
                urls.append(resolved)
    return urls


def parse_sitemap_products(xml_text: str) -> List[Product]:
    products: List[Product] = []
    try:
        root = ET.fromstring(xml_text or "")
        for node in root.findall(".//{*}url"):
            loc = node.find("{*}loc")
            product = sitemap_node_to_product(loc.text if loc is not None else "", node)
            if product:
                products.append(product)
    except ET.ParseError:
        for block in re.findall(r"<url>(.*?)</url>", xml_text or "", flags=re.I | re.S):
            loc_match = re.search(r"<loc>(https://www\.ace\.co\.il/(\d{5,10}))</loc>", block, flags=re.I)
            if not loc_match:
                continue
            image_match = re.search(r"<image:loc>(.*?)</image:loc>", block, flags=re.I | re.S)
            title_match = re.search(r"<image:title>(.*?)</image:title>", block, flags=re.I | re.S)
            product = sitemap_product(
                loc_match.group(2),
                absolute_ace_url(loc_match.group(1)),
                strip_html(title_match.group(1)) if title_match else loc_match.group(2),
                absolute_ace_url(image_match.group(1)) if image_match else "",
            )
            if product:
                products.append(product)
    return products


def sitemap_node_to_product(loc_text: str, node: ET.Element) -> Optional[Product]:
    url = absolute_ace_url(loc_text or "")
    sku = url.rstrip("/").split("/")[-1]
    if not re.fullmatch(r"\d{5,10}", sku or ""):
        return None
    if urllib.parse.urlparse(url).path.strip("/") != sku:
        return None
    image_url = ""
    title = sku
    for child in node.iter():
        tag = child.tag.rsplit("}", 1)[-1].lower()
        text = str(child.text or "").strip()
        if tag == "loc" and child is not node.find("{*}loc") and text:
            image_url = absolute_ace_url(text)
        elif tag == "title" and text:
            title = strip_html(text)
    return sitemap_product(sku, url, title, image_url)


def sitemap_product(sku: str, url: str, title: str, image_url: str = "") -> Product:
    return Product(
        sku=sku,
        title=title or sku,
        url=url,
        image_url=image_url,
        department=infer_department({"title": title, "url": url}),
        available=True,
        tags=expand_query(title),
        sales_notes=["מוצר מאינדקס sitemap חי של ACE.", "למחיר וזמינות סופיים יש לפתוח את דף המוצר החי."],
        last_seen=now(),
    )


def parse_product_cards(html_text: str, category: str = "") -> List[Product]:
    cards = re.findall(r'<li class="item product product-item".*?</li>', html_text or "", flags=re.I | re.S)
    products: List[Product] = []
    seen: set[str] = set()
    for card in cards[:80]:
        product = parse_product_card(card, category)
        if not product or product.sku in seen:
            continue
        seen.add(product.sku)
        products.append(product)
    return products


def regex_text_matches(pattern: str, value: str) -> List[str]:
    matches = re.findall(pattern, value or "", flags=re.I | re.S)
    output: List[str] = []
    for match in matches:
        if isinstance(match, tuple):
            output.extend(str(part) for part in match if str(part).strip())
        elif str(match).strip():
            output.append(str(match))
    return output


def clean_brand(value: str) -> str:
    brand = html.unescape(str(value or "")).strip()
    if not brand or brand.isdigit() or brand.lower().startswith("icon-"):
        return ""
    return brand


def parse_product_card(card: str, category: str = "") -> Optional[Product]:
    link_match = re.search(r'<a[^>]+href="([^"]+)"[^>]*class="[^"]*product[^"]*photo', card, flags=re.I)
    if not link_match:
        link_match = re.search(r'<a[^>]+href="([^"]+)"', card, flags=re.I)
    title_match = re.search(r'<strong[^>]+class="[^"]*product-item-name[^"]*"[^>]*>(.*?)</strong>', card, flags=re.I | re.S)
    img_match = re.search(r'<img[^>]+class="[^"]*product-image-photo[^"]*"[^>]+src="([^"]+)"', card, flags=re.I)
    brand_match = re.search(r'<div[^>]+class="[^"]*product-item-brand[^"]*"[^>]*>.*?<img[^>]+alt="([^"]*)"', card, flags=re.I | re.S)
    if not link_match or not title_match:
        return None

    product_url = absolute_ace_url(link_match.group(1))
    sku = product_url.rstrip("/").split("/")[-1]
    title = strip_html(title_match.group(1))
    prices = re.findall(r'<span class="priceNum">([^<]+)</span>', card, flags=re.I)
    price = parse_price(prices[0]) if prices else None
    regular_price = parse_price(prices[1]) if len(prices) > 1 else None
    promo_text = strip_html(" ".join(regex_text_matches(
        r'<span[^>]+class="[^"]*(?:savings-percentage-value|joomi-saleswatch-product-text|vat-free-text)[^"]*"[^>]*>(.*?)</span>|'
        r'<div[^>]+class="[^"]*product-item-sale-expired[^"]*"[^>]*>(.*?)</div>',
        card,
    )))
    return Product(
        sku=sku,
        title=title,
        url=product_url,
        image_url=absolute_ace_url(img_match.group(1)) if img_match else "",
        category=category,
        department=infer_department({"title": title, "category": category, "url": product_url}),
        brand=clean_brand(brand_match.group(1) if brand_match else ""),
        price=price,
        regular_price=regular_price,
        available="לא זמין" not in strip_html(card),
        promo=promo_text,
        tags=expand_query(category),
        sales_notes=["מוצר חי מאתר ACE.", "המחיר והזמינות נמשכים מהאתר בזמן החיפוש."],
        last_seen=now(),
    )


def parse_product_page(html_text: str, sku: str) -> Product:
    set_product = re.search(r"context\.setProduct\((\{.*?\})\)", html_text or "", flags=re.S)
    data: Dict[str, Any] = {}
    if set_product:
        try:
            data = json.loads(set_product.group(1))
        except json.JSONDecodeError:
            data = {}
    title = str(data.get("name") or "").strip()
    if not title:
        title_match = re.search(r'<span[^>]+itemprop="name"[^>]*>(.*?)</span>', html_text or "", flags=re.I | re.S)
        title = strip_html(title_match.group(1)) if title_match else sku
    pricing = data.get("pricing") or {}
    image_url = absolute_ace_url(str(data.get("mainImageUrl") or ""))
    if not image_url:
        img_match = re.search(r'<img[^>]+class="[^"]*(?:gallery-placeholder__image|product-image-photo)[^"]*"[^>]+src="([^"]+)"', html_text or "", flags=re.I)
        image_url = absolute_ace_url(img_match.group(1)) if img_match else ""
    brand_match = re.search(r'<div[^>]+class="[^"]*product-item-brand[^"]*"[^>]*>.*?<img[^>]+alt="([^"]*)"', html_text or "", flags=re.I | re.S)
    category = " / ".join(strip_html(v) for v in regex_text_matches(
        r'<li class="item \d+">.*?(?:<a[^>]*title="([^"]+)"|<strong>(.*?)</strong>)',
        html_text or "",
    )[:5])
    description_match = re.search(r'<div class="product attribute description">.*?<div class="value"[^>]*>(.*?)</div>', html_text or "", flags=re.I | re.S)
    specs = {}
    description = strip_html(description_match.group(1)) if description_match else ""
    if description:
        specs["תיאור"] = description[:280]
    promo = strip_html(" ".join(regex_text_matches(
        r'<span[^>]+class="[^"]*(?:savings-percentage-value|joomi-saleswatch-product-text|vat-free-text)[^"]*"[^>]*>(.*?)</span>|'
        r'<div[^>]+class="[^"]*product-item-sale-expired[^"]*"[^>]*>(.*?)</div>',
        html_text or "",
    )))
    return Product(
        sku=str(data.get("sku") or sku),
        title=title,
        url=absolute_ace_url(str(data.get("canonicalUrl") or f"{ACE_ORIGIN}/{sku}")),
        image_url=image_url,
        category=category,
        department=infer_department({"title": title, "category": category, "url": data.get("canonicalUrl", "")}),
        brand=clean_brand(brand_match.group(1) if brand_match else ""),
        price=parse_price(pricing.get("specialPrice") or pricing.get("regularPrice")),
        regular_price=parse_price(pricing.get("regularPrice")),
        available="לא זמין" not in strip_html(html_text or ""),
        promo=promo,
        specs=specs,
        sales_notes=["פרטי מוצר חיים מאתר ACE.", "יש להפנות לדף המוצר לפרטים מחייבים וסופיים."],
        last_seen=now(),
    )


def expand_query(query: str) -> List[str]:
    q = normalize_text(query)
    terms = [part for part in re.split(r"[\s,.;:!?]+", q) if part]
    synonyms = {
        "sofa": ["sofa", "couch", "ספה", "ספות", "סלון"],
        "couch": ["sofa", "couch", "ספה", "ספות", "סלון"],
        "ספה": ["ספה", "ספות", "sofa", "couch", "סלון"],
        "ספות": ["ספה", "ספות", "sofa", "couch", "סלון"],
        "מיטה": ["מיטה", "נפתחת", "אירוח"],
        "bed": ["מיטה", "נפתחת", "אירוח"],
        "gray": ["אפור", "gray", "grey"],
        "grey": ["אפור", "gray", "grey"],
        "אפור": ["אפור", "gray", "grey"],
        "brown": ["חום", "בז", "brown"],
        "חום": ["חום", "בז", "brown"],
    }
    expanded = set(terms)
    for term in terms:
        expanded.update(synonyms.get(term, []))
    if any(t in q for t in ["סלון", "רביצה", "פינת"]):
        expanded.update(["ספה", "ספות"])
    return [normalize_text(term) for term in expanded if normalize_text(term)]


def score_product(product: Product, terms: List[str]) -> int:
    if not terms:
        return 1
    blob = product.search_blob()
    title = normalize_text(product.title)
    score = 0
    for term in terms:
        if not term:
            continue
        if term in title:
            score += 6
        elif term in blob:
            score += 2
    if product.available:
        score += 1
    if product.promo:
        score += 1
    return score


def showcase_products(department: str = "", limit: int = 6) -> List[Product]:
    """Products for idle store screens.

    This is deliberately local-data first so the store-owner demo keeps working
    after the product cache is built.
    """
    department_norm = normalize_text(department)
    candidates = [product for product in catalog.products if product.available]
    if not candidates:
        candidates = list(catalog.products)

    def rank(product: Product) -> tuple[int, int, float]:
        discount = discount_percent(product.price, product.regular_price) or 0
        department_match = 1 if department_norm and department_norm == normalize_text(product.department) else 0
        promo = 1 if product.promo else 0
        rich = len(product.specs or {}) + len(product.sales_notes or {})
        image = 1 if product.image_url else 0
        price = product.price if product.price is not None else 10**9
        return (department_match, discount + promo + rich + image, -price)

    ordered = sorted(candidates, key=rank, reverse=True)
    return ordered[: max(1, min(limit, 12))]


async def search_catalog(
    q: str = "",
    category: str = "",
    min_price: Optional[float] = None,
    max_price: Optional[float] = None,
    limit: int = 12,
) -> List[Product]:
    result = await search_catalog_with_source(q, category, min_price, max_price, limit)
    return result["products"]


async def search_catalog_with_source(
    q: str = "",
    category: str = "",
    min_price: Optional[float] = None,
    max_price: Optional[float] = None,
    limit: int = 12,
    live_only: bool = False,
) -> Dict[str, Any]:
    live_products = await live_catalog.search(q, category, min_price, max_price, limit)
    if live_products:
        return {"source": "live", "fallback_used": False, "products": live_products}
    if live_only:
        return {"source": "live", "fallback_used": False, "products": []}
    return {"source": "fallback", "fallback_used": True, "products": catalog.search(q, category, min_price, max_price, limit)}


async def get_catalog_product(sku: str) -> Product:
    result = await get_catalog_product_with_source(sku)
    return result["product"]


async def get_catalog_product_with_source(sku: str, live_only: bool = False) -> Dict[str, Any]:
    live_product = await live_catalog.get(sku)
    if live_product:
        return {"source": "live", "fallback_used": False, "product": live_product}
    if live_only:
        raise HTTPException(status_code=404, detail="Live ACE product not found")
    return {"source": "fallback", "fallback_used": True, "product": catalog.get(sku)}


async def scrape_category(url: str) -> List[Product]:
    async with httpx.AsyncClient(timeout=20, follow_redirects=True, headers=ACE_HEADERS) as client:
        response = await client.get(url)
    response.raise_for_status()
    html_text = response.text
    return parse_product_cards(html_text, category="ריהוט לסלון / ספות")


@dataclass
class ScreenState:
    id: str
    name: str
    location_label: str
    department: str
    priority: int
    available: bool = True
    connected: bool = False
    leased_by: Optional[str] = None
    lease_until: Optional[float] = None
    last_seen: Optional[float] = None
    display: Dict[str, Any] = field(default_factory=lambda: {"mode": "idle"})

    def public(self) -> Dict[str, Any]:
        data = asdict(self)
        data["lease_remaining"] = max(0, int((self.lease_until or 0) - now()))
        data["status"] = self.status()
        return data

    def status(self) -> str:
        if self.leased_by:
            return "leased"
        if self.available:
            return "idle"
        return "busy"


class ScreenManager:
    def __init__(self) -> None:
        self.screens: Dict[str, ScreenState] = {}
        self.reset()

    def reset(self) -> None:
        configured = [
            ScreenState("entrance", "Entrance Screen", "ליד הכניסה הראשית", "general", 10, available=False),
            ScreenState("electricity", "Electricity Screen", "ליד מחלקת החשמל", "electricity", 20, available=False),
            ScreenState("plumbing", "Plumbing Screen", "ליד מחלקת האינסטלציה", "plumbing", 30, available=True),
            ScreenState("garden", "Garden Screen", "ליד מחלקת הגינה", "garden", 40, available=False),
            ScreenState("furniture", "Furniture Screen", "ליד מחלקת הריהוט", "furniture", 5, available=False),
        ]
        self.screens = {screen.id: screen for screen in configured}

    def cleanup(self) -> None:
        current = now()
        for screen in self.screens.values():
            if screen.lease_until and screen.lease_until < current:
                screen.leased_by = None
                screen.lease_until = None
                screen.display = {"mode": "idle"}

    def list(self) -> List[Dict[str, Any]]:
        self.cleanup()
        return [screen.public() for screen in sorted(self.screens.values(), key=lambda s: s.priority)]

    def get(self, screen_id: str) -> ScreenState:
        self.cleanup()
        if screen_id not in self.screens:
            raise HTTPException(status_code=404, detail="Screen not found")
        return self.screens[screen_id]

    def heartbeat(self, screen_id: str) -> Dict[str, Any]:
        screen = self.get(screen_id)
        screen.connected = True
        screen.last_seen = now()
        return screen.public()

    def set_available(self, screen_id: str, available: bool) -> Dict[str, Any]:
        screen = self.get(screen_id)
        screen.available = available
        if not available:
            screen.leased_by = None
            screen.lease_until = None
            screen.display = {"mode": "busy"}
        elif screen.display.get("mode") == "busy":
            screen.display = {"mode": "idle"}
        return screen.public()

    def release(self, screen_id: str) -> Dict[str, Any]:
        screen = self.get(screen_id)
        screen.leased_by = None
        screen.lease_until = None
        screen.display = {"mode": "idle"}
        return screen.public()

    def lease(self, session_id: str, preferred_department: str = "general") -> ScreenState:
        self.cleanup()
        for screen in self.screens.values():
            if screen.leased_by == session_id:
                screen.lease_until = now() + LEASE_SECONDS
                return screen
        idle = [s for s in self.screens.values() if s.available and not s.leased_by]
        if not idle:
            raise HTTPException(status_code=409, detail="No available screen")
        def rank(screen: ScreenState) -> tuple[int, int]:
            department_score = 0 if screen.department == preferred_department else 1
            return (department_score, screen.priority)
        screen = sorted(idle, key=rank)[0]
        screen.leased_by = session_id
        screen.lease_until = now() + LEASE_SECONDS
        return screen

    def show(
        self,
        session_id: str,
        products: List[Dict[str, Any]],
        headline: str,
        message: str,
        mode: str,
        preferred_department: str,
    ) -> Dict[str, Any]:
        screen = self.lease(session_id, preferred_department)
        screen.display = {
            "mode": mode or "products",
            "headline": headline,
            "message": message,
            "products": products,
            "updated_at": now(),
        }
        screen.lease_until = now() + LEASE_SECONDS
        return screen.public()


catalog = Catalog()
live_catalog = AceLiveCatalog()
screens = ScreenManager()
session_memory: Dict[str, Dict[str, Any]] = {}


SYSTEM_PROMPT = """
את יועצת קנייה קולית של ACE. המטרה שלך היא לעזור ללקוח לבחור בביטחון: להבין צורך, להציע התאמה טובה, להסביר ערך ברור, ולהקל על מעבר לדף המוצר כשהלקוח מוכן.

התנהגות:
- דברי בעברית טבעית, קצרה וברורה.
- כל טקסט שאת מציגה או מתמללת ללקוח חייב להיות בעברית תקנית או מספרים/שמות מוצר כפי שהם באתר. אם תמלול קולי נראה כמו ערבית, רוסית או שפה אחרת בגלל זיהוי שגוי, אל תציגי אותו כפי שהוא; התייחסי אליו כבקשה קולית בעברית ובקשי הבהרה קצרה.
- כשלקוח מבקש מוצר, שאלי עד שתי שאלות קנייה חסרות: תקציב, מידה, שימוש, צבע, דחיפות או צורך במשלוח.
- אחרי שיש מספיק מידע, הציגי 2-3 מוצרים אמיתיים מהכלים בלבד. לעולם אל תמציאי מוצר, מחיר, מלאי או קישור.
- ברירת המחדל היא ייעוץ מכירתי רגוע: הדגישי התאמה לצורך, מבצע, זמינות אונליין ושימושיות בלי לחץ ובלי ניסוחים אגרסיביים.
- אל תתנדבי לדבר על חסרונות או מחיר גבוה. אם הלקוח מבקש במפורש, מסגרי את זה כהתאמה לצורך: "אם החלל קטן", "אם התקציב הוא השיקול המרכזי", "אם חשוב אירוח"; בלי לתייג מוצר כיקר.
- אם מתאים להציג על מסך גדול, קראי ל-show_on_screen. אם מוקצה מסך, אמרי ללקוח איפה למצוא אותו.
- מלאי הוא זמינות אונליין בלבד, לא מלאי סניף.
- סגירה: הציעי בעדינות לפתוח את דף המוצר ב-ACE או להציג על המסך הגדול. אל תדחפי לרכישה אם הלקוח עדיין מתלבט.
""".strip()


TOOLS = [
    {
        "type": "function",
        "name": "search_products",
        "description": "חיפוש מוצרים אמיתיים ישירות בקטלוג ACE החי לפי צורך, תקציב וקטגוריה. הכלי מחזיר רק תוצאות live, לא מוצרי fallback של הדמו.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "category": {"type": "string"},
                "min_price": {"type": "number"},
                "max_price": {"type": "number"},
                "limit": {"type": "integer"},
            },
            "required": [],
        },
    },
    {
        "type": "function",
        "name": "get_product_details",
        "description": "קבלת פרטי מוצר ישירות מדף מוצר חי באתר ACE לפי SKU. אם אין מוצר חי, הכלי מחזיר שגיאה ולא משתמש ב-fallback.",
        "parameters": {
            "type": "object",
            "properties": {"sku": {"type": "string"}},
            "required": ["sku"],
        },
    },
    {
        "type": "function",
        "name": "get_screen_status",
        "description": "בדיקת זמינות מסכי החנות לדמו.",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
    {
        "type": "function",
        "name": "show_on_screen",
        "description": "הצגת מוצרים או השוואה במסך חנות זמין.",
        "parameters": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "product_ids": {"type": "array", "items": {"type": "string"}},
                "headline": {"type": "string"},
                "message": {"type": "string"},
                "mode": {"type": "string", "enum": ["products", "comparison", "hero"]},
                "department": {"type": "string"},
            },
            "required": ["product_ids"],
        },
    },
    {
        "type": "function",
        "name": "answer_store_policy",
        "description": "תשובות קצרות על משלוח, זמינות, החזרות ומגבלות הדמו.",
        "parameters": {
            "type": "object",
            "properties": {"topic": {"type": "string"}},
            "required": [],
        },
    },
]


app = FastAPI(title="ACE Voice Sales Assistant Demo")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def customer_page() -> HTMLResponse:
    return HTMLResponse(read_template("customer.html"))


@app.get("/screen/{screen_id}")
def screen_page(screen_id: str, request: Request) -> HTMLResponse:
    screen = screens.get(screen_id)
    content = (
        read_template("screen.html")
        .replace("__SCREEN_ID__", html.escape(screen.id))
        .replace("__PUBLIC_BASE_URL__", html.escape(public_base_url(request), quote=True))
    )
    return HTMLResponse(content)


@app.get("/demo")
def demo_page() -> HTMLResponse:
    return HTMLResponse(read_template("demo.html"))


@app.get("/healthz")
def healthz() -> Dict[str, Any]:
    screens.cleanup()
    return {
        "ok": True,
        "products": len(catalog.products),
        "screens": len(screens.screens),
        "catalog_loaded_from": catalog.loaded_from,
    }


@app.get("/api/catalog/status")
async def catalog_status(refresh: bool = False) -> Dict[str, Any]:
    status = await live_catalog.status(refresh=refresh)
    return {
        "local_fallback_products": len(catalog.products),
        "local_fallback_source": catalog.loaded_from,
        "live_catalog": status,
    }


@app.get("/api/catalog/audit")
async def catalog_audit(
    sample_size: int = Query(10, ge=1, le=25),
    offset: int = Query(0, ge=0),
    refresh: bool = False,
    strategy: str = Query("slice", pattern="^(slice|spread)$"),
    verify_search: bool = False,
) -> Dict[str, Any]:
    return await live_catalog.audit(
        sample_size=sample_size,
        offset=offset,
        refresh=refresh,
        strategy=strategy,
        verify_search=verify_search,
    )


@app.get("/api/products/search")
async def search_products(
    q: str = Query("", alias="q"),
    category: str = "",
    min_price: Optional[float] = None,
    max_price: Optional[float] = None,
    limit: int = 12,
    live_only: bool = False,
    include_meta: bool = False,
) -> Any:
    result = await search_catalog_with_source(q, category, min_price, max_price, limit, live_only=live_only)
    products = [p.public() for p in result["products"]]
    if include_meta:
        return {
            "source": result["source"],
            "fallback_used": result["fallback_used"],
            "live_only": live_only,
            "count": len(products),
            "products": products,
        }
    return products


@app.get("/api/products/{sku}")
async def product_details(sku: str, live_only: bool = False, include_meta: bool = False) -> Dict[str, Any]:
    result = await get_catalog_product_with_source(sku, live_only=live_only)
    product = result["product"].public()
    if include_meta:
        return {
            "source": result["source"],
            "fallback_used": result["fallback_used"],
            "live_only": live_only,
            "product": product,
        }
    return product


@app.get("/api/idle-showcase")
def idle_showcase(
    department: str = "",
    limit: int = 6,
) -> Dict[str, Any]:
    products = [product.public() for product in showcase_products(department, limit)]
    return {
        "headline": "מוצרים חמים באתר ACE",
        "message": "המסך מציג הצעות מהאתר ומזמין לקוחות להמשיך עם יועץ המכירות הדיגיטלי.",
        "products": products,
        "website_path": "/",
    }


@app.get("/api/products/{sku}/image")
async def product_image(sku: str) -> Response:
    product = await get_catalog_product(sku)
    IMAGE_CACHE_DIR.mkdir(exist_ok=True)
    path = product_image_path(product.sku)
    if path.exists():
        return Response(path.read_bytes(), media_type="image/jpeg")
    if product.image_url:
        try:
            async with httpx.AsyncClient(timeout=12, follow_redirects=True) as client:
                resp = await client.get(product.image_url, headers=ACE_HEADERS)
            if resp.status_code < 400 and resp.content:
                path.write_bytes(resp.content)
                return Response(resp.content, media_type=resp.headers.get("content-type", "image/jpeg"))
        except httpx.RequestError:
            pass
    svg = placeholder_svg(product.title)
    return Response(svg.encode("utf-8"), media_type="image/svg+xml")


def placeholder_svg(title: str) -> str:
    safe = html.escape(title[:80])
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="900" height="620" viewBox="0 0 900 620">
<rect width="900" height="620" fill="#f3f4f6"/>
<rect x="70" y="110" width="760" height="350" rx="26" fill="#d9dee7"/>
<rect x="140" y="250" width="620" height="150" rx="28" fill="#aeb7c5"/>
<text x="450" y="515" text-anchor="middle" font-family="Arial, sans-serif" font-size="30" fill="#1f2937">{safe}</text>
</svg>"""


@app.get("/api/qr")
def qr(data: str) -> Response:
    try:
        import qrcode
        import qrcode.image.svg
        factory = qrcode.image.svg.SvgImage
        image = qrcode.make(data, image_factory=factory, border=2)
        return Response(image.to_string(), media_type="image/svg+xml")
    except Exception:
        safe = html.escape(data)
        svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="260" height="260" viewBox="0 0 260 260">
<rect width="260" height="260" fill="#fff"/><rect x="18" y="18" width="64" height="64" fill="#111827"/>
<rect x="178" y="18" width="64" height="64" fill="#111827"/><rect x="18" y="178" width="64" height="64" fill="#111827"/>
<text x="130" y="135" text-anchor="middle" font-family="Arial" font-size="15" fill="#111827">QR unavailable</text>
<text x="130" y="157" text-anchor="middle" font-family="Arial" font-size="11" fill="#4b5563">{safe[:32]}</text>
</svg>"""
        return Response(svg, media_type="image/svg+xml")


@app.get("/api/screens")
def list_screens() -> Dict[str, Any]:
    return {"screens": screens.list()}


@app.get("/api/screens/{screen_id}")
def get_screen(screen_id: str) -> Dict[str, Any]:
    return screens.get(screen_id).public()


@app.post("/api/screens/{screen_id}/heartbeat")
def screen_heartbeat(screen_id: str) -> Dict[str, Any]:
    return screens.heartbeat(screen_id)


@app.post("/api/screens/{screen_id}/availability")
async def set_screen_availability(screen_id: str, request: Request) -> Dict[str, Any]:
    body = await request.json()
    return screens.set_available(screen_id, bool(body.get("available")))


@app.post("/api/screens/{screen_id}/release")
def release_screen(screen_id: str) -> Dict[str, Any]:
    return screens.release(screen_id)


@app.post("/api/show")
async def show_on_screen(request: Request) -> Dict[str, Any]:
    body = await request.json()
    session_id = str(body.get("session_id") or "demo-session")
    product_ids = [str(v) for v in body.get("product_ids", [])]
    if product_ids:
        products = [(await get_catalog_product(sku)).public() for sku in product_ids]
    else:
        products = [p.public() for p in await search_catalog(body.get("query", "ספה"), limit=3)]
    if not products:
        raise HTTPException(status_code=404, detail="No products to show")
    department = body.get("department") or products[0].get("department") or "general"
    screen = screens.show(
        session_id=session_id,
        products=products,
        headline=str(body.get("headline") or "ההמלצות של ACE"),
        message=str(body.get("message") or "בחרתי כמה אפשרויות שמתאימות לצורך שלך."),
        mode=str(body.get("mode") or "products"),
        preferred_department=str(department),
    )
    return {"screen": screen, "products": products}


@app.post("/api/demo/reset")
def demo_reset() -> Dict[str, Any]:
    screens.reset()
    session_memory.clear()
    return {"ok": True, "screens": screens.list()}


@app.post("/api/demo/preload-sofa")
async def preload_sofa() -> Dict[str, Any]:
    products = [p.public() for p in await search_catalog("ספה נפתחת סלון", max_price=4000, limit=3)]
    screen = screens.show(
        session_id="presenter-preload",
        products=products,
        headline="תרחיש דמו: לקוח מחפש ספה לסלון",
        message="המסך מוכן להציג את ההמלצות אחרי שיחת המכירה.",
        mode="comparison",
        preferred_department="furniture",
    )
    return {"ok": True, "screen": screen, "products": products}


@app.post("/api/demo/refresh-catalog")
async def refresh_catalog() -> Dict[str, Any]:
    return await catalog.refresh_sofas()


@app.post("/api/demo/chat")
async def demo_chat(request: Request) -> Dict[str, Any]:
    body = await request.json()
    session_id = str(body.get("session_id") or "demo-session")
    text = str(body.get("text") or "")
    memory = session_memory.setdefault(session_id, {"stage": "new"})
    lower = normalize_text(text)
    asks_for_tradeoff = any(term in lower for term in ["חסרון", "חסרונות", "טרייד", "trade", "השוואה", "להשוות"])

    if memory["stage"] == "new" and any(term in lower for term in ["ספה", "sofa", "couch", "סלון"]):
        memory["stage"] = "qualified"
        return {
            "message": "בשמחה. כדי להתאים לך ספה שתסגור גם מראה וגם שימוש: מה התקציב בערך, והאם חשוב שהיא תיפתח למיטה?",
            "stage": memory["stage"],
        }

    if memory.get("stage") == "recommended" and asks_for_tradeoff and memory.get("products"):
        products = memory["products"]
        best = products[0]
        return {
            "message": (
                f"כן. מבחינת התאמה, {best['title']} היא הבחירה שהייתי מתקדם איתה כי היא סוגרת גם סלון וגם אירוח. "
                "אם החלל קטן יותר, יש אפשרות קומפקטית יותר; ואם המטרה היא רביצה רחבה, יש אפשרות מפנקת יותר. "
                "לצורך שתיארת זו התאמה טובה, ואפשר לפתוח את דף המוצר כשתרצה לראות פרטים מלאים."
            ),
            "stage": memory["stage"],
            "products": products,
        }

    wants_budget = re.search(r"(\d[\d,\.]*)", text)
    wants_bed = any(term in lower for term in ["מיטה", "נפתחת", "אורח", "bed"])
    max_price = parse_price(wants_budget.group(1)) if wants_budget else 4000
    query = "ספה נפתחת מיטה" if wants_bed else "ספה סלון"
    products = [p.public() for p in await search_catalog(query, max_price=max_price, limit=3)]
    if not products:
        products = [p.public() for p in await search_catalog("ספה", limit=3)]

    best = products[0]
    screen = screens.show(
        session_id=session_id,
        products=products,
        headline="המלצת יועץ ACE לספה",
        message=f"האפשרות המובילה היא {best['title']} כי היא נותנת פתרון סלון חזק, שימושיות יומיומית וזמינות אונליין.",
        mode="comparison",
        preferred_department=best.get("department") or "furniture",
    )
    location = screen["location_label"]
    memory["stage"] = "recommended"
    memory["products"] = products
    return {
        "message": (
            f"מצאתי לך התאמה טובה. כדאי לשקול את {best['title']}: היא יושבת טוב על הצורך, "
            f"{'נפתחת למיטה ולכן טובה גם לאירוח, ' if wants_bed else ''}"
            f"וזמינה אונליין. שמתי את ההמלצה והאפשרויות ליד על המסך {location}. "
            "אם זה נראה מתאים, אפשר לפתוח את דף המוצר ב-ACE ולבדוק פרטים מלאים."
        ),
        "stage": memory["stage"],
        "products": products,
        "screen": screen,
    }


@app.get("/api/policy")
def policy(topic: str = "") -> Dict[str, str]:
    topic_norm = normalize_text(topic)
    if "מלאי" in topic_norm or "inventory" in topic_norm:
        answer = "בדמו הזה זמינות היא זמינות אונליין מאתר ACE. מלאי סניף אמיתי דורש חיבור למערכת ACE."
    elif "משלוח" in topic_norm or "delivery" in topic_norm:
        answer = "מועד ודמי משלוח תלויים במוצר ובכתובת. היועץ מפנה לדף המוצר ב-ACE לפרטים המחייבים."
    elif "החזר" in topic_norm or "return" in topic_norm:
        answer = "מדיניות החזרות מחייבת צריכה להילקח מדפי השירות של ACE. בדמו נציג תשובה כללית ונפנה לאתר."
    else:
        answer = "אני יכול לענות על מוצרים, זמינות אונליין, משלוח, החזרות, והשוואות קנייה בדמו."
    return {"answer": answer}


def realtime_session_config() -> Dict[str, Any]:
    return {
        "type": "realtime",
        "model": REALTIME_MODEL,
        "instructions": SYSTEM_PROMPT,
        "tools": TOOLS,
        "tool_choice": "auto",
        "audio": {
            "output": {"voice": "marin"},
            "input": {
                "transcription": {
                    "model": "gpt-realtime-whisper",
                    "language": "he",
                    "delay": "medium",
                },
            },
        },
        "reasoning": {"effort": "low"},
    }


@app.post("/api/session")
async def create_realtime_session(request: Request) -> Dict[str, Any]:
    load_dotenv()
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise HTTPException(status_code=503, detail="OPENAI_API_KEY is not configured. Use the typed demo path.")
    body = {}
    try:
        body = await request.json()
    except Exception:
        body = {}
    safety_id = str(body.get("session_id") or "ace-demo")
    session = realtime_session_config()
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "OpenAI-Safety-Identifier": safety_id,
    }
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(OPENAI_CLIENT_SECRETS_URL, headers=headers, json={"session": session})
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"OpenAI session failed: {response.text}")
    data = response.json()
    client_secret = data.get("client_secret")
    token = (
        client_secret.get("value") if isinstance(client_secret, dict) else client_secret
    ) or data.get("value") or data.get("token")
    if not token:
        raise HTTPException(status_code=502, detail="OpenAI did not return an ephemeral token")
    return {"token": token, "session": data}


@app.post("/api/transcribe")
async def transcribe_audio(request: Request, session_id: str = "ace-demo") -> Dict[str, Any]:
    load_dotenv()
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise HTTPException(status_code=503, detail="OPENAI_API_KEY is not configured. Use the typed demo path.")

    audio = await request.body()
    if len(audio) < 512:
        raise HTTPException(status_code=400, detail="Audio recording is too short")

    content_type = request.headers.get("content-type") or "audio/webm"
    if "mp4" in content_type or "m4a" in content_type or "aac" in content_type:
        filename = "speech.m4a"
    elif "mpeg" in content_type or "mp3" in content_type:
        filename = "speech.mp3"
    elif "wav" in content_type:
        filename = "speech.wav"
    elif "ogg" in content_type:
        filename = "speech.ogg"
    else:
        filename = "speech.webm"

    headers = {
        "Authorization": f"Bearer {api_key}",
        "OpenAI-Safety-Identifier": session_id,
    }
    prompt = "עברית ישראלית. לקוח בחנות ACE שואל על ספה, ריהוט, משלוח, זמינות אונליין או מוצר לבית."
    models = [TRANSCRIPTION_MODEL]
    if TRANSCRIPTION_MODEL != "whisper-1":
        models.append("whisper-1")

    last_error = ""
    async with httpx.AsyncClient(timeout=45) as client:
        for model in models:
            files = {
                "file": (filename, audio, content_type),
                "model": (None, model),
                "language": (None, "he"),
                "prompt": (None, prompt),
            }
            response = await client.post(OPENAI_TRANSCRIPTIONS_URL, headers=headers, files=files)
            if response.status_code < 400:
                data = response.json()
                text = str(data.get("text") or "").strip()
                if not text:
                    raise HTTPException(status_code=502, detail="Transcription returned empty text")
                return {"text": text, "model": model}
            last_error = response.text
            if response.status_code not in {400, 404}:
                break
    raise HTTPException(status_code=502, detail=f"OpenAI transcription failed: {last_error}")


@app.post("/api/realtime/sdp")
async def create_realtime_call(request: Request, session_id: str = "ace-demo") -> Response:
    """Server-side WebRTC negotiation relay.

    This avoids browser-specific failures when posting SDP directly to OpenAI from
    embedded browsers while keeping the API key server-side.
    """
    load_dotenv()
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise HTTPException(status_code=503, detail="OPENAI_API_KEY is not configured. Use the typed demo path.")

    offer_sdp = (await request.body()).decode("utf-8", errors="replace").strip()
    if not offer_sdp.startswith("v="):
        raise HTTPException(status_code=400, detail="Request body must be an SDP offer")
    if len(offer_sdp) < 100:
        raise HTTPException(status_code=400, detail=f"SDP offer is too short ({len(offer_sdp)} bytes)")

    files = {
        "sdp": (None, offer_sdp),
        "session": (None, json.dumps(realtime_session_config(), ensure_ascii=False)),
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "OpenAI-Safety-Identifier": session_id,
    }
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            "https://api.openai.com/v1/realtime/calls",
            headers=headers,
            files=files,
        )
    if response.status_code >= 400:
        raise HTTPException(
            status_code=502,
            detail=f"OpenAI WebRTC negotiation failed after forwarding {len(offer_sdp)} bytes of SDP: {response.text}",
        )
    return Response(response.text, media_type="application/sdp")


@app.api_route("/proxy/{path:path}", methods=["GET", "HEAD"])
async def proxy_ace(path: str, request: Request) -> Response:
    target_path = "/" + path if path else "/"
    target = urllib.parse.urljoin(ACE_ORIGIN, target_path)
    if request.url.query:
        target = f"{target}?{request.url.query}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "he-IL,he;q=0.9,en;q=0.8",
    }
    async with httpx.AsyncClient(follow_redirects=True, timeout=20, headers=headers) as client:
        resp = await client.get(target)
    content_type = resp.headers.get("content-type", "")
    blocked = {
        "x-frame-options",
        "content-security-policy",
        "content-security-policy-report-only",
        "content-length",
        "transfer-encoding",
        "content-encoding",
    }
    out_headers = {k: v for k, v in resp.headers.items() if k.lower() not in blocked}
    if "text/html" not in content_type:
        return Response(resp.content, media_type=content_type, headers=out_headers)
    content = inject_proxy_script(resp.text)
    return HTMLResponse(content=content, headers=out_headers)


@app.api_route("/proxy", methods=["GET", "HEAD"])
async def proxy_root(request: Request) -> Response:
    return await proxy_ace("", request)


def inject_proxy_script(source: str) -> str:
    html_text = re.sub(
        r"<meta[^>]+http-equiv=[\"']?Content-Security-Policy[\"']?[^>]*>",
        "",
        source,
        flags=re.I,
    )
    if "<head" in html_text:
        html_text = re.sub(r"(<head[^>]*>)", r'\1<base href="https://www.ace.co.il/">', html_text, count=1, flags=re.I)
    script = """
<script>
(function(){
  function localize(url){
    try {
      var u = new URL(url, "https://www.ace.co.il/");
      if (/(^|\\.)ace\\.co\\.il$/i.test(u.hostname)) return "/proxy" + u.pathname + u.search + u.hash;
    } catch(e) {}
    return url;
  }
  document.addEventListener("click", function(event){
    var link = event.target.closest && event.target.closest("a[href]");
    if (!link) return;
    var next = localize(link.href);
    if (next !== link.href) {
      event.preventDefault();
      window.location.href = next;
    }
  }, true);
})();
</script>
"""
    if "</body>" in html_text.lower():
        return re.sub(r"</body>", script + "</body>", html_text, count=1, flags=re.I)
    return html_text + script


if __name__ == "__main__":
    import uvicorn

    load_dotenv()
    uvicorn.run("server:app", host="0.0.0.0", port=int(os.getenv("PORT", "8765")), reload=False)
