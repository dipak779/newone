# HandleHunt

A web portal for finding available short, one-word usernames (like `@karma`, `@therapy`, `@showoff`)
on **Telegram** and **Instagram**.

- Paste your own words, or generate random ones (length + style: plain / `getkarma` / `karmahq` / mixed)
- Live results stream in per name and platform
- Filter by *any free* / *free on both*, copy hits, export CSV
- Star names to keep them in a saved list and re-scan them later

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
Use one server instance: the cache, throttle and rate limits live in memory.

## Settings (environment variables)

| Variable | Default | Meaning |
|---|---|---|
| `MAX_NAMES_PER_SCAN` | 50 | names per scan |
| `CHECKS_PER_HOUR_PER_IP` | 300 | checks per visitor per hour |
| `CACHE_TTL_SECONDS` | 900 | how long results are reused |
| `TELEGRAM_INTERVAL` | 0.7 | seconds between requests to Telegram (whole server) |
| `INSTAGRAM_INTERVAL` | 3.0 | seconds between requests to Instagram (whole server) |

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
