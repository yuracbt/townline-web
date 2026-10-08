# TownLine Web

A multi-user news-hub web portal — the web sibling of the TownLine Android app.
Each user gets their own local-news line: own feeds, own stories, own read/saved
state, own town and scan settings.

## Run it locally

You need **Python 3.10+** ([python.org/downloads](https://www.python.org/downloads/)).

```bash
git clone https://github.com/yuracbt/townline-web.git
cd townline-web
./run.sh            # macOS / Linux — first run installs everything itself
```

On Windows, double-click `run.bat` (or run it from a terminal). No git?
Download the ZIP from the repo's green **Code** button and unzip it instead.

Then open **http://localhost:5000** — register an account and you're in.
Registration starts blank: set your town in Settings (by name, postal code, or
ZIP) and matching local + regional feeds are seeded. Your first scan starts
immediately; the background scanner then re-scans on your chosen interval
(5/15/30 min, 1/2/4/8/12/24 h).

- Stop the server with Ctrl+C in the terminal.
- Your data lives in `townline.db` next to `app.py` — delete that file to
  start completely fresh.

## Hosting it for free

See **[HOSTING.md](HOSTING.md)** — detailed guide: Cloudflare Quick Tunnel
(5-minute public URL from your own computer, best for testing), Render free
tier (fixed address, sleeps when idle), and Oracle Always Free (always-on).

## Features

- **Accounts** — register / login / logout, session cookies, passwords hashed
  with pbkdf2 (Werkzeug). Every user's content is fully isolated.
- **Location by name, postal code or ZIP** — Settings has a search box powered
  by OpenStreetMap Nominatim (free, no key). Pick a result and it becomes your
  town; matching curated feeds are seeded automatically, with a Google News
  search for the town as a fallback.
- **RSS/Atom scanning** — background daemon thread, one feed at a time,
  10s HTTP timeout, errors recorded per feed instead of killing the loop.
- **Smart categories** — the Android app's `Categorizer` rules ported 1:1 to
  `categorizer.py`: feed-name voting, feed-URL voting (`pravda.com.ua` →
  Ukraine whatever the feed is named), topic/place detection (Calgary,
  Edmonton, Alberta buckets, Events, Business, Sport, Community), home-town
  fallback. Category chips are dynamic — only categories you actually have.
  "↻ Rethink categories" re-applies the rules to all stored stories on demand.
- **Feeds page** — enable/disable, delete, add by URL, per-feed "thinking"
  label (what the categorizer guesses from name + URL), last-sync info,
  one-click "Suggested feeds" matched to your town, "Scan now".
- **Unread tracking** — "N new stories — mark all read" header, per-story
  read, save/unsave (★).

## Project layout

```
app.py               — Flask app: auth, routes, scanner, Nominatim geocoding
categorizer.py       — categorization rules (ported from the Android app)
feeds_directory.json — curated feed directory, matched to towns
templates/           — server-rendered pages (base, login, register,
                       dashboard, feeds, settings)
static/style.css     — light, readable styling, no build step
townline.db          — SQLite database (created on first run)
```

## Notes

- The database file `townline.db` is created next to `app.py` on first run.
- `SECRET_KEY` env var sets the session secret; the default is fine for local
  use only.
- Geocoding and feed fetching go to the public internet; Nominatim's usage
  policy asks for the identifying `User-Agent` the app already sends — don't
  hammer it.
- Google News RSS is used only as a generic fallback feed; it may rate-limit
  heavy use.

## Deploying later (any free Python host)

1. Set `SECRET_KEY` to a long random value in the host's env vars.
2. Use the host's start command equivalent to `python app.py` (or point a WSGI
   server at `app:app` — note the background scanner thread starts in the
   `__main__` block, so for WSGI start it explicitly, e.g. call
   `threading.Thread(target=scanner_loop, daemon=True).start()` after import).
3. Give the app a writable disk for `townline.db`, or point `DB_PATH` at a
   persistent volume. SQLite is fine for a family-scale hub; move to Postgres
   if it ever outgrows that.
4. Free options that run Python: Render, Railway, Fly.io, PythonAnywhere —
   all work with this stack (Flask + SQLite + feedparser, no paid services).
