# Opus Backend

Django 5 + DRF + Channels backend for the Opus apps (Opus Go client + Opus
Master). Split out of the mobile monorepo so it can be deployed independently
(e.g. on Railway).

- API: Django REST Framework (token auth)
- Realtime: Django Channels + Redis (order offers, chat, live location)
- DB: PostgreSQL
- SMS OTP: Eskiz.uz (mock/dry-run fallback for dev)

## Layout

```
config/        Django project (settings, asgi, urls, routing)
apps/          accounts, masters, billing, orders, chat, geo, reviews, support, notifications
tests/         backend test suite
Dockerfile     production image (uvicorn ASGI)
railway.json   Railway build + start (migrate, collectstatic, seed_categories)
compose.yaml   local dev stack (postgres + redis + backend)
```

## Local dev (Docker)

```bash
cp .env.example .env
docker compose up --build
# API at http://localhost:8000/api/  · health: /api/health/
```

## Local dev (SQLite, no services)

```bash
pip install -r requirements.txt
export MASTERGO_USE_SQLITE=1 MASTERGO_USE_INMEMORY_CHANNELS=1
python manage.py migrate
python manage.py seed_categories
python manage.py runserver
python manage.py test tests   # run the suite
```

## Deploy on Railway

1. New Project → Deploy from this GitHub repo. It builds from `Dockerfile` and
   runs its production command (migrate → collectstatic →
   seed_categories → uvicorn on `$PORT`). No demo/fake data is seeded.
2. Add plugins **PostgreSQL** and **Redis** (they inject `DATABASE_URL` and
   `REDIS_URL`).
3. Set variables (see table). Health check: `GET /api/health/`.
4. Create an admin: `python manage.py createsuperuser` (Railway shell). Masters
   are approved from Django Admin.

### Environment variables

| Variable | Value | Notes |
|---|---|---|
| `DJANGO_SECRET_KEY` | random string | **Required** in production |
| `DJANGO_DEBUG` | `0` | production |
| `DJANGO_ALLOWED_HOSTS` | `localhost,127.0.0.1` | Railway domain auto-added |
| `DATABASE_URL` | `${{Postgres.DATABASE_URL}}` | from plugin |
| `REDIS_URL` | `${{Redis.REDIS_URL}}` | from plugin (Channels + OTP cache) |
| `REDIS_SOCKET_TIMEOUT_SECONDS` | `2` | Redis connection and cache read timeout |
| `CHANNELS_REDIS_SOCKET_TIMEOUT_SECONDS` | `10` | Channels read timeout; must exceed its 5-second blocking receive |
| `MASTERGO_ORDER_SWEEPER_ENABLED` | `1` | one bounded worker expires stale offers |
| `MASTERGO_MAX_ONLINE_HOURS` | `8` | automatically takes a master offline after one online session |
| `MASTERGO_SUPPORT_INACTIVITY_HOURS` | `3` | closes a support chat when the operator is waiting for the user |
| `MASTERGO_REALTIME_SEND_TIMEOUT_SECONDS` | `2` | Redis must not block API requests |
| `DATABASE_STATEMENT_TIMEOUT_MS` | `20000` | bounds slow/locked SQL statements |
| `MAPBOX_ACCESS_TOKEN` | restricted public token | forward/reverse Geocoding v6 |
| `MASTERGO_MEDIA_STORAGE` | `r2` | Use `local` for local development; `r2` needs all five values below |
| `R2_ACCOUNT_ID` | Cloudflare account ID | Builds the R2 S3 endpoint |
| `R2_BUCKET_NAME` | dedicated media bucket | Public reads through the domain below |
| `R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY` | bucket-scoped R2 API token keys | Keep in Railway backend variables only |
| `R2_PUBLIC_BASE_URL` | `https://media.example.com` | Custom domain connected to the R2 bucket; no path |
| `MASTERGO_MOCK_OTP` | `1` first, `0` for real SMS | `1` = fixed code, no SMS |
| `MASTERGO_MOCK_OTP_CODE` | `1111` | used only when mock is on |
| `SMS_DRY_RUN` | `0` for real SMS | `1` = log code instead of sending |
| `ESKIZ_EMAIL` / `ESKIZ_PASSWORD` | Eskiz creds | required for real SMS |
| `OTP_SMS_TEMPLATE` | `Opus: tasdiqlash kodi {code}...` | must match Eskiz-approved template |

> **SMS note:** Eskiz only delivers a fixed test string until your sender name
> and template are moderated/approved. Keep `MASTERGO_MOCK_OTP=1` (login with
> code `1111`) for the first test round, then flip to real SMS once approved.

### Cloudflare R2 media

Create a dedicated R2 bucket and a bucket-scoped API token with object read and
write permission. Connect a custom domain to the bucket in Cloudflare R2 bucket
settings. Set the six media variables above on the Railway backend service and
redeploy. The backend writes through R2's S3 API using region `auto`, while
returned media URLs use the custom domain. Django Admin static files stay on
WhiteNoise. Mobile apps continue uploading through the Django API and need no
R2 credentials or configuration.

The custom domain makes uploaded media publicly readable, consistent with the
existing `/media/` URLs. Use this bucket only for media intended for that access
model. The `r2.dev` URL is for development traffic only. Existing local uploads
are not migrated automatically; copy them to the same keys in R2 before enabling
`MASTERGO_MEDIA_STORAGE=r2` if those URLs must keep working. Verify a newly
uploaded avatar, order photo, chat attachment, and portfolio image after deploy.
The backend fails at startup if R2 is selected without complete configuration.

## Connecting the apps

After deploy, the mobile apps are built pointing at this backend:

```powershell
# in the apps repo
.\scripts\build_apks.ps1 `
  -ApiBaseUrl https://<app>.up.railway.app/api `
  -MapboxAccessToken <public-mapbox-token>
```

The WebSocket URL (`wss://…`) is derived automatically.
