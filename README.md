# ACE Voice Sales Assistant Demo

Local store-owner demo for an ACE sales consultant assistant.

## Run

```bash
python3 -m pip install -r requirements.txt
cp .env.example .env
python3 server.py
```

Open:

- Customer demo: http://localhost:8765/
- Presenter console: http://localhost:8765/demo
- Store screen example: http://localhost:8765/screen/plumbing

`OPENAI_API_KEY` is only needed for live voice. The typed demo path works without it.

## Demo Flow

1. Open `/demo`, then open the customer and screen pages.
2. Reset the demo. By default, only the screen near plumbing is available.
3. In the customer panel, click `לקוח מחפש ספה`.
4. Click `תקציב וצורך`.
5. The assistant recommends sofa options and pushes the comparison to the available screen.

## Data

- `data/fallback_products.json` keeps the demo reliable offline.
- `POST /api/demo/refresh-catalog` tries to refresh sofa products from public ACE pages.
- `GET /api/catalog/status?refresh=true` loads the live ACE sitemap/category sources and reports coverage.
- `GET /api/catalog/audit?sample_size=5&strategy=spread&verify_search=true&search_pages=3&refresh=true` samples live sitemap products across the catalog and verifies they resolve through ACE product pages and assistant search pages.
- `GET /api/catalog/readiness?sample_size=3&search_pages=3&refresh=true` summarizes live ACE access, deterministic first/middle/last sitemap-position access, and text-search health separately.
- `GET /api/products/search?q=...&live_only=true&include_meta=true` proves a search result came from live ACE access instead of demo fallback data.
- `GET /api/products/browse?page=1&limit=12&include_meta=true` pages through the live ACE sitemap product index, which is the completeness path when search ranking does not expose a product by title.
- `GET /api/products/catalog-position/123?include_meta=true` resolves a deterministic 1-based product position from the live ACE sitemap index.
- Live ACE sitemap/search data is kept in memory only and is not written as a local product catalog copy.
- Product images are cached under `data/image_cache` after first successful fetch.

## Tests

```bash
pytest
```

## Deploy For Partner Demo

This app is ready for a single web-service deployment. Render is the recommended target because the demo needs a long-running FastAPI service for screen state, heartbeats, QR generation, ACE proxying, and OpenAI calls.

### Render

1. Put this project in a Git repo and push it to GitHub, GitLab, or Bitbucket.
2. In Render, create a new Blueprint from the repo. The included `render.yaml` provisions one Python web service.
3. Set `OPENAI_API_KEY` in Render environment variables. Do not commit `.env`.
4. Optional: set `DEMO_PUBLIC_BASE_URL` to the deployed URL or custom domain. If unset, the app uses the current request host for website QR codes.
5. Open these URLs after deploy:

- Customer demo: `https://YOUR-DEMO-HOST/`
- Presenter console: `https://YOUR-DEMO-HOST/demo`
- Store screen: `https://YOUR-DEMO-HOST/screen/plumbing`
- Health check: `https://YOUR-DEMO-HOST/healthz`

The idle store screen QR opens the website/customer assistant. Screen assignment is still automatic by availability when the assistant pushes a product to a screen.

### Render Without GitHub App Access

If Render cannot access the GitHub repo, deploy the included Docker image instead:

```bash
docker build -t ghcr.io/scrioteam/ace-voice-assistant:latest .
docker push ghcr.io/scrioteam/ace-voice-assistant:latest
```

Then create a Render web service from the image URL and set the same environment variables listed above. This avoids Render GitHub App authorization issues.

### Generic Python Host

Use the included `Procfile` or start command:

```bash
uvicorn server:app --host 0.0.0.0 --port $PORT
```

Required environment variable:

- `OPENAI_API_KEY` for live voice and transcription.

Optional environment variables:

- `OPENAI_REALTIME_MODEL`, default `gpt-realtime-2`.
- `OPENAI_TRANSCRIPTION_MODEL`, default `gpt-4o-mini-transcribe`.
- `DEMO_PUBLIC_BASE_URL`, useful for custom domains or tunnels.
