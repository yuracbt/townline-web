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

## Mac workflow: test → update → deploy

`tl.sh` wraps the whole cycle (macOS / Linux):

```bash
./tl.sh run      # run locally at http://localhost:5000
./tl.sh update   # pull the newest version from GitHub
./tl.sh deploy   # deploy GitHub's version to the Oracle VM, restart the app
./tl.sh restart  # restart the app on the Oracle VM (no code changes)
./tl.sh ssh      # open a shell on the VM
./tl.sh logs     # show the app's recent log on the VM
```

One-time setup for `deploy`/`ssh`/`logs`: the VM's SSH private key has to live
at `~/.ssh/townline-web-2.key`. Get it from Oracle Console → Cloud Shell:

```bash
cat ~/.ssh/ampere-watch
```

Save that output to `~/.ssh/townline-web-2.key` on your Mac, then
`chmod 600 ~/.ssh/townline-web-2.key`. Note the VM deploys from GitHub, so
`git push` any code changes before `./tl.sh deploy`.

> Full command reference: [COMMANDS.md](COMMANDS.md) — getting updates,
> pushing your changes, deploy, server details, troubleshooting.

## Update, upload, restart

**Update** — get the newest code from GitHub onto your computer:

```bash
cd ~/townline-web
./tl.sh update
```

**Upload** — send your changes to GitHub, then to the Oracle cloud server:

```bash
cd ~/townline-web
git add -A
git commit -m "Describe what you changed"
git push
./tl.sh deploy
```

(The server pulls its code from GitHub, so `git push` first — `deploy` alone
only sends what's already on GitHub.)

**Restart the app:**

- Locally: press **Ctrl+C** in the terminal where it runs, then `./tl.sh run` again.
- On the Oracle cloud server: `./tl.sh restart`

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
