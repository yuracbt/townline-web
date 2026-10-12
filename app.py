"""TownLine Web — a multi-user local-news hub portal.

Run:  pip install -r requirements.txt && python app.py
Then open http://localhost:5000

Each user gets fully separate feeds, stories, read/saved state and settings.
A background daemon thread scans every user's enabled feeds on their own
chosen interval (minimum 5 minutes).
"""

import json
import os
import re
import secrets
import sqlite3
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from html.parser import HTMLParser

import feedparser
import requests
from flask import (Flask, flash, g, jsonify, redirect, render_template,
                   request, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.middleware.proxy_fix import ProxyFix

from categorizer import categorize, think_source

# ---------------------------------------------------------------- config

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "townline.db")
DIRECTORY_PATH = os.path.join(BASE_DIR, "feeds_directory.json")
PLACES_PATH = os.path.join(BASE_DIR, "places_directory.json")

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-key-change-me")
# In production the app sits behind nginx (TLS termination); trust the
# proxy's X-Forwarded-* headers so url_for(_external=True) builds https://
# links (password-reset emails) correctly.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1,
                        x_prefix=1)

UA = "TownLineWeb/1.0"
FEED_TIMEOUT = 10  # seconds; be polite to other people's servers

INTERVALS = [
    (5, "Every 5 minutes"),
    (15, "Every 15 minutes"),
    (30, "Every 30 minutes"),
    (60, "Every hour"),
    (120, "Every 2 hours"),
    (240, "Every 4 hours"),
    (480, "Every 8 hours"),
    (720, "Every 12 hours"),
    (1440, "Every day"),
]


def load_directory():
    try:
        with open(DIRECTORY_PATH, encoding="utf-8") as f:
            return json.load(f).get("feeds", [])
    except Exception:
        return []


def load_places_directory():
    try:
        with open(PLACES_PATH, encoding="utf-8") as f:
            return json.load(f).get("places", [])
    except Exception:
        return []


FEED_DIRECTORY = load_directory()
PLACES_DIRECTORY = load_places_directory()

