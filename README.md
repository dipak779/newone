# HandleHunt

A web portal for finding available short, one-word usernames (like `@karma`, `@therapy`, `@showoff`)
on **Telegram** and **Instagram**.

- Paste your own words, or generate random ones (length + style: plain / `getkarma` / `karmahq` / mixed)
- Live results stream in per name and platform
- Filter by *any free* / *free on both*, copy hits, export CSV
- Star names to keep them in a saved list and re-scan them later
- **Live finds:** a background hunter checks a new batch of names every minute, around the clock,
  and lists every name that's available on Telegram and/or Instagram (refreshes on the page every 20s)

## The background hunter

When the server starts, it begins working through ~3,000 candidates (the words in `app/words.txt` plus
variants like `getkarma`, `karmahq`). Every minute it checks the next 5 names on both platforms and saves
the results in SQLite (`data/handlehunt.db`). Order of work:

1. names never checked
2. names that were available (re-checked every 6 h so the list stays honest) or blocked (`UNKNOWN`, retried hourly)
3. everything else, re-checked weekly

Names that get taken drop off the Live finds list automatically. Add words to `app/words.txt` to grow the pool.

API: `GET /api/available?platform=telegram|instagram` and `GET /api/hunter` (status).

## Run locally

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload
# open http://localhost:8000   (API docs at /api/docs)
```

Or with Docker:

```bash
docker build -t handlehunt . && docker run -p 8000:8000 handlehunt
```

It deploys to any host that runs a container or Python app (Render, Railway, Fly.io, a VPS).
Use **one** always-on server instance (no "sleep when idle" free tier, or the hunter stops),
and give it a persistent disk mounted at `/srv/data` so found names survive restarts.

## Deploy on Render

1. Create an account at render.com and connect your GitHub (allow access to this repo).
2. Dashboard -> **New** -> **Blueprint** -> pick this repo and the branch with the code.
3. Render reads `render.yaml` and creates the `handlehunt` web service (Starter plan + 1 GB disk). Click **Apply**.
4. After the build, open the `https://handlehunt-xxxx.onrender.com` URL. The Live finds feed fills in within minutes.

Every push to that branch redeploys automatically.

## Settings (environment variables)

| Variable | Default | Meaning |
|---|---|---|
| `MAX_NAMES_PER_SCAN` | 50 | names per scan |
| `CHECKS_PER_HOUR_PER_IP` | 300 | checks per visitor per hour |
| `CACHE_TTL_SECONDS` | 900 | how long results are reused |
| `TELEGRAM_INTERVAL` | 0.7 | seconds between requests to Telegram (whole server) |
| `INSTAGRAM_INTERVAL` | 3.0 | seconds between requests to Instagram (whole server) |
| `HUNT_ENABLED` | 1 | run the background hunter |
| `HUNT_INTERVAL_SECONDS` | 60 | time between hunter rounds |
| `HUNT_BATCH` | 5 | names per round |
| `HUNT_VARIANTS` | 1 | also hunt prefixed/suffixed variants |
| `HUNT_PLATFORMS` | telegram,instagram | platforms the hunter checks |
| `DB_PATH` | data/handlehunt.db | SQLite file |

## Result meanings

| Status | Meaning |
|---|---|
| `AVAILABLE` | No public account found. Usually claimable, but may be reserved or banned. |
| `TAKEN` | A user, bot, channel or group has it. |
| `FRAGMENT` | Telegram name is sold/auctioned on fragment.com. |
| `INVALID` | Breaks platform rules. **Telegram requires 5+ characters**; 4-letter names exist only on Fragment. |
| `UNKNOWN` | Blocked or rate limited; retry later. |

**Telegram** is checked via the public `t.me/<name>` page, then fragment.com.
**Instagram** is checked via its public profile lookup, which rate limits anonymous traffic hard;
from datacenter IPs you may see many `UNKNOWN`s. Always confirm a hit by claiming it in the app.

## Command line

The checker also works without the web UI:

```bash
python -m app.finder showoff manner therapy money --only-available
```

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest tests
```
