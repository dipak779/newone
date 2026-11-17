# Username Finder

Checks short, word-style usernames (like `@money`, `@karma`, `@showoff`) on **Telegram** and **Instagram**.
Pure Python 3.9+, no dependencies.

## Usage

```bash
# check specific names
python username_finder.py showoff manner therapy money cash save pleased karma

# check 30 random words (5-7 letters) from words.txt
python username_finder.py --random 30 --min-len 5 --max-len 7

# your own list, Telegram only, show only hits, save to CSV
python username_finder.py --file my_words.txt -p telegram --only-available -o hits.csv
```

Options: `--delay` (seconds between names, default 3), `--seed` (repeatable random picks),
`-o results.json|.csv`, `--no-color`.

## Result meanings

| Status | Meaning |
|---|---|
| `AVAILABLE` | No public account found. Usually claimable, but may be reserved or banned. |
| `TAKEN` | A user, bot, channel or group has it. |
| `FRAGMENT` | Telegram name is sold/auctioned on fragment.com (buy it there, can't just claim it). |
| `INVALID` | Breaks platform rules. **Telegram requires 5+ characters**, so `@cash`, `@save`, `@money`-style 4-letter names only exist via Fragment. |
| `UNKNOWN` | Blocked or rate limited. Raise `--delay` and retry later. |

## How it checks

- **Telegram:** `t.me/<name>` shows a profile title if the name is in use; otherwise fragment.com is checked for auctions/sales.
- **Instagram:** the public `web_profile_info` endpoint returns 404 for unused names. Instagram rate limits
  anonymous requests heavily, so keep the delay at a few seconds; you'll start seeing `UNKNOWN` (429) if you go too fast.

Always confirm a hit by actually trying to set the username in the app.

## Tests

```bash
python -m pytest tests
```