# ---------------------------------------------------------------- db


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            pw_hash TEXT NOT NULL,
            created_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS settings (
            user_id INTEGER PRIMARY KEY,
            town_name TEXT DEFAULT '',
            lat REAL,
            lon REAL,
            scan_interval_min INTEGER DEFAULT 240,
            notify_enabled INTEGER DEFAULT 1,
            last_scan INTEGER DEFAULT 0,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS feeds (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            url TEXT NOT NULL,
            enabled INTEGER DEFAULT 1,
            last_sync INTEGER DEFAULT 0,
            last_count INTEGER DEFAULT 0,
            last_error TEXT,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS stories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            feed_id INTEGER,
            title TEXT NOT NULL,
            url TEXT NOT NULL,
            published INTEGER DEFAULT 0,
            summary TEXT DEFAULT '',
            category TEXT DEFAULT 'News',
            is_read INTEGER DEFAULT 0,
            is_saved INTEGER DEFAULT 0,
            created_at INTEGER NOT NULL,
            UNIQUE(user_id, url),
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_stories_user ON stories(user_id, published DESC);
        CREATE TABLE IF NOT EXISTS places (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            url TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        """
    )
    # Migrations for databases created before these columns existed.
    for ddl in (
        "ALTER TABLE settings ADD COLUMN state TEXT DEFAULT ''",
        "ALTER TABLE settings ADD COLUMN country_code TEXT DEFAULT ''",
        "ALTER TABLE settings ADD COLUMN story_limit INTEGER DEFAULT 200",
        "ALTER TABLE settings ADD COLUMN theme TEXT DEFAULT 'light'",
        "ALTER TABLE users ADD COLUMN email TEXT DEFAULT ''",
        "ALTER TABLE users ADD COLUMN is_blocked INTEGER DEFAULT 0",
    ):
        try:
            db.execute(ddl)
        except sqlite3.OperationalError:
            pass  # column already there
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS password_resets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            token TEXT UNIQUE NOT NULL,
            expires_at INTEGER NOT NULL,
            used INTEGER DEFAULT 0,
            created_at INTEGER NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        )
        """
    )
    # Drop duplicate feeds/places that slipped in before the dedup guards.
    _dedup_table(db, "feeds")
    _dedup_table(db, "places")
    db.commit()
    db.close()


# ---------------------------------------------------------------- helpers

def current_user():
    uid = session.get("user_id")
    if not uid:
        return None
    db = get_db()
    return db.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()


# ---------------------------------------------------------------- auth helpers

ADMIN_USERNAME = "yuriy.chobotok@gmail.com"

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")


def valid_email(addr):
    return bool(addr and EMAIL_RE.match(addr.strip()))


def password_problems(pw):
    """Return a list of unmet strength requirements (empty = strong enough)."""
    problems = []
    if len(pw or "") < 8:
        problems.append("at least 8 characters")
    if not re.search(r"[A-Z]", pw or ""):
        problems.append("at least one capital letter")
    if not re.search(r"[0-9]", pw or ""):
        problems.append("at least one number")
    if not re.search(r"[^A-Za-z0-9]", pw or ""):
        problems.append("at least one special character")
    return problems


# Outgoing mail for password resets. Configure on the server, e.g.
#   SMTP_HOST=smtp.example.com SMTP_PORT=587 SMTP_USER=... SMTP_PASS=... \
#   SMTP_FROM="TownLine <noreply@example.com>"
# Until it is configured, reset links stay in the admin panel so the
# administrator can forward them manually.
SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587") or 587)
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASS = os.environ.get("SMTP_PASS", "")
SMTP_FROM = os.environ.get("SMTP_FROM", "")


def email_configured():
    return bool(SMTP_HOST and SMTP_FROM)


def send_email(to_addr, subject, body):
    """Send a plain-text email via the configured SMTP relay.
    Returns True on success, False when unconfigured or on failure."""
    if not email_configured():
        return False
    import smtplib
    from email.message import EmailMessage
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = SMTP_FROM
    msg["To"] = to_addr
    msg.set_content(body)
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as s:
            s.starttls()
            if SMTP_USER:
                s.login(SMTP_USER, SMTP_PASS)
            s.send_message(msg)
        return True
    except Exception as e:  # noqa: BLE001 - delivery failure is non-fatal
        app.logger.warning("email to %s failed: %s", to_addr, e)
        return False


def send_reset_email(to_addr, reset_link):
    """Send the password-reset link. Returns True on success."""
    return send_email(
        to_addr,
        "TownLine password reset",
        "Someone requested a password reset for your TownLine account.\n\n"
        "Reset your password here (valid for 1 hour):\n"
        f"{reset_link}\n\n"
        "If this was not you, just ignore this message.",
    )


def send_temp_password_email(to_addr, username, temp_password, login_link,
                             reset_link):
    """Notify a user that the admin reset their password. True on success."""
    return send_email(
        to_addr,
        "TownLine: your password was reset",
        f"Hi {username},\n\n"
        "The TownLine administrator has reset your password.\n\n"
        f"Your temporary password is: {temp_password}\n\n"
        f"Log in here: {login_link}\n\n"
        "Or set your own password right away with this one-time link "
        f"(valid for 1 hour):\n{reset_link}\n\n"
        "If this was not expected, contact the administrator.",
    )


def generate_temp_password():
    """One-time temporary password that passes the strength policy."""
    import string
    # 12 chars, guaranteed to include upper, digit and special.
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*-_=+"
    while True:
        pw = "".join(secrets.choice(alphabet) for _ in range(12))
        if not password_problems(pw):
            return pw


def login_required(view):
    from functools import wraps

    @wraps(view)
    def wrapper(*a, **kw):
        user = current_user()
        if not user:
            return redirect(url_for("login"))
        if user["is_blocked"]:
            # Blocked while a session was still alive: kill it.
            session.pop("user_id", None)
            flash("This account has been blocked. Contact the administrator.")
            return redirect(url_for("login"))
        return view(*a, **kw)

    return wrapper


def admin_required(view):
    from functools import wraps

    @wraps(view)
    def wrapper(*a, **kw):
        user = current_user()
        if not user or user["username"] != ADMIN_USERNAME:
            flash("That page is for the administrator only.")
            return redirect(url_for("dashboard"))
        return view(*a, **kw)

    return wrapper


@app.context_processor
def inject_admin():
    is_admin = False
    theme = "light"
    uid = session.get("user_id")
    if uid:
        db = get_db()
        row = db.execute("SELECT username FROM users WHERE id = ?",
                         (uid,)).fetchone()
        is_admin = bool(row and row["username"] == ADMIN_USERNAME)
        trow = db.execute("SELECT theme FROM settings WHERE user_id = ?",
                          (uid,)).fetchone()
        if trow and trow["theme"] in ("light", "dark"):
            theme = trow["theme"]
    return {"is_admin": is_admin, "theme": theme}


def get_settings(user_id):
    db = get_db()
    row = db.execute("SELECT * FROM settings WHERE user_id = ?",
                     (user_id,)).fetchone()
    return dict(row) if row else {}


def rel_time(ts):
    """'2h ago' style age, like the Android app."""
    if not ts:
        return "never"
    delta = int(time.time()) - int(ts)
    if delta < 60:
        return "just now"
    if delta < 3600:
        return f"{delta // 60}m ago"
    if delta < 86400:
        return f"{delta // 3600}h ago"
    return f"{delta // 86400}d ago"


def norm_url(u):
    """Canonical form for duplicate comparison (smart): lowercased,
    scheme dropped, leading www. dropped, default ports dropped,
    query/fragment ignored, no trailing slash.

    So https://www.example.com/feed, http://example.com/feed/ and
    HTTPS://EXAMPLE.COM/feed?utm=x all count as the same source.
    Only used for comparison — the original URL is always stored."""
    u = (u or "").strip()
    if not u:
        return ""
    try:
        p = urllib.parse.urlsplit(u.lower())
        host = p.netloc
        if host.startswith("www."):
            host = host[4:]
        if host.endswith(":80"):
            host = host[:-3]
        elif host.endswith(":443"):
            host = host[:-4]
        path = p.path.rstrip("/") or "/"
        return host + path
    except Exception:
        return u.lower().rstrip("/")


def _dedup_table(db, table):
    """Delete duplicate rows (same user + same normalized URL), keep oldest."""
    rows = db.execute(
        f"SELECT id, user_id, url FROM {table}").fetchall()
    seen, dup_ids = set(), []
    for r in rows:
        key = (r["user_id"], norm_url(r["url"]))
        if key in seen:
            dup_ids.append(r["id"])
        else:
            seen.add(key)
    if dup_ids:
        db.execute(
            f"DELETE FROM {table} WHERE id IN "
            f"({','.join('?' * len(dup_ids))})", dup_ids)


def suggested_for_town(town_name, state="", country_code=""):
    """Directory feeds whose match keys fit the town (Android semantics).

    Keys are town substrings, '*alberta' (Alberta towns), or '*canada'
    (matches every Canadian user).
    """
    t = (town_name or "").lower()
    st = (state or "").lower()
    alberta = ("alberta" in t or ", ab" in t or t.endswith(" ab")
               or st == "alberta")
    out = []
    for f in FEED_DIRECTORY:
        for key in f.get("match", []):
            if key == "*canada":
                hit = True  # directory is Canada-focused; always relevant
            elif key == "*alberta":
                hit = alberta
            elif not key.startswith("*") and key in t:
                hit = True
            else:
                hit = False
            if hit:
                out.append(f)
                break
    return out


def is_working_feed(url):
    """Port of Android's FeedDirectory.isWorkingFeed: only suggest feeds
    that actually serve parseable RSS/Atom right now. One retry smooths
    over transient hiccups (a dead feed fails twice, fast)."""
    for _ in range(2):
        try:
            r = requests.get(url, headers={"User-Agent": UA}, timeout=12)
            if r.status_code != 200:
                continue
            head = r.content[:65536].decode("utf-8", "ignore").lower()
            is_feed = "<rss" in head or "<feed" in head
            has_items = "<item" in head or "<entry" in head
            if is_feed and has_items:
                return True
        except Exception:
            pass
    return False


# ---------------------------------------------------------------- web feed discovery
# "Scan the internet" for feeds covering a town:
#   1. Google News search for the town -> publisher domains behind the stories
#   2. per domain: <link rel=alternate> autodiscovery + feed-ish hrefs in the
#      HTML + conventional feed paths (/feed, /rss, /rss.xml, ...)
#   3. every candidate is verified with is_working_feed before suggesting
# Candidates are cached per town for 24h (discovery changes rarely);
# verification still happens live on every suggestions load.

DISCOVERY_TTL = 24 * 3600
_discovery_cache = {}  # town_lower -> (timestamp, [{"name","url"}, ...])

CONVENTIONAL_FEED_PATHS = ("/feed", "/rss", "/rss.xml", "/feed.xml",
                           "/atom.xml", "/feeds", "/news/feed", "/rss/news")


class _FeedLinkParser(HTMLParser):
    """Collects <link rel=alternate type=rss|atom> hrefs from a page."""

    def __init__(self):
        super().__init__()
        self.feeds = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() != "link":
            return
        d = dict(attrs)
        if (d.get("rel", "").lower() == "alternate"
                and d.get("type", "").lower()
                in ("application/rss+xml", "application/atom+xml")
                and d.get("href")):
            self.feeds.append({"title": d.get("title", ""),
                               "href": d.get("href")})


def _looks_like_feed_url(url):
    """True if the URL's path looks like a feed (segment/extension match —
    not a bare substring, so 'rogerssportsandmedia' doesn't match 'rss')."""
    try:
        path = urllib.parse.urlparse(url).path.lower()
    except Exception:
        return False
    if path.startswith("/wiki/"):
        return False
    if path.endswith((".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg",
                      ".ico", ".mp4", ".mp3", ".pdf")):
        return False
    segs = [s for s in path.split("/") if s]
    if not segs:
        return False
    last = segs[-1]
    if last in ("rss", "feed", "feeds", "atom"):
        return True
    if last.endswith((".rss", ".xml", ".atom")):
        return True
    return any(s in ("rss", "feed", "feeds", "atom") for s in segs)


def _feed_hrefs_in_html(html, base_url):
    """Feed-ish hrefs found anywhere in page HTML (e.g. a '/rss' index link)."""
    out = []
    for m in re.finditer(r'''(?:href|src)\s*=\s*["']([^"']+)["']''',
                         html[:300000], re.IGNORECASE):
        u = m.group(1).strip()
        if not u or u.startswith(("#", "javascript:", "mailto:")):
            continue
        full = urllib.parse.urljoin(base_url, u)
        if _looks_like_feed_url(full) and full not in out:
            out.append(full)
    return out[:12]


def _candidate_feed_urls(domain):
    """Feed URL candidates for a publisher domain (unverified).
    Returns [{"title", "url"}]; title may be "" (derived later)."""
    candidates = []

    def add(url, title=""):
        if url and all(c["url"] != url for c in candidates):
            candidates.append({"title": title, "url": url})

    for scheme in ("https", "http"):
        try:
            r = requests.get(f"{scheme}://{domain}",
                             headers={"User-Agent": UA}, timeout=10)
            if r.status_code != 200:
                continue
            ctype = r.headers.get("Content-Type", "")
            if "html" not in ctype:
                continue
            html = r.text
            parser = _FeedLinkParser()
            try:
                parser.feed(html[:300000])
            except Exception:
                pass
            for f in parser.feeds:
                add(urllib.parse.urljoin(r.url, f["href"]), f["title"])
            # one level deeper: a feed index page (e.g. /rss) may list feeds
            for href in _feed_hrefs_in_html(html, r.url):
                add(href)
                try:
                    r2 = requests.get(href, headers={"User-Agent": UA},
                                      timeout=10)
                    if (r2.status_code == 200
                            and "html" in r2.headers.get("Content-Type", "")):
                        for href2 in _feed_hrefs_in_html(r2.text, r2.url):
                            add(href2)
                except Exception:
                    pass
            break  # https worked; no need to try http
        except Exception:
            continue
    for path in CONVENTIONAL_FEED_PATHS:
        add(f"https://{domain}{path}")
    return candidates[:30]


def _publisher_domains_for_town(town, max_domains=8):
    """Publisher domains behind Google News stories about the town."""
    url = ("https://news.google.com/rss/search?q="
           + urllib.parse.quote(town) + "&hl=en&gl=CA&ceid=CA:en")
    try:
        parsed = fetch_feed(url)
    except Exception:
        return []
    domains = []
    for e in parsed.entries[:40]:
        href = ""
        src = e.get("source") or {}
        if isinstance(src, dict):
            href = src.get("href") or ""
        if not href:
            link = e.get("link") or ""
            if "news.google.com" not in link:
                href = link
        host = urllib.parse.urlparse(href).netloc.lower()
        if host.startswith("www."):
            host = host[4:]
        if host and "news.google.com" not in host and host not in domains:
            domains.append(host)
        if len(domains) >= max_domains:
            break
    return domains


def _pretty_feed_name(title, url):
    """Human name for a discovered feed: the site's own <link> title when
    available, else 'host — Section' derived from the URL path."""
    if title and len(title.strip()) < 80:
        return title.strip()
    host = urllib.parse.urlparse(url).netloc.lower()
    host = host[4:] if host.startswith("www.") else host
    seg = url.rstrip("/").rsplit("/", 1)[-1].lower()
    label = seg.replace("-", " ").replace("_", " ").title()
    if label and label.lower() not in ("feed", "rss", "rss.xml", "feed.xml",
                                       "atom.xml", "atom", "feeds", "index"):
        return f"{host} — {label}"
    return host


def discover_web_feeds(town):
    """Feeds found on the open web for a town (cached 24h, unverified —
    verification happens live in /api/suggestions). Max 6 per domain."""
    key = (town or "").lower().strip()
    if not key:
        return []
    now = time.time()
    hit = _discovery_cache.get(key)
    if hit and now - hit[0] < DISCOVERY_TTL:
        return hit[1]
    found, seen, per_domain = [], set(), {}
    domains = _publisher_domains_for_town(key)
    with ThreadPoolExecutor(max_workers=4) as pool:
        for candidates in pool.map(_candidate_feed_urls, domains):
            for c in candidates:
                url = c["url"]
                domain = urllib.parse.urlparse(url).netloc.lower()
                if domain.startswith("www."):
                    domain = domain[4:]
                norm = norm_url(url)
                if not norm or norm in seen or per_domain.get(domain, 0) >= 6:
                    continue
                seen.add(norm)
                per_domain[domain] = per_domain.get(domain, 0) + 1
                found.append({"name": _pretty_feed_name(c["title"], url),
                              "url": url, "origin": "web"})
                if len(found) >= 30:
                    break
            if len(found) >= 30:
                break
    _discovery_cache[key] = (now, found)
    return found


def suggested_places_for_town(town_name):
    """Curated places (event calendars, libraries, chambers) for the town."""
    t = (town_name or "").lower()
    out = []
    for p in PLACES_DIRECTORY:
        for key in p.get("match", []):
            if not key.startswith("*") and key in t:
                out.append({"name": p["name"], "url": p["url"],
                            "origin": "directory"})
                break
    return out


# ---------------------------------------------------------------- OSM place discovery
# "Scan the internet" for real local places: theatres, galleries, museums,
# libraries, universities, schools, community centres, city halls and shops
# around the user's coordinates, via the OpenStreetMap Overpass API.

OSM_TTL = 24 * 3600
_osm_cache = {}  # town_lower -> (timestamp, [places])

OSM_KIND_LABELS = {
    "theatre": "Theatre", "library": "Library",
    "arts_centre": "Arts centre", "gallery": "Gallery",
    "museum": "Museum", "university": "University",
    "college": "College", "school": "School",
    "community_centre": "Community centre", "townhall": "City hall",
    "attraction": "Attraction",
}

OVERPASS_URLS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
)

# Preferred display order for place kinds (variety first, not 60 libraries).
OSM_KIND_RANK = {
    "theatre": 0, "gallery": 1, "museum": 2, "arts_centre": 3,
    "library": 4, "university": 5, "attraction": 6, "townhall": 7,
    "college": 8, "community_centre": 9,
}


def _osm_kind(tags):
    for key in ("amenity", "tourism", "shop"):
        v = (tags.get(key) or "").lower()
        if v:
            return v
    return ""


def _osm_label(kind):
    if kind in OSM_KIND_LABELS:
        return OSM_KIND_LABELS[kind]
    return kind.replace("_", " ").title() or "Place"


def _osm_place_url(tags, name, town):
    for key in ("website", "contact:website"):
        site = (tags.get(key) or "").strip()
        if site:
            if not site.lower().startswith(("http://", "https://")):
                site = "https://" + site
            return site
    q = urllib.parse.quote(f"{name}, {town}")
    return f"https://www.google.com/maps/search/?api=1&query={q}"


def _overpass(query):
    """Run one Overpass query; [] on any failure."""
    for base in OVERPASS_URLS:
        try:
            r = requests.post(base, data={"data": query},
                              headers={"User-Agent": UA}, timeout=30)
            if r.status_code == 200:
                return r.json().get("elements", [])
        except Exception:
            continue
    return []


def discover_osm_places(lat, lon, town):
    """Live places around (lat, lon) from OpenStreetMap. Cached 24h.

    Two queries: cultural/civic places in 15 km, local shops in 8 km.
    (Schools are deliberately excluded — hundreds of them in any city,
    and they make the query time out without adding visit value.)
    """
    key = (town or "").lower().strip()
    if not key or not lat or not lon:
        return []
    now = time.time()
    hit = _osm_cache.get(key)
    if hit and now - hit[0] < OSM_TTL:
        return hit[1]

    queries = [
        ("[out:json][timeout:20];("
         f"node(around:15000,{lat},{lon})"
         "[amenity~\"^(theatre|library|arts_centre|university|college"
         "|community_centre|townhall)$\"];"
         f"node(around:15000,{lat},{lon})"
         "[tourism~\"^(gallery|museum|attraction)$\"];"
         ");out tags 60;"),
        ("[out:json][timeout:20];("
         f"node(around:8000,{lat},{lon})[shop~\"^(books|art|music|gift)$\"];"
         ");out tags 30;"),
    ]
    elements = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        for els in pool.map(_overpass, queries):
            elements.extend(els)

    found, seen = [], set()
    for el in elements:
        tags = el.get("tags", {}) or {}
        name = (tags.get("name") or "").strip()
        if not name or len(name) > 80:
            continue
        kind = _osm_kind(tags)
        norm = name.lower()
        if norm in seen:
            continue
        seen.add(norm)
        found.append({"name": name, "kind": _osm_label(kind),
                      "url": _osm_place_url(tags, name, town),
                      "origin": "nearby", "_rank": OSM_KIND_RANK.get(kind, 99)})
    found.sort(key=lambda p: (p["_rank"], p["name"].lower()))
    found = [{k: v for k, v in p.items() if k != "_rank"}
             for p in found[:60]]
    _osm_cache[key] = (now, found)
    return found


def seed_default_feeds(user_id, town_name, state="", country_code=""):
    """Seed sensible starting feeds for a town: curated directory matches,
    plus a Google News search for the town name as a generic fallback."""
    db = get_db()
    have = {norm_url(r["url"]) for r in
            db.execute("SELECT url FROM feeds WHERE user_id = ?",
                       (user_id,)).fetchall()}
    added = 0
    for f in suggested_for_town(town_name, state, country_code):
        if norm_url(f["url"]) not in have:
            db.execute(
                "INSERT INTO feeds (user_id, name, url, enabled) VALUES (?,?,?,1)",
                (user_id, f["name"], f["url"]),
            )
            have.add(norm_url(f["url"]))
            added += 1
    if added == 0 and town_name:
        cc = (country_code or "ca").upper()
        gnews = ("https://news.google.com/rss/search?q="
                 + urllib.parse.quote(town_name)
                 + f"&hl=en&gl={cc}&ceid={cc}:en")
        if norm_url(gnews) not in have:
            db.execute(
                "INSERT INTO feeds (user_id, name, url, enabled) VALUES (?,?,?,1)",
                (user_id, f"Google News — {town_name}", gnews),
            )
            added += 1
    db.commit()
    return added


# ---------------------------------------------------------------- scanner

def fetch_feed(url):
    r = requests.get(url, headers={"User-Agent": UA}, timeout=FEED_TIMEOUT)
    r.raise_for_status()
    return feedparser.parse(r.content)


def scan_feed(db, user_id, feed):
    """Scan one feed; returns number of new stories. Never raises."""
    try:
        parsed = fetch_feed(feed["url"])
    except Exception as e:
        db.execute("UPDATE feeds SET last_error = ? WHERE id = ?",
                   (str(e)[:300], feed["id"]))
        db.commit()
        return 0
    if getattr(parsed, "bozo", False) and not parsed.entries:
        db.execute("UPDATE feeds SET last_error = ? WHERE id = ?",
                   ("could not parse feed", feed["id"]))
        db.commit()
        return 0
    new = 0
    now = int(time.time())
    for e in parsed.entries:
        link = (e.get("link") or "").strip()
        if not link:
            continue
        title = (e.get("title") or "Untitled").strip()
        summary = (e.get("summary") or e.get("description") or "").strip()[:2000]
        pp = e.get("published_parsed") or e.get("updated_parsed")
        published = int(time.mktime(pp)) if pp else now
        cat = categorize(feed["name"], feed["url"], title, summary)
        cur = db.execute(
            """INSERT OR IGNORE INTO stories
               (user_id, feed_id, title, url, published, summary, category,
                created_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (user_id, feed["id"], title, link, published, summary, cat, now),
        )
        if cur.rowcount:
            new += 1
    db.execute(
        "UPDATE feeds SET last_sync = ?, last_count = ?, last_error = NULL "
        "WHERE id = ?",
        (now, new, feed["id"]),
    )
    db.commit()
    return new


def scan_user(user_id):
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    try:
        feeds = db.execute(
            "SELECT * FROM feeds WHERE user_id = ? AND enabled = 1",
            (user_id,)).fetchall()
        for feed in feeds:  # one at a time; a bad feed must not kill the loop
            try:
                scan_feed(db, user_id, dict(feed))
            except Exception as e:
                try:
                    db.execute("UPDATE feeds SET last_error = ? WHERE id = ?",
                               (str(e)[:300], feed["id"]))
                    db.commit()
                except Exception:
                    pass
        db.execute("UPDATE settings SET last_scan = ? WHERE user_id = ?",
                   (int(time.time()), user_id))
        db.commit()
    finally:
        db.close()


def scanner_loop():
    """Daemon: every minute, scan each user whose interval has elapsed."""
    while True:
        try:
            db = sqlite3.connect(DB_PATH)
            db.row_factory = sqlite3.Row
            users = db.execute("SELECT id FROM users").fetchall()
            now = int(time.time())
            for u in users:
                s = db.execute(
                    "SELECT scan_interval_min, last_scan FROM settings "
                    "WHERE user_id = ?", (u["id"],)).fetchone()
                if not s:
                    continue
                interval = max(5, s["scan_interval_min"] or 240) * 60
                if now - (s["last_scan"] or 0) >= interval:
                    db.close()  # scan_user opens its own connection
                    scan_user(u["id"])
                    db = sqlite3.connect(DB_PATH)
                    db.row_factory = sqlite3.Row
            db.close()
        except Exception as e:
            print("scanner error:", e)
        time.sleep(60)


# ---------------------------------------------------------------- auth

@app.route("/register", methods=["GET", "POST"])
def register():
    if current_user():
        return redirect(url_for("dashboard"))
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")
        db = get_db()
        pw_problems = password_problems(password)
        if not username or not email or not password:
            error = "Username, email and password are all required."
        elif not valid_email(email):
            error = "That does not look like a valid email address."
        elif db.execute("SELECT id FROM users WHERE username = ?",
                        (username,)).fetchone():
            error = "That username is taken — please pick another."
        elif pw_problems:
            error = "Password needs " + ", ".join(pw_problems) + "."
        else:
            pw_hash = generate_password_hash(password, method="pbkdf2:sha256")
            cur = db.execute(
                "INSERT INTO users (username, email, pw_hash, created_at) "
                "VALUES (?,?,?,?)",
                (username, email, pw_hash, int(time.time())),
            )
            uid = cur.lastrowid
            db.execute("INSERT INTO settings (user_id) VALUES (?)", (uid,))
            db.commit()
            # Blank slate: no town, no feeds. The user picks their location
            # in Settings, which seeds matching feeds for that town.
            session["user_id"] = uid
            return redirect(url_for("settings_page"))
    return render_template("register.html", error=error)


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user():
        return redirect(url_for("dashboard"))
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        db = get_db()
        user = db.execute("SELECT * FROM users WHERE username = ?",
                          (username,)).fetchone()
        if user and check_password_hash(user["pw_hash"], password):
            if user["is_blocked"]:
                error = "This account has been blocked. Contact the administrator."
            else:
                session["user_id"] = user["id"]
                return redirect(url_for("dashboard"))
        else:
            error = "Wrong username or password."
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.pop("user_id", None)
    return redirect(url_for("login"))


# ---------------------------------------------------------------- password recovery

RESET_TOKEN_TTL = 3600  # reset links live for 1 hour


@app.route("/forgot", methods=["GET", "POST"])
def forgot_password():
    if current_user():
        return redirect(url_for("dashboard"))
    message = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        db = get_db()
        user = db.execute("SELECT * FROM users WHERE username = ?",
                          (username,)).fetchone()
        if user and user["email"]:
            token = secrets.token_urlsafe(32)
            db.execute(
                "INSERT INTO password_resets (user_id, token, expires_at, "
                "created_at) VALUES (?,?,?,?)",
                (user["id"], token, int(time.time()) + RESET_TOKEN_TTL,
                 int(time.time())),
            )
            db.commit()
            reset_link = url_for("password_reset", token=token, _external=True)
            send_reset_email(user["email"], reset_link)
        # Same message either way: do not reveal which usernames exist.
        message = ("If that username exists and has an email on file, a "
                   "password-reset link is on its way. It is valid for "
                   "1 hour.")
    return render_template("forgot.html", message=message)


@app.route("/reset/<token>", methods=["GET", "POST"])
def password_reset(token):
    if current_user():
        return redirect(url_for("dashboard"))
    db = get_db()
    row = db.execute(
        "SELECT pr.*, u.username FROM password_resets pr "
        "JOIN users u ON u.id = pr.user_id "
        "WHERE pr.token = ? AND pr.used = 0 AND pr.expires_at > ?",
        (token, int(time.time())),
    ).fetchone()
    if not row:
        return render_template("reset.html", invalid=True)
    error = None
    if request.method == "POST":
        password = request.form.get("password", "")
        confirm = request.form.get("confirm", "")
        pw_problems = password_problems(password)
        if password != confirm:
            error = "The two passwords do not match."
        elif pw_problems:
            error = "Password needs " + ", ".join(pw_problems) + "."
        else:
            db.execute("UPDATE users SET pw_hash = ? WHERE id = ?",
                       (generate_password_hash(password,
                                                method="pbkdf2:sha256"),
                        row["user_id"]))
            db.execute("UPDATE password_resets SET used = 1 WHERE id = ?",
                       (row["id"],))
            db.commit()
            flash("Your password was reset. Log in with the new one.")
            return redirect(url_for("login"))
    return render_template("reset.html", invalid=False, error=error,
                           username=row["username"])


# ---------------------------------------------------------------- administration

@app.route("/admin")
@login_required
@admin_required
def admin_panel():
    db = get_db()
    users = db.execute(
        "SELECT u.*, "
        "(SELECT COUNT(*) FROM stories s WHERE s.user_id = u.id) AS stories, "
        "(SELECT COUNT(*) FROM feeds f WHERE f.user_id = u.id) AS feeds "
        "FROM users u ORDER BY u.created_at DESC"
    ).fetchall()
    tokens = db.execute(
        "SELECT pr.token, pr.expires_at, u.username, u.email "
        "FROM password_resets pr JOIN users u ON u.id = pr.user_id "
        "WHERE pr.used = 0 AND pr.expires_at > ? "
        "ORDER BY pr.created_at DESC",
        (int(time.time()),),
    ).fetchall()
    now = int(time.time())
    user_rows = []
    for u in users:
        d = dict(u)
        d["created_str"] = datetime.fromtimestamp(
            u["created_at"], tz=timezone.utc).strftime("%Y-%m-%d")
        user_rows.append(d)
    token_rows = []
    for t in tokens:
        d = dict(t)
        mins = max(0, (t["expires_at"] - now) // 60)
        d["expires_str"] = f"expires in {mins} min"
        d["link"] = url_for("password_reset", token=t["token"],
                            _external=True)
        token_rows.append(d)
    return render_template("admin.html", users=user_rows, tokens=token_rows,
                           admin_username=ADMIN_USERNAME,
                           email_ok=email_configured(),
                           user=current_user())


@app.route("/admin/block", methods=["POST"])
@login_required
@admin_required
def admin_block():
    target_id = int(request.form.get("user_id", 0) or 0)
    me = current_user()
    db = get_db()
    target = db.execute("SELECT * FROM users WHERE id = ?",
                        (target_id,)).fetchone()
    if not target:
        flash("User not found.")
    elif target["id"] == me["id"]:
        flash("You cannot block your own admin account.")
    else:
        new_state = 0 if target["is_blocked"] else 1
        db.execute("UPDATE users SET is_blocked = ? WHERE id = ?",
                   (new_state, target_id))
        db.commit()
        flash(("Blocked " if new_state else "Unblocked ") +
              target["username"] + ".")
    return redirect(url_for("admin_panel"))


@app.route("/admin/reset_password", methods=["POST"])
@login_required
@admin_required
def admin_reset_password():
    """Admin sets a one-time temporary password for a user and notifies
    them by email (when delivery is configured). The temp password is
    also shown to the admin, since stored passwords are one-way hashes
    and can never be displayed."""
    target_id = int(request.form.get("user_id", 0) or 0)
    db = get_db()
    target = db.execute("SELECT * FROM users WHERE id = ?",
                        (target_id,)).fetchone()
    if not target:
        flash("User not found.")
        return redirect(url_for("admin_panel"))
    temp = generate_temp_password()
    db.execute("UPDATE users SET pw_hash = ? WHERE id = ?",
               (generate_password_hash(temp, method="pbkdf2:sha256"),
                target_id))
    # One-time "set your own password" link, same as the forgot flow.
    token = secrets.token_urlsafe(32)
    db.execute(
        "INSERT INTO password_resets (user_id, token, expires_at, "
        "created_at) VALUES (?,?,?,?)",
        (target_id, token, int(time.time()) + RESET_TOKEN_TTL,
         int(time.time())),
    )
    db.commit()
    emailed = False
    if target["email"]:
        emailed = send_temp_password_email(
            target["email"], target["username"], temp,
            url_for("login", _external=True),
            url_for("password_reset", token=token, _external=True),
        )
    flash(f"Password for {target['username']} reset. "
          f"Temporary password: {temp}"
          + (f" — emailed to {target['email']}."
             if emailed else
             " — email NOT sent (no email on file or delivery "
             "not configured)."))
    return redirect(url_for("admin_panel"))


@app.route("/admin/delete", methods=["POST"])
@login_required
@admin_required
def admin_delete():
    target_id = int(request.form.get("user_id", 0) or 0)
    me = current_user()
    db = get_db()
    target = db.execute("SELECT * FROM users WHERE id = ?",
                        (target_id,)).fetchone()
    if not target:
        flash("User not found.")
    elif target["id"] == me["id"]:
        flash("You cannot delete your own admin account.")
    else:
        # Manual cascade: SQLite foreign keys are not enforced here.
        for table in ("password_resets", "stories", "feeds", "places",
                      "settings"):
            db.execute(f"DELETE FROM {table} WHERE user_id = ?",
                       (target_id,))
        db.execute("DELETE FROM users WHERE id = ?", (target_id,))
        db.commit()
        flash("Deleted " + target["username"] + " and all their data.")
    return redirect(url_for("admin_panel"))


# ---------------------------------------------------------------- dashboard

@app.route("/")
@login_required
def dashboard():
    user = current_user()
    db = get_db()
    settings = get_settings(user["id"])
    active_cat = request.args.get("category", "")
    unread_only = request.args.get("unread") == "1"

    cats = [r["category"] for r in db.execute(
        "SELECT DISTINCT category FROM stories WHERE user_id = ? "
        "ORDER BY category", (user["id"],)).fetchall()]
    # Priority: News first, then alphabetical, Sport and Business last.
    cats = sorted(cats, key=lambda c: (0, "") if c == "News"
                  else ((2, c) if c in ("Sport", "Business") else (1, c)))

    unread_counts = {r["category"]: r["c"] for r in db.execute(
        "SELECT category, COUNT(*) c FROM stories "
        "WHERE user_id = ? AND is_read = 0 GROUP BY category",
        (user["id"],)).fetchall()}
    cat_totals = {r["category"]: r["c"] for r in db.execute(
        "SELECT category, COUNT(*) c FROM stories "
        "WHERE user_id = ? GROUP BY category",
        (user["id"],)).fetchall()}
    total_stories = sum(cat_totals.values())
    unread_total = sum(unread_counts.values())
    unread_saved = db.execute(
        "SELECT COUNT(*) c FROM stories WHERE user_id = ? AND is_read = 0 "
        "AND is_saved = 1", (user["id"],)).fetchone()["c"]

    def chip_url(category=None, unread=False):
        args = {}
        if category:
            args["category"] = category
        if unread:
            args["unread"] = 1
        return url_for("dashboard", **args)

    def page_url(p):
        args = {}
        if active_cat:
            args["category"] = active_cat
        if unread_only:
            args["unread"] = 1
        if p > 1:
            args["page"] = p
        return url_for("dashboard", **args)

    chips = [
        {"label": "All", "url": chip_url(unread=unread_only),
         "count": unread_total,
         "active": not active_cat and not unread_only},
        {"label": "★ Saved",
         "url": chip_url(category="Saved", unread=unread_only),
         "count": unread_saved, "active": active_cat == "Saved"},
    ]
    for c in cats:
        if c == "Saved":
            continue
        chips.append({
            "label": c,
            "url": chip_url(category=c, unread=unread_only),
            "count": unread_counts.get(c, 0),
            "active": active_cat == c,
            "removable": True,
        })

    where = "WHERE s.user_id = ?"
    params = [user["id"]]
    if active_cat == "Saved":
        where += " AND s.is_saved = 1"
    elif active_cat:
        where += " AND s.category = ?"
        params.append(active_cat)
    if unread_only:
        where += " AND s.is_read = 0"
    base = ("SELECT s.*, f.name AS feed_name FROM stories s "
            "LEFT JOIN feeds f ON f.id = s.feed_id " + where)

    # Stories per page (0 = All: everything on one page, no pagination).
    page_size = settings.get("story_limit")
    if page_size is None:
        page_size = 200
    page, total_pages = 1, 1
    if page_size > 0:
        total = db.execute(
            "SELECT COUNT(*) c FROM stories s " + where, params).fetchone()["c"]
        total_pages = max(1, (total + page_size - 1) // page_size)
        try:
            page = int(request.args.get("page", 1))
        except (TypeError, ValueError):
            page = 1
        page = max(1, min(page, total_pages))
        stories = db.execute(
            base + " ORDER BY s.published DESC LIMIT ? OFFSET ?",
            params + [page_size, (page - 1) * page_size]).fetchall()
    else:
        stories = db.execute(
            base + " ORDER BY s.published DESC", params).fetchall()

    return render_template(
        "dashboard.html", user=user, settings=settings, chips=chips,
        active_cat=active_cat, unread=unread_total, unread_only=unread_only,
        stories=stories, rel_time=rel_time, page=page, total_pages=total_pages,
        page_url=page_url, total_stories=total_stories, cat_totals=cat_totals,
        unread_on_url=chip_url(category=active_cat or None, unread=True),
        unread_off_url=chip_url(category=active_cat or None, unread=False),
    )


@app.route("/story/<int:sid>/read")
@login_required
def mark_read(sid):
    db = get_db()
    db.execute("UPDATE stories SET is_read = 1 WHERE id = ? AND user_id = ?",
               (sid, session["user_id"]))
    db.commit()
    if request.headers.get("X-Requested-With") == "fetch":
        return ("", 204)  # instant-read AJAX call: no content needed
    return redirect(request.referrer or url_for("dashboard"))


@app.route("/stories/read_all")
@login_required
def mark_all_read():
    db = get_db()
    db.execute("UPDATE stories SET is_read = 1 WHERE user_id = ?",
               (session["user_id"],))
    db.commit()
    return redirect(url_for("dashboard"))


@app.route("/stories/clear", methods=["POST"])
@login_required
def clear_stories():
    """Delete every story of the current user ("clean news line")."""
    db = get_db()
    n = db.execute("SELECT COUNT(*) c FROM stories WHERE user_id = ?",
                   (session["user_id"],)).fetchone()["c"]
    db.execute("DELETE FROM stories WHERE user_id = ?", (session["user_id"],))
    db.commit()
    flash(f"News line cleaned — {n} {'story' if n == 1 else 'stories'} "
          "removed.")
    return redirect(url_for("dashboard"))


@app.route("/stories/clear_category", methods=["POST"])
@login_required
def clear_category():
    """Delete all of the current user's stories in one category
    ("remove filter tile")."""
    category = request.form.get("category", "")
    db = get_db()
    n = db.execute(
        "SELECT COUNT(*) c FROM stories WHERE user_id = ? AND category = ?",
        (session["user_id"], category)).fetchone()["c"]
    if n:
        db.execute("DELETE FROM stories WHERE user_id = ? AND category = ?",
                   (session["user_id"], category))
        db.commit()
        flash(f"Removed “{category}” — {n} "
              f"{'story' if n == 1 else 'stories'} deleted.")
    return redirect(url_for("dashboard"))


@app.route("/story/<int:sid>/save")
@login_required
def toggle_save(sid):
    db = get_db()
    db.execute(
        "UPDATE stories SET is_saved = 1 - is_saved WHERE id = ? AND user_id = ?",
        (sid, session["user_id"]),
    )
    db.commit()
    return redirect(request.referrer or url_for("dashboard"))


@app.route("/rethink")
@login_required
def rethink():
    """Re-apply the current categorization rules to all stored stories."""
    user = current_user()
    db = get_db()
    rows = db.execute(
        "SELECT s.id, f.name, f.url, s.title, s.summary, s.category "
        "FROM stories s LEFT JOIN feeds f ON f.id = s.feed_id "
        "WHERE s.user_id = ?", (user["id"],)).fetchall()
    n = 0
    for r in rows:
        if r["category"] == "Saved":
            continue  # user-saved links keep their shelf
        db.execute("UPDATE stories SET category = ? WHERE id = ?",
                   (categorize(r["name"], r["url"], r["title"],
                               r["summary"]), r["id"]))
        n += 1
    db.commit()
    return redirect(url_for("dashboard"))


# ---------------------------------------------------------------- feeds

@app.route("/feeds")
@login_required
def feeds_page():
    user = current_user()
    db = get_db()
    settings = get_settings(user["id"])
    feeds = db.execute(
        "SELECT * FROM feeds WHERE user_id = ? ORDER BY name",
        (user["id"],)).fetchall()
    feed_rows = []
    for f in feeds:
        d = dict(f)
        d["thinking"] = think_source(f["name"], f["url"])
        d["sync_info"] = (rel_time(f["last_sync"]) if f["last_sync"]
                          else "not scanned yet")
        feed_rows.append(d)
    have_urls = {f["url"] for f in feeds}
    return render_template("feeds.html", user=user, settings=settings,
                           feeds=feed_rows, rel_time=rel_time)


@app.route("/api/suggestions")
@login_required
def api_suggestions():
    """Feeds suggested for the user's town, verified working right now —
    like the Android app's Discover screen, plus feeds discovered on the
    open web for the town. Excludes already-added feeds."""
    user = current_user()
    db = get_db()
    settings = get_settings(user["id"])
    town = settings.get("town_name", "")
    have = {norm_url(r["url"]) for r in
            db.execute("SELECT url FROM feeds WHERE user_id = ?",
                       (user["id"],)).fetchall()}

    candidates = []
    seen = set()

    def add_candidate(name, url, origin):
        norm = norm_url(url)
        if not norm or norm in seen or norm in have:
            return
        seen.add(norm)
        candidates.append({"name": name, "url": url.strip(), "origin": origin})

    for f in suggested_for_town(town, settings.get("state", ""),
                                settings.get("country_code", "")):
        add_candidate(f["name"], f["url"], "directory")
    for f in discover_web_feeds(town):
        add_candidate(f["name"], f["url"], "web")

    working = []
    seen_names = set()
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = pool.map(is_working_feed, [c["url"] for c in candidates])
    for c, ok in zip(candidates, results):
        if ok and c["name"] not in seen_names:
            seen_names.add(c["name"])
            working.append(c)
    return jsonify({
        "town": town,
        "suggestions": working,
    })


@app.route("/feeds/add", methods=["POST"])
@login_required
def feed_add():
    name = request.form.get("name", "").strip()
    url = request.form.get("url", "").strip()
    added = False
    duplicate = False
    if url:
        if not name:
            name = url
        db = get_db()
        have = {norm_url(r["url"]) for r in
                db.execute("SELECT url FROM feeds WHERE user_id = ?",
                           (session["user_id"],)).fetchall()}
        if norm_url(url) in have:
            duplicate = True
        else:
            db.execute(
                "INSERT INTO feeds (user_id, name, url, enabled) "
                "VALUES (?,?,?,1)",
                (session["user_id"], name, url),
            )
            db.commit()
            added = True
            threading.Thread(target=scan_user, args=(session["user_id"],),
                             daemon=True).start()
    wants_json = (request.headers.get("X-Requested-With") == "fetch"
                  or "application/json" in request.headers.get("Accept", ""))
    if wants_json:
        return jsonify({"ok": added, "duplicate": duplicate,
                        "name": name, "url": url})
    if duplicate:
        flash("That feed is already in your list.")
    return redirect(url_for("feeds_page"))


@app.route("/feeds/<int:fid>/toggle", methods=["POST"])
@login_required
def feed_toggle(fid):
    db = get_db()
    db.execute(
        "UPDATE feeds SET enabled = 1 - enabled "
        "WHERE id = ? AND user_id = ?", (fid, session["user_id"]))
    db.commit()
    return redirect(url_for("feeds_page"))


@app.route("/feeds/<int:fid>/delete", methods=["POST"])
@login_required
def feed_delete(fid):
    db = get_db()
    db.execute("DELETE FROM feeds WHERE id = ? AND user_id = ?",
               (fid, session["user_id"]))
    db.commit()
    return redirect(url_for("feeds_page"))


@app.route("/feeds/scan_now", methods=["POST"])
@login_required
def scan_now():
    session["scan_requested_at"] = int(time.time())
    threading.Thread(target=scan_user, args=(session["user_id"],),
                     daemon=True).start()
    flash("Scanning your feeds — new stories will appear in a moment.", "scan")
    return redirect(request.referrer or url_for("feeds_page"))


@app.route("/api/scan_status")
@login_required
def scan_status():
    """Has the scan requested via 'Scan now' finished yet?"""
    requested = session.get("scan_requested_at", 0)
    if not requested:
        return {"status": "idle"}
    last = get_settings(session["user_id"]).get("last_scan") or 0
    if last >= requested:
        session.pop("scan_requested_at", None)
        return {"status": "done"}
    return {"status": "scanning"}


# ---------------------------------------------------------------- places

@app.route("/places")
@login_required
def places_page():
    user = current_user()
    db = get_db()
    settings = get_settings(user["id"])
    places = db.execute(
        "SELECT * FROM places WHERE user_id = ? ORDER BY name",
        (user["id"],)).fetchall()
    return render_template("places.html", user=user, settings=settings,
                           places=places)


@app.route("/api/places/suggestions")
@login_required
def api_places_suggestions():
    """Places for the user's town: curated directory + live OpenStreetMap
    discovery around their coordinates. Excludes already-saved places."""
    user = current_user()
    db = get_db()
    settings = get_settings(user["id"])
    town = settings.get("town_name", "")
    have = {norm_url(r["url"]) for r in
            db.execute("SELECT url FROM places WHERE user_id = ?",
                       (user["id"],)).fetchall()}
    suggestions, seen = [], set()

    def add(name, url, kind="", origin=""):
        norm = norm_url(url)
        if not norm or norm in seen or norm in have:
            return
        seen.add(norm)
        suggestions.append({"name": name, "url": url.strip(),
                            "kind": kind, "origin": origin})

    for p in suggested_places_for_town(town):
        add(p["name"], p["url"], origin="directory")
    for p in discover_osm_places(settings.get("lat"), settings.get("lon"),
                                 town):
        add(p["name"], p["url"], kind=p["kind"], origin=p["origin"])
    return jsonify({"town": town, "suggestions": suggestions})


@app.route("/places/add", methods=["POST"])
@login_required
def place_add():
    name = request.form.get("name", "").strip()
    url = request.form.get("url", "").strip()
    added = False
    duplicate = False
    if url:
        if not name:
            name = url
        db = get_db()
        have = {norm_url(r["url"]) for r in
                db.execute("SELECT url FROM places WHERE user_id = ?",
                           (session["user_id"],)).fetchall()}
        if norm_url(url) in have:
            duplicate = True
        else:
            db.execute(
                "INSERT INTO places (user_id, name, url) VALUES (?,?,?)",
                (session["user_id"], name, url),
            )
            db.commit()
            added = True
    wants_json = (request.headers.get("X-Requested-With") == "fetch"
                  or "application/json" in request.headers.get("Accept", ""))
    if wants_json:
        return jsonify({"ok": added, "duplicate": duplicate,
                        "name": name, "url": url})
    if duplicate:
        flash("That place is already in your list.")
    return redirect(url_for("places_page"))


@app.route("/places/<int:pid>/delete", methods=["POST"])
@login_required
def place_delete(pid):
    db = get_db()
    db.execute("DELETE FROM places WHERE id = ? AND user_id = ?",
               (pid, session["user_id"]))
    db.commit()
    return redirect(url_for("places_page"))


# ---------------------------------------------------------------- settings

@app.route("/settings", methods=["GET", "POST"])
@login_required
def settings_page():
    user = current_user()
    db = get_db()
    if request.method == "POST":
        interval = int(request.form.get("scan_interval_min", 240))
        notify = 1 if request.form.get("notify_enabled") else 0
        raw_limit = request.form.get("story_limit", 200)
        try:
            story_limit = int(raw_limit)
        except (TypeError, ValueError):
            story_limit = 200
        if story_limit not in (0, 5, 10, 15, 25, 50, 100, 200):
            story_limit = 200  # 0 = All: everything on one page
        db.execute(
            "UPDATE settings SET scan_interval_min = ?, notify_enabled = ?, "
            "story_limit = ? WHERE user_id = ?",
            (interval, notify, story_limit, user["id"]),
        )
        db.commit()
        return redirect(url_for("settings_page"))
    settings = get_settings(user["id"])
    return render_template("settings.html", user=user, settings=settings,
                           intervals=INTERVALS)


@app.route("/settings/theme", methods=["POST"])
@login_required
def settings_theme():
    theme = request.form.get("theme", "light")
    if theme not in ("light", "dark"):
        theme = "light"
    db = get_db()
    db.execute("UPDATE settings SET theme = ? WHERE user_id = ?",
               (theme, session["user_id"]))
    db.commit()
    return redirect(url_for("settings_page"))


@app.route("/settings/account", methods=["POST"])
@login_required
def settings_account():
    user = current_user()
    email = request.form.get("email", "").strip()
    if not valid_email(email):
        flash("That does not look like a valid email address.")
    else:
        db = get_db()
        db.execute("UPDATE users SET email = ? WHERE id = ?",
                   (email, user["id"]))
        db.commit()
        flash("Email address saved.")
    return redirect(url_for("settings_page"))


@app.route("/api/geocode")
@login_required
def api_geocode():
    """Location search via OpenStreetMap Nominatim (free, no key).
    Accepts place names, postal codes, ZIPs — whatever Nominatim returns."""
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify([])
    try:
        r = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": q, "format": "json", "limit": 5,
                    "addressdetails": 1},
            headers={"User-Agent": UA},
            timeout=10,
        )
        r.raise_for_status()
        out = []
        for item in r.json():
            addr = item.get("address", {})
            city = (addr.get("city") or addr.get("town")
                    or addr.get("village") or addr.get("hamlet")
                    or addr.get("municipality") or "")
            out.append({
                "display_name": item.get("display_name", ""),
                "lat": item.get("lat"),
                "lon": item.get("lon"),
                "city": city,
                "state": addr.get("state", ""),
                "country_code": addr.get("country_code", ""),
            })
        return jsonify(out)
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 502


@app.route("/settings/location", methods=["POST"])
@login_required
def set_location():
    """Save the picked Nominatim result as the user's town and seed feeds."""
    user = current_user()
    name = request.form.get("display_name", "").strip()
    city = request.form.get("city", "").strip()
    lat = request.form.get("lat", "").strip()
    lon = request.form.get("lon", "").strip()
    state = request.form.get("state", "")
    country_code = request.form.get("country_code", "")
    if name:
        db = get_db()
        # Prefer the real place name ("Los Angeles") over a bare postal
        # code ("90210, Los Angeles, …" → first part would be the ZIP).
        town = city or name.split(",")[0].strip()
        db.execute(
            "UPDATE settings SET town_name = ?, lat = ?, lon = ?, "
            "state = ?, country_code = ? WHERE user_id = ?",
            (town, float(lat or 0) or None, float(lon or 0) or None,
             state, country_code, user["id"]),
        )
        db.commit()
        seed_default_feeds(user["id"], town, state=state,
                           country_code=country_code)
        threading.Thread(target=scan_user, args=(user["id"],),
                         daemon=True).start()
        flash(f"Town set to {town} — here are feeds we found for it.")
    return redirect(url_for("settings_page"))


@app.route("/settings/locate", methods=["POST"])
@login_required
def locate():
    """'Scan near me': set the town from the browser's geolocation
    (lat/lon) via Nominatim reverse-geocoding, then seed + scan like a
    manual location pick."""
    user = current_user()
    data = request.get_json(force=True, silent=True) or {}
    lat, lon = data.get("lat"), data.get("lon")
    if lat is None or lon is None:
        return jsonify({"ok": False})
    try:
        r = requests.get(
            "https://nominatim.openstreetmap.org/reverse",
            params={"lat": lat, "lon": lon, "format": "json",
                    "addressdetails": 1},
            headers={"User-Agent": UA},
            timeout=10,
        )
        r.raise_for_status()
        item = r.json()
    except Exception:
        return jsonify({"ok": False})
    addr = item.get("address", {}) or {}
    city = (addr.get("city") or addr.get("town") or addr.get("village")
            or addr.get("hamlet") or addr.get("municipality") or "")
    town = city or (item.get("display_name") or "").split(",")[0].strip()
    if not town:
        return jsonify({"ok": False})
    state = addr.get("state", "")
    country_code = addr.get("country_code", "")
    db = get_db()
    db.execute(
        "UPDATE settings SET town_name = ?, lat = ?, lon = ?, state = ?, "
        "country_code = ? WHERE user_id = ?",
        (town, float(lat), float(lon), state, country_code, user["id"]),
    )
    db.commit()
    seed_default_feeds(user["id"], town, state=state,
                       country_code=country_code)
    threading.Thread(target=scan_user, args=(user["id"],),
                     daemon=True).start()
    return jsonify({"ok": True, "town": town})


# ---------------------------------------------------------------- main

@app.context_processor
def inject_helpers():
    return {"rel_time": rel_time}


def start_background():
    """DB + scanner thread. Runs at import so it also works under gunicorn
    (use a single worker: gunicorn --workers 1)."""
    init_db()
    t = threading.Thread(target=scanner_loop, daemon=True)
    t.start()


start_background()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"TownLine Web on http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, debug=False)
