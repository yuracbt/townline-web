"""TownsLine Web — a multi-user local-news hub portal.

Run:  pip install -r requirements.txt && python app.py
Then open http://localhost:5000

Each user gets fully separate feeds, stories, read/saved state and settings.
A background daemon thread scans every user's enabled feeds on their own
chosen interval (minimum 5 minutes).
"""

import json
import math
import os
import re
import secrets
import hashlib
import sqlite3
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser

import feedparser
import requests
from flask import (Flask, flash, g, jsonify, redirect, render_template,
                   request, Response, session, url_for)
from werkzeug.middleware.proxy_fix import ProxyFix

from categorizer import categorize, think_source

# ---------------------------------------------------------------- config

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "townline.db")
DIRECTORY_PATH = os.path.join(BASE_DIR, "feeds_directory.json")
TOWNS_PATH = os.path.join(BASE_DIR, "towns.json")
PLACES_PATH = os.path.join(BASE_DIR, "places_directory.json")

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-key-change-me")
# In production the app sits behind nginx (TLS termination); trust the
# proxy's X-Forwarded-* headers so url_for(_external=True) builds https://
# links (password-reset emails) correctly.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1,
                        x_prefix=1)

# Canonical host is the apex domain. 301-redirect www -> apex (preserving
# path and query) so every page exists in exactly one copy — otherwise
# Google Search Console flags "Duplicate, Google chose different canonical
# than user" and splits indexing between the two hosts.
@app.before_request
def _www_to_apex():
    host = request.host.split(":")[0].lower()
    if host == "www.townsline.com":
        qs = request.query_string.decode("utf-8", "ignore")
        target = "https://townsline.com" + request.path
        if qs:
            target += "?" + qs
        return redirect(target, code=301)

UA = "TownsLineWeb/1.0"
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


def load_towns():
    try:
        with open(TOWNS_PATH, encoding="utf-8") as f:
            towns = json.load(f).get("towns", [])
        return {t["slug"]: t for t in towns if t.get("slug")}
    except Exception:
        return {}


FEED_DIRECTORY = load_directory()
PLACES_DIRECTORY = load_places_directory()
TOWNS = load_towns()


CONTINENT_BY_CC = {
    "ae": "Asia",
    "af": "Asia",
    "ag": "North America",
    "ai": "North America",
    "al": "Europe",
    "am": "Asia",
    "ao": "Africa",
    "ar": "South America",
    "at": "Europe",
    "au": "Oceania",
    "aw": "North America",
    "az": "Asia",
    "ba": "Europe",
    "bb": "North America",
    "bd": "Asia",
    "be": "Europe",
    "bf": "Africa",
    "bg": "Europe",
    "bh": "Asia",
    "bi": "Africa",
    "bj": "Africa",
    "bl": "North America",
    "bo": "South America",
    "bq": "North America",
    "br": "South America",
    "bs": "North America",
    "bw": "Africa",
    "by": "Europe",
    "bz": "North America",
    "ca": "North America",
    "cd": "Africa",
    "cf": "Africa",
    "cg": "Africa",
    "ch": "Europe",
    "ci": "Africa",
    "cl": "South America",
    "cm": "Africa",
    "cn": "Asia",
    "co": "South America",
    "cr": "North America",
    "cu": "North America",
    "cw": "North America",
    "cz": "Europe",
    "de": "Europe",
    "dj": "Africa",
    "dk": "Europe",
    "dm": "North America",
    "do": "North America",
    "dz": "Africa",
    "ec": "South America",
    "ee": "Europe",
    "eg": "Africa",
    "er": "Africa",
    "es": "Europe",
    "et": "Africa",
    "fi": "Europe",
    "fr": "Europe",
    "ga": "Africa",
    "gb": "Europe",
    "gd": "North America",
    "ge": "Asia",
    "gf": "South America",
    "gh": "Africa",
    "gm": "Africa",
    "gn": "Africa",
    "gp": "North America",
    "gr": "Europe",
    "gt": "North America",
    "gw": "Africa",
    "gy": "South America",
    "hn": "North America",
    "hr": "Europe",
    "ht": "North America",
    "hu": "Europe",
    "id": "Asia",
    "ie": "Europe",
    "il": "Asia",
    "in": "Asia",
    "iq": "Asia",
    "ir": "Asia",
    "is": "Europe",
    "it": "Europe",
    "jm": "North America",
    "jo": "Asia",
    "jp": "Asia",
    "ke": "Africa",
    "kg": "Asia",
    "kh": "Asia",
    "kn": "North America",
    "kp": "Asia",
    "kr": "Asia",
    "kw": "Asia",
    "ky": "North America",
    "kz": "Asia",
    "la": "Asia",
    "lb": "Asia",
    "lc": "North America",
    "lk": "Asia",
    "lr": "Africa",
    "ls": "Africa",
    "lt": "Europe",
    "lv": "Europe",
    "ly": "Africa",
    "ma": "Africa",
    "md": "Europe",
    "me": "Europe",
    "mf": "North America",
    "mg": "Africa",
    "mk": "Europe",
    "ml": "Africa",
    "mm": "Asia",
    "mn": "Asia",
    "mo": "Asia",
    "mq": "North America",
    "mr": "Africa",
    "ms": "North America",
    "mw": "Africa",
    "mx": "North America",
    "my": "Asia",
    "mz": "Africa",
    "na": "Africa",
    "ne": "Africa",
    "ng": "Africa",
    "ni": "North America",
    "nl": "Europe",
    "no": "Europe",
    "np": "Asia",
    "nz": "Oceania",
    "om": "Asia",
    "pa": "North America",
    "pe": "South America",
    "ph": "Asia",
    "pk": "Asia",
    "pl": "Europe",
    "pr": "North America",
    "ps": "Asia",
    "pt": "Europe",
    "py": "South America",
    "qa": "Asia",
    "ro": "Europe",
    "rs": "Europe",
    "rw": "Africa",
    "sa": "Asia",
    "sd": "Africa",
    "se": "Europe",
    "sg": "Asia",
    "si": "Europe",
    "sk": "Europe",
    "sl": "Africa",
    "sn": "Africa",
    "so": "Africa",
    "sr": "South America",
    "ss": "Africa",
    "sv": "North America",
    "sx": "North America",
    "sy": "Asia",
    "tc": "North America",
    "td": "Africa",
    "tg": "Africa",
    "th": "Asia",
    "tj": "Asia",
    "tm": "Asia",
    "tn": "Africa",
    "tr": "Asia",
    "tt": "North America",
    "tw": "Asia",
    "tz": "Africa",
    "ua": "Europe",
    "ug": "Africa",
    "us": "North America",
    "uy": "South America",
    "uz": "Asia",
    "vc": "North America",
    "ve": "South America",
    "vg": "North America",
    "vi": "North America",
    "vn": "Asia",
    "xk": "Europe",
    "ye": "Asia",
    "za": "Africa",
    "zm": "Africa",
    "zw": "Africa",
}
CONTINENT_ORDER = ["North America", "Europe", "Oceania"]


def load_sponsors():
    """All sponsors as dicts (admin list). Kept for symmetry; the live
    surfaces use the filtered helpers below."""
    db = get_db()
    return [dict(r) for r in db.execute(
        "SELECT * FROM sponsors ORDER BY is_active DESC, name").fetchall()]


def town_slug_for(town_name):
    """Match a user's town name to a town-page slug (for sponsors).

    Unicode-aware: 'Львів' -> 'львів'. The old ASCII-only version
    collapsed every non-Latin name to '', which cross-matched
    unrelated towns (a Kyiv user would match a Lviv sponsor)."""
    name = (town_name or "").strip().lower()
    if not name:
        return ""
    for slug, t in TOWNS.items():
        if (t.get("name") or "").lower() == name:
            return slug
    # Fallback: slugify (handles towns not in the directory).
    # \w keeps letters in every script, so non-English names get
    # distinct, stable slugs.
    return re.sub(r"[^\w]+", "-", name, flags=re.UNICODE).strip("-")


def add_months(d, months):
    """Calendar-month arithmetic (Jan 31 + 1 month = Feb 28)."""
    month = d.month - 1 + months
    year = d.year + month // 12
    month = month % 12 + 1
    day = min(d.day, [31, 29 if year % 4 == 0 and (year % 100 != 0
             or year % 400 == 0) else 28, 31, 30, 31, 30,
             31, 31, 30, 31, 30, 31][month - 1])
    return d.replace(year=year, month=month, day=day)


NEWS_MONTHLY_LIMIT = 10  # news items a sponsor may post per billing period
# Fixed reasons an admin can pick when removing a sponsor news item.
REMOVAL_REASONS = [
    "Breaks the listing rules",
    "Offensive content",
    "Not appropriate for a family audience",
    "Spam or misleading",
    "Expired or incorrect information",
    "Other",
]


def news_used_this_period(sponsor_id):
    """News items posted in the sponsor's current billing period."""
    db = get_db()
    row = db.execute("SELECT news_period_start FROM sponsors WHERE id = ?",
                     (sponsor_id,)).fetchone()
    start = row["news_period_start"] if row else 0
    return db.execute(
        "SELECT COUNT(*) c FROM sponsor_news WHERE sponsor_id = ? "
        "AND created_at >= ?", (sponsor_id, start or 0)).fetchone()["c"]


def haversine_km(lat1, lon1, lat2, lon2):
    """Great-circle distance in km between two lat/lon points."""
    r = math.radians
    dlat = r(lat2 - lat1)
    dlon = r(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(r(lat1)) * math.cos(r(lat2)) * math.sin(dlon / 2) ** 2)
    return 6371 * 2 * math.asin(math.sqrt(a))


def get_sponsors_for_town(slug):
    """Active sponsors listed under this town slug (public town page).

    Global (Elite) sponsors are shown on every town page."""
    db = get_db()
    return [dict(r) for r in db.execute(
        "SELECT * FROM sponsors WHERE is_active = 1 "
        "AND (town_slug = ? OR is_global = 1) "
        "ORDER BY name", (slug,)).fetchall()]


def get_sponsors_for_user(settings):
    """Active sponsors for the user's own town, or within the sponsor's
    radius of the user's coordinates — so a user in Calgary or Balzac
    also sees an Airdrie business. Global (Elite) sponsors are shown
    to everyone."""
    db = get_db()
    user_slug = town_slug_for(settings.get("town_name"))
    ulat, ulon = settings.get("lat"), settings.get("lon")
    out = []
    for r in db.execute(
            "SELECT * FROM sponsors WHERE is_active = 1 ORDER BY name"
    ).fetchall():
        d = dict(r)
        if d.get("is_global"):
            out.append(d)
            continue
        if d["town_slug"] == user_slug:
            out.append(d)
            continue
        if (ulat and ulon and d["lat"] and d["lon"] and d["radius_km"]
                and haversine_km(ulat, ulon, d["lat"], d["lon"])
                <= d["radius_km"]):
            out.append(d)
    return out


def sponsor_news_stories_for_user(settings):
    """Sponsor news as story-shaped rows for the Business tile.

    When a business posts news (via their portal link or the admin),
    readers see it as a news item in the Business tile — a row in the
    story list, labeled Sponsored — not a separate promo card.
    """
    db = get_db()
    today = datetime.now(timezone.utc).date().isoformat()
    out = []
    for s in get_sponsors_for_user(settings):
        for n in db.execute(
                "SELECT * FROM sponsor_news WHERE sponsor_id = ? "
                "AND is_active = 1 AND status = 'active' ORDER BY created_at DESC",
                (s["id"],)).fetchall():
            expires = (n["expires"] or "").strip()
            if expires and expires < today:
                continue
            out.append({
                "id": None,
                "title": n["title"],
                "text": n["text"] or "",
                "url": n["url"] or s["url"],
                "feed_name": s["name"],
                "published": n["created_at"],
                "category": "Business",
                "is_read": 1,
                "is_saved": 0,
                "is_sponsor": True,
            })
    return out


def get_sponsor_by_code(code):
    """Sponsor for a self-service portal code, or None (404)."""
    if not code:
        return None
    db = get_db()
    row = db.execute("SELECT * FROM sponsors WHERE code = ?",
                     (code,)).fetchone()
    return dict(row) if row else None


def _news_quota_left(sponsor_id):
    """News items the sponsor may still post this period (0 = at limit)."""
    return max(0, NEWS_MONTHLY_LIMIT - news_used_this_period(sponsor_id))


def send_sponsor_welcome(sponsor):
    """Welcome email for a new sponsor: their personal news-portal link,
    what the listing includes, and the renewal date. Admin in BCC.
    Returns True when the email went out."""
    portal = SITE_URL.rstrip("/") + "/s/" + sponsor["code"]
    period = sponsor.get("billing_period") or 1
    period_word = {1: "month", 3: "3 months", 6: "6 months",
                   12: "1 year"}.get(period, f"{period} months")
    if sponsor.get("is_global"):
        reach_line = ("Your Elite listing is featured on every town page, "
                      "in the Places tab, and your news appears in the "
                      "Business tile for readers everywhere.")
    else:
        reach_line = (f"Your business is now featured on your town's page, "
                      f"in the Places tab, and your news appears in the "
                      f"Business tile for readers in "
                      f"{sponsor.get('town_name') or 'your town'} and nearby.")
    body = (
        f"Hi {sponsor['name']},\n\n"
        f"Welcome to TownsLine! Your featured listing is live.\n\n"
        f"{reach_line}\n\n"
        f"Manage your own news here (keep this link private — anyone "
        f"with it can post news as your business):\n{portal}\n\n"
        f"You can post up to {NEWS_MONTHLY_LIMIT} news items per "
        f"{period_word} billing period.\n\n"
        f"Your listing renews on {sponsor.get('renews_on') or '—'}. "
        f"We'll remind you 3 days before.\n\n"
        f"Questions? Just reply to this email.\n\n— TownsLine"
    )
    return send_email(sponsor["email"],
                      "Your TownsLine listing is live — manage your news here",
                      body, bcc=ADMIN_EMAIL)


def send_sponsor_paused(sponsor, reason):
    """Paused notice: the listing + news are hidden. `reason` is the
    human-readable cause ('No payment', 'Rules violation', free text).
    Admin in BCC."""
    portal = SITE_URL.rstrip("/") + "/s/" + sponsor["code"]
    body = (
        f"Hi {sponsor['name']},\n\n"
        f"Your TownsLine featured listing has been paused.\n\n"
        f"Reason: {reason}.\n\n"
        f"Your listing and your news are hidden for now — they come back "
        f"as soon as the issue is resolved.\n\n"
        f"Your listing was due to renew on "
        f"{sponsor.get('renews_on') or '—'}.\n\n"
        f"Your personal news link (for when you're back):\n{portal}\n\n"
        f"Questions? Just reply to this email.\n\n— TownsLine"
    )
    return send_email(sponsor["email"],
                      "Your TownsLine listing is paused",
                      body, bcc=ADMIN_EMAIL)


def send_sponsor_deleted(sponsor, reason):
    """Deleted-by-request notice. Admin in BCC."""
    body = (
        f"Hi {sponsor['name']},\n\n"
        f"Your TownsLine featured listing has been deleted by request.\n\n"
        f"Reason: {reason}.\n\n"
        f"Your listing and your news are removed.\n\n"
        f"Questions? Just reply to this email.\n\n— TownsLine"
    )
    return send_email(sponsor["email"],
                      "Your TownsLine listing has been deleted",
                      body, bcc=ADMIN_EMAIL)


def send_sponsor_news_added(sponsor, news):
    """News-is-live notice with the item's details. Admin in BCC."""
    if sponsor.get("is_global"):
        reach_line = ("It now shows as a Sponsored row on the Business tile "
                      "for readers everywhere.")
    else:
        reach_line = (f"It now shows as a Sponsored row on the Business tile "
                      f"for readers in "
                      f"{sponsor.get('town_name') or 'your town'} and nearby.")
    body = (
        f"Hi {sponsor['name']},\n\n"
        f"New news is live on your TownsLine listing:\n\n"
        f"Headline: {news['title']}\n"
        f"Text: {news.get('text') or '—'}\n"
        f"Link: {news.get('url') or sponsor['url']}\n"
        f"Expires: {news.get('expires') or 'no expiry'}\n\n"
        f"{reach_line}\n\n"
        f"Questions? Just reply to this email.\n\n— TownsLine"
    )
    return send_email(sponsor["email"],
                      f"Your TownsLine news is live: {news['title']}",
                      body, bcc=ADMIN_EMAIL)


def send_sponsor_news_removed(sponsor, news, reason):
    """Moderation notice: a news item was removed by the admin, with the
    recorded reason. Admin in BCC."""
    body = (
        f"Hi {sponsor['name']},\n\n"
        f"One of your TownsLine news items was removed by our team:\n\n"
        f"Headline: {news['title']}\n"
        f"Reason: {reason}.\n\n"
        f"It no longer shows on TownsLine. If you think this was a "
        f"mistake, just reply to this email and we'll take another look.\n\n"
        f"— TownsLine"
    )
    return send_email(sponsor["email"],
                      f"Your TownsLine news was removed: {news['title']}",
                      body, bcc=ADMIN_EMAIL)


def send_sponsor_changed(sponsor, changes):
    """Info-changed notice listing exactly what changed
    (list of (field, old, new)). Admin in BCC."""
    lines = "\n".join(f"{label}: {old} → {new}"
                      for label, old, new in changes)
    body = (
        f"Hi {sponsor['name']},\n\n"
        f"Your TownsLine listing information was changed:\n\n"
        f"{lines}\n\n"
        f"Questions? Just reply to this email.\n\n— TownsLine"
    )
    return send_email(sponsor["email"],
                      "Your TownsLine listing information changed",
                      body, bcc=ADMIN_EMAIL)


def auto_pause_overdue_sponsors():
    """Hourly: sponsors past their renewal date with no Mark paid get
    paused automatically (listing + news hidden) and emailed. Paused
    rows are never re-selected, so this never double-fires."""
    db = get_db()
    today = datetime.now(timezone.utc).date().isoformat()
    for s in db.execute(
            "SELECT * FROM sponsors WHERE is_active = 1 "
            "AND renews_on != '' AND renews_on < ?", (today,)).fetchall():
        db.execute("UPDATE sponsors SET is_active = 0 WHERE id = ?",
                   (s["id"],))
        db.commit()
        if s["email"]:
            send_sponsor_paused(
                dict(s),
                "No payment (renewal date passed, no payment received)")


def flag_overdue_sponsors(sponsors):
    """Mark active sponsors past their renewal date — the Admin tables
    highlight these rows so unpaid listings stand out."""
    today = datetime.now(timezone.utc).date().isoformat()
    for s in sponsors:
        s["is_overdue"] = bool(
            s.get("is_active") and s.get("renews_on")
            and s["renews_on"] < today)
    return sponsors


# ---------------------------------------------------------------- Pro multi-town subscriptions

PRO_EXTRA_LIMITS = {"pro": 5, "elite": 10}


def pro_enabled(user):
    """Master gate for EVERY Pro UI element (town tiles, towns card,
    grouped feeds, subscription card).

    Admin-only preview for now. At public launch this needs no change:
    any user who ever held Pro/Elite (active or lapsed — lapsed users
    keep the greyed-out towns view) gets the Pro UI; everyone else sees
    today's single-town product byte-for-byte.
    """
    if not user:
        return False
    if user["username"] == ADMIN_USERNAME:
        return True
    return (user["pro_plan"] or "free") in ("pro", "elite")


def pro_extra_limit(plan):
    """Extra towns a paid plan allows (pro: 5, elite: 10, free: 0)."""
    return PRO_EXTRA_LIMITS.get(plan or "free", 0)


def min_scan_interval(user):
    """Minimum background-scan interval (minutes) by plan, closest to
    the promised tiers: free 60, pro 15, elite 5."""
    status, plan, _ = pro_status(user)
    if status == "active":
        return 5 if plan == "elite" else 15
    return 60


def pro_status(user):
    """Subscription status for a user row/dict.

    Returns ('active'|'lapsed', plan, expires_at). 'active' needs
    pro_plan in ('pro','elite') AND pro_expires_at in the future.
    Computed from the DB on every call, so expiry greys out extra
    towns instantly and a renew restores them — no cron needed for
    enforcement.
    """
    plan = (user["pro_plan"] or "free") if user else "free"
    expires_at = int(user["pro_expires_at"] or 0) if user else 0
    active = plan in ("pro", "elite") and expires_at > int(time.time())
    return ("active" if active else "lapsed"), plan, expires_at


def pro_expiry_str(expires_at):
    if not expires_at:
        return "—"
    return datetime.fromtimestamp(
        expires_at, tz=timezone.utc).strftime("%Y-%m-%d")


def ensure_main_town(user_id):
    """Make sure user_towns has a main-town row matching the user's
    settings.town_name (their 'main' town). Returns the row dict or
    None when no town is set yet.

    Also reconciles: if the settings town changed (via the Settings
    picker), the old main row is updated in place — there is only ever
    one main town per user.
    """
    db = get_db()
    s = get_settings(user_id)
    town_name = (s.get("town_name") or "").strip()
    if not town_name:
        row = db.execute(
            "SELECT * FROM user_towns WHERE user_id = ? AND is_main = 1",
            (user_id,)).fetchone()
        return dict(row) if row else None
    slug = town_slug_for(town_name)
    row = db.execute(
        "SELECT * FROM user_towns WHERE user_id = ? AND is_main = 1",
        (user_id,)).fetchone()
    if row and row["town_slug"] != slug:
        # Main town changed in Settings. If the new town is already an
        # extra, promote it (demote the old main); otherwise rename the
        # pinned row in place. Either way there is exactly one main town.
        clash = db.execute(
            "SELECT id FROM user_towns WHERE user_id = ? AND town_slug = ? "
            "AND is_main = 0", (user_id, slug)).fetchone()
        if clash:
            db.execute("UPDATE user_towns SET is_main = 0 WHERE id = ?",
                       (row["id"],))
            db.execute("UPDATE user_towns SET is_main = 1, sort_order = 0, "
                       "lat = ?, lon = ? WHERE id = ?",
                       (s.get("lat"), s.get("lon"), clash["id"]))
        else:
            db.execute(
                "UPDATE user_towns SET town_name = ?, town_slug = ?, "
                "lat = ?, lon = ?, sort_order = 0 WHERE id = ?",
                (town_name, slug, s.get("lat"), s.get("lon"), row["id"]))
        db.commit()
        row = db.execute(
            "SELECT * FROM user_towns WHERE user_id = ? AND is_main = 1",
            (user_id,)).fetchone()
        return dict(row) if row else None
    if row:
        return dict(row)
    try:
        db.execute(
            "INSERT INTO user_towns (user_id, town_name, town_slug, lat, "
            "lon, is_main, sort_order, created_at) VALUES (?,?,?,?,?,1,0,?)",
            (user_id, town_name, slug,
             s.get("lat"), s.get("lon"), int(time.time())))
        db.commit()
    except sqlite3.IntegrityError:
        pass  # raced insert; fall through to the re-read
    row = db.execute(
        "SELECT * FROM user_towns WHERE user_id = ? AND is_main = 1",
        (user_id,)).fetchone()
    return dict(row) if row else None


def get_user_towns(user_id):
    """User's town list: main town pinned first, extras in the user's
    custom sort_order. Ensures the main row exists. Single source of
    truth for dashboard tiles, Settings, Places and feeds grouping."""
    ensure_main_town(user_id)
    db = get_db()
    rows = db.execute(
        "SELECT * FROM user_towns WHERE user_id = ? "
        "ORDER BY is_main DESC, sort_order ASC, created_at ASC",
        (user_id,)).fetchall()
    return [dict(r) for r in rows]


def add_user_town(user_id, town_name, lat=None, lon=None):
    """Add an extra town to a user's list. Rejected unless the
    subscription is active (Yuriy's call 2026-10-16: no town adds while
    lapsed — renew first). Enforces the plan's slot limit. Seeds the
    town's feeds via seed_default_feeds(), tagging the new feed rows
    with town_slug so stories attribute to the right town.
    First town ever (no towns, no settings town): becomes the main town
    automatically. Returns (ok, message)."""
    db = get_db()
    town_name = (town_name or "").strip()
    if not town_name:
        return False, "Town name is required."
    user = db.execute("SELECT * FROM users WHERE id = ?",
                      (user_id,)).fetchone()
    status, plan, _ = pro_status(user)
    if status != "active":
        if plan == "free":
            return False, "Extra towns need a Pro subscription."
        return False, "Subscription lapsed — renew to add extra towns."
    limit = pro_extra_limit(plan)
    towns = get_user_towns(user_id)
    slug = town_slug_for(town_name)
    if any(t["town_slug"] == slug for t in towns):
        return False, f"{town_name} is already in the town list."
    extras = [t for t in towns if not t["is_main"]]
    if len(extras) >= limit:
        return False, (f"Plan limit reached: {limit} extra towns "
                       f"on the {plan.capitalize()} plan.")
    before = {r["id"] for r in db.execute(
        "SELECT id FROM feeds WHERE user_id = ?", (user_id,))}
    max_order = db.execute(
        "SELECT COALESCE(MAX(sort_order), 0) m FROM user_towns "
        "WHERE user_id = ?", (user_id,)).fetchone()["m"]
    # First town ever (no rows and no settings town): it becomes the
    # main town automatically, pinned first.
    is_main = 1 if not towns else 0
    try:
        db.execute(
            "INSERT INTO user_towns (user_id, town_name, town_slug, lat, "
            "lon, is_main, sort_order, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (user_id, town_name, slug, lat, lon, is_main,
             0 if is_main else max_order + 1, int(time.time())))
    except sqlite3.IntegrityError:
        return False, f"{town_name} is already in the town list."
    if is_main:
        db.execute("UPDATE settings SET town_name = ?, lat = ?, lon = ? "
                   "WHERE user_id = ?", (town_name, lat, lon, user_id))
    # Seed feeds for the new town, tagging the fresh feed rows so the
    # Pro news views can attribute stories to this town. Feeds that
    # already existed (URL dedup) keep their previous attribution.
    seed_default_feeds(user_id, town_name, lat=lat, lon=lon)
    for r in db.execute("SELECT id FROM feeds WHERE user_id = ?",
                        (user_id,)):
        if r["id"] not in before:
            db.execute("UPDATE feeds SET town_slug = ? WHERE id = ?",
                       (slug, r["id"]))
    db.commit()
    return True, f"Added {town_name}."


def move_user_town(user_id, town_id, direction):
    """Move an extra town up/down in the user's custom order (swaps
    sort_order with the adjacent extra town). The main town is pinned
    first and can never move. Returns (ok, message)."""
    db = get_db()
    row = db.execute("SELECT * FROM user_towns WHERE id = ? AND user_id = ?",
                     (town_id, user_id)).fetchone()
    if not row:
        return False, "Town not found."
    if row["is_main"]:
        return False, "The main town is pinned and can't be moved."
    extras = [dict(r) for r in db.execute(
        "SELECT * FROM user_towns WHERE user_id = ? AND is_main = 0 "
        "ORDER BY sort_order ASC, created_at ASC", (user_id,)).fetchall()]
    idx = next((i for i, t in enumerate(extras) if t["id"] == town_id), None)
    if idx is None:
        return False, "Town not found."
    j = idx - 1 if direction == "up" else idx + 1
    if j < 0 or j >= len(extras):
        return False, "Already at the edge."
    a, b = extras[idx], extras[j]
    db.execute("UPDATE user_towns SET sort_order = ? WHERE id = ?",
               (b["sort_order"], a["id"]))
    db.execute("UPDATE user_towns SET sort_order = ? WHERE id = ?",
               (a["sort_order"], b["id"]))
    db.commit()
    return True, f"Moved {a['town_name']} {'up' if direction == 'up' else 'down'}."


def reorder_user_towns(user_id, ordered_ids):
    """Persist a full drag-and-drop ordering of a user's towns.
    ordered_ids is the complete list of town row ids, main town first.
    The main town must stay first and can never move. Returns
    (ok, message)."""
    db = get_db()
    towns = [dict(r) for r in db.execute(
        "SELECT * FROM user_towns WHERE user_id = ?", (user_id,)).fetchall()]
    if not towns:
        return False, "No towns to reorder."
    by_id = {t["id"]: t for t in towns}
    try:
        ids = [int(i) for i in ordered_ids]
    except (TypeError, ValueError):
        return False, "Invalid town order."
    if set(ids) != set(by_id) or len(ids) != len(by_id):
        return False, "Town list changed — please reload and try again."
    main = next(t for t in towns if t["is_main"])
    if ids[0] != main["id"]:
        return False, "The main town is pinned first and can't be moved."
    for pos, tid in enumerate(ids):
        db.execute("UPDATE user_towns SET sort_order = ? WHERE id = ?",
                   (pos, tid))
    db.commit()
    return True, "Town order saved."


def make_main_town(user_id, town_id):
    """Make an extra town the main town. The previous main is demoted to
    extra and they swap sort positions, so the visible order stays
    intuitive. Before flipping, untagged feeds/places (added while the
    old town was main, where "untagged" means "belongs to main") are
    pinned to the old town's slug — otherwise they'd silently migrate
    to the new main town. settings.town_name is synced so
    ensure_main_town() never reconciles the flip back. Returns
    (ok, message)."""
    db = get_db()
    row = db.execute("SELECT * FROM user_towns WHERE id = ? AND user_id = ?",
                     (town_id, user_id)).fetchone()
    if not row:
        return False, "Town not found."
    if row["is_main"]:
        return False, "That's already your main town."
    main = db.execute("SELECT * FROM user_towns WHERE user_id = ? "
                      "AND is_main = 1", (user_id,)).fetchone()
    if main and main["town_slug"]:
        # Pin legacy items to the outgoing main town first.
        db.execute("UPDATE feeds SET town_slug = ? WHERE user_id = ? "
                   "AND COALESCE(town_slug, '') = ''",
                   (main["town_slug"], user_id))
        db.execute("UPDATE places SET town_slug = ? WHERE user_id = ? "
                   "AND COALESCE(town_slug, '') = ''",
                   (main["town_slug"], user_id))
    if main:
        db.execute("UPDATE user_towns SET is_main = 0, sort_order = ? "
                   "WHERE id = ?", (row["sort_order"], main["id"]))
    db.execute("UPDATE user_towns SET is_main = 1, sort_order = ? "
               "WHERE id = ?", (main["sort_order"] if main else 0, town_id))
    db.execute("UPDATE settings SET town_name = ?, lat = ?, lon = ? "
               "WHERE user_id = ?",
               (row["town_name"], row["lat"], row["lon"], user_id))
    db.commit()
    return True, f"{row['town_name']} is now your main town."


def remove_user_town(user_id, town_id):
    """Remove an extra town and EVERYTHING in it: its feeds, those
    feeds' stories, and its places. The main town can never be removed.
    Returns (ok, message)."""
    db = get_db()
    row = db.execute("SELECT * FROM user_towns WHERE id = ? AND user_id = ?",
                     (town_id, user_id)).fetchone()
    if not row:
        return False, "Town not found."
    if row["is_main"]:
        return False, "The main town can't be removed."
    slug = row["town_slug"]
    feed_ids = [r["id"] for r in db.execute(
        "SELECT id FROM feeds WHERE user_id = ? AND town_slug = ?",
        (user_id, slug)).fetchall()]
    if feed_ids:
        db.execute(
            "DELETE FROM stories WHERE user_id = ? AND feed_id IN (%s)"
            % ",".join("?" * len(feed_ids)), (user_id, *feed_ids))
        db.execute("DELETE FROM feeds WHERE user_id = ? AND town_slug = ?",
                   (user_id, slug))
    db.execute("DELETE FROM places WHERE user_id = ? AND town_slug = ?",
               (user_id, slug))
    db.execute("DELETE FROM user_towns WHERE id = ?", (town_id,))
    db.commit()
    return True, f"Removed {row['town_name']} and everything in it."


def town_item_counts(user_id, town_slug):
    """(feeds, stories, places) counts for a town — for the removal
    warning dialog."""
    db = get_db()
    n_feeds = db.execute(
        "SELECT COUNT(*) c FROM feeds WHERE user_id = ? AND town_slug = ?",
        (user_id, town_slug)).fetchone()["c"]
    n_stories = db.execute(
        "SELECT COUNT(*) c FROM stories s JOIN feeds f ON f.id = s.feed_id "
        "WHERE s.user_id = ? AND f.town_slug = ?",
        (user_id, town_slug)).fetchone()["c"]
    n_places = db.execute(
        "SELECT COUNT(*) c FROM places WHERE user_id = ? AND town_slug = ?",
        (user_id, town_slug)).fetchone()["c"]
    return n_feeds, n_stories, n_places


def send_pro_renewal_notices():
    """Email Pro/Elite users 3 days before their subscription renews
    (admin in BCC). Idempotent: pro_notice_sent_for records which
    expiry the notice went out for, mirroring the sponsor notices.
    Email failure never breaks the loop."""
    db = get_db()
    now = int(time.time())
    target = now + 3 * 86400
    for u in db.execute(
            "SELECT * FROM users WHERE pro_plan IN ('pro','elite') "
            "AND email != '' AND pro_expires_at >= ? AND "
            "pro_expires_at < ? AND pro_notice_sent_for != pro_expires_at",
            (target, target + 86400)).fetchall():
        d = dict(u)
        _, plan, expires_at = pro_status(d)
        towns = get_user_towns(d["id"])
        kept = ", ".join(t["town_name"] for t in towns) or "your town"
        exp = pro_expiry_str(expires_at)
        body = (
            f"Hi {d['username']},\n\n"
            f"Your TownsLine {plan.capitalize()} renews on {exp} "
            f"— 3 days from now.\n\n"
            f"Your towns stay saved: {kept}.\n\n"
            f"To renew, just reply to this email.\n\n"
            f"— TownsLine"
        )
        if send_email(d["email"],
                      "Your TownsLine Pro renews in 3 days",
                      body, bcc=ADMIN_EMAIL):
            db.execute("UPDATE users SET pro_notice_sent_for = "
                       "pro_expires_at WHERE id = ?", (d["id"],))
    db.commit()


def send_due_digests():
    """Email digests to active subscribers: daily for Elite, weekly for
    Pro (closest to the promised tiers). Idempotent via
    settings.digest_last_sent. Skips users with digests off, lapsed
    plans, or no unread stories. Email failure never breaks the loop."""
    db = get_db()
    now = int(time.time())
    for u in db.execute(
            "SELECT u.id, u.username, u.email, u.pro_plan, u.pro_expires_at, "
            "s.digest_last_sent, s.digest_enabled "
            "FROM users u JOIN settings s ON s.user_id = u.id "
            "WHERE u.pro_plan IN ('pro','elite') AND u.email != ''"
    ).fetchall():
        plan = u["pro_plan"]
        if (u["pro_expires_at"] or 0) <= now:
            continue  # lapsed: no digest
        if not (u["digest_enabled"] if u["digest_enabled"] is not None
                else 1):
            continue
        due_every = 86400 if plan == "elite" else 7 * 86400
        if now - (u["digest_last_sent"] or 0) < due_every:
            continue
        stories = db.execute(
            "SELECT s.title, s.url, f.name AS feed_name, "
            "COALESCE(f.town_slug,'') AS ftown "
            "FROM stories s LEFT JOIN feeds f ON f.id = s.feed_id "
            "WHERE s.user_id = ? AND s.is_read = 0 "
            "ORDER BY s.published DESC LIMIT 10",
            (u["id"],)).fetchall()
        if not stories:
            continue
        names = {t["town_slug"]: t["town_name"] for t in db.execute(
            "SELECT town_slug, town_name FROM user_towns WHERE user_id = ?",
            (u["id"],)).fetchall()}
        lines = []
        for st in stories:
            town = names.get(st["ftown"], "")
            lines.append(f"{'[' + town + '] ' if town else ''}"
                         f"{st['title']}\n{st['url']}")
        kind = "daily" if plan == "elite" else "weekly"
        body = (
            f"Hi {u['username']},\n\n"
            f"Your {kind} TownsLine digest — {len(stories)} unread "
            f"{'story' if len(stories) == 1 else 'stories'}:\n\n"
            + "\n\n".join(lines) +
            "\n\nRead them all at https://townsline.com/\n"
            "Turn off digests any time in Settings → Notifications.\n\n"
            "— TownsLine"
        )
        try:
            ok = send_email(u["email"], "Your TownsLine digest", body)
        except Exception:  # noqa: BLE001 - digest is non-fatal
            ok = False
        if ok:
            db.execute("UPDATE settings SET digest_last_sent = ? "
                       "WHERE user_id = ?", (now, u["id"]))
            db.commit()


def send_renewal_notices():
    """Email sponsors 3 days before their listing renews (admin in BCC).

    Idempotent: notice_sent_for records which renews_on date the notice
    went out for, so a restart or repeat run never double-sends.
    """
    db = get_db()
    target = (datetime.now(timezone.utc).date()
              + timedelta(days=3)).isoformat()
    for s in db.execute(
            "SELECT * FROM sponsors WHERE is_active = 1 AND email != '' "
            "AND renews_on = ? AND notice_sent_for != renews_on",
            (target,)).fetchall():
        portal = SITE_URL.rstrip("/") + "/s/" + s["code"]
        featured_on = ("every town page" if s["is_global"]
                       else "your town's page")
        body = (
            f"Hi {s['name']},\n\n"
            f"Your TownsLine featured listing renews on {s['renews_on']} "
            f"— 3 days from now.\n\n"
            f"Your listing stays live: featured on {featured_on}, in "
            f"the Places tab, and your news on the Business tile.\n\n"
            f"Want to update your news before then? Manage it here:\n"
            f"{portal}\n\n"
            f"To stop the listing, just reply to this email.\n\n"
            f"— TownsLine"
        )
        if send_email(s["email"],
                      "Your TownsLine listing renews in 3 days",
                      body, bcc=ADMIN_EMAIL):
            db.execute("UPDATE sponsors SET notice_sent_for = renews_on "
                       "WHERE id = ?", (s["id"],))
    db.commit()


def renewal_loop():
    """Daemon: check renewal notices + digests + auto-pause overdue
    sponsors hourly (all are idempotent, so repeats and restarts are
    harmless)."""
    while True:
        try:
            with app.app_context():
                send_renewal_notices()
                send_pro_renewal_notices()
                send_due_digests()
                auto_pause_overdue_sponsors()
        except Exception as e:  # noqa: BLE001 - background job never dies
            app.logger.warning("renewal notices failed: %s", e)
        time.sleep(3600)

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
        CREATE TABLE IF NOT EXISTS feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            message TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
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
        CREATE TABLE IF NOT EXISTS sponsors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            tagline TEXT DEFAULT '',
            url TEXT NOT NULL,
            town_slug TEXT NOT NULL DEFAULT '',
            town_name TEXT DEFAULT '',
            lat REAL,
            lon REAL,
            radius_km REAL DEFAULT 30,
            promo_title TEXT DEFAULT '',
            promo_text TEXT DEFAULT '',
            promo_url TEXT DEFAULT '',
            promo_expires TEXT DEFAULT '',
            is_active INTEGER DEFAULT 1,
            created_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sponsor_news (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sponsor_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            text TEXT DEFAULT '',
            url TEXT DEFAULT '',
            expires TEXT DEFAULT '',
            is_active INTEGER DEFAULT 1,
            created_at INTEGER NOT NULL,
            FOREIGN KEY(sponsor_id) REFERENCES sponsors(id) ON DELETE CASCADE
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
        CREATE TABLE IF NOT EXISTS magic_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL,
            token_hash TEXT NOT NULL UNIQUE,
            town_slug TEXT DEFAULT '',
            created_at INTEGER NOT NULL,
            expires_at INTEGER NOT NULL,
            used_at INTEGER DEFAULT 0,
            ip TEXT DEFAULT ''
        )
        """
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_magic_links_hash "
        "ON magic_links(token_hash)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_magic_links_email "
        "ON magic_links(email)"
    )
    # Drop duplicate feeds/places that slipped in before the dedup guards.
    _dedup_table(db, "feeds")
    _dedup_table(db, "places")
    # One-time seed: import sponsors.json into the sponsors table the first
    # time it is empty, so the file-based demo listing survives the move to
    # database-managed sponsors. Later edits happen in the Admin page.
    if db.execute("SELECT COUNT(*) c FROM sponsors").fetchone()["c"] == 0:
        try:
            with open(os.path.join(BASE_DIR, "sponsors.json")) as fh:
                seed = json.load(fh)
        except (OSError, ValueError):
            seed = {}
        for slug, entries in (seed or {}).items():
            town = TOWNS.get(slug, {}) if isinstance(TOWNS, dict) else {}
            for e in entries or []:
                promo = e.get("promo") or {}
                db.execute(
                    "INSERT INTO sponsors (name, tagline, url, town_slug, "
                    "town_name, lat, lon, radius_km, promo_title, promo_text, "
                    "promo_url, promo_expires, is_active, created_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,1,?)",
                    (e.get("name", ""), e.get("tagline", ""), e.get("url", ""),
                     slug, town.get("name", slug), town.get("lat"),
                     town.get("lon"), 30, promo.get("title", ""),
                     promo.get("text", ""), promo.get("url", ""),
                     promo.get("expires", ""), int(time.time())),
                )
    # Moderation: an admin can remove a news item with a recorded reason
    # (soft-remove; the sponsor sees what was removed and why, and the
    # admin can restore it).
    for ddl in (
        "ALTER TABLE sponsor_news ADD COLUMN status TEXT DEFAULT 'active'",
        "ALTER TABLE sponsor_news ADD COLUMN removal_reason TEXT DEFAULT ''",
        "ALTER TABLE sponsor_news ADD COLUMN removed_at INTEGER DEFAULT 0",
    ):
        try:
            db.execute(ddl)
        except sqlite3.OperationalError:
            pass  # column already there
    # Sponsor billing: payment period in months, start of the current
    # news-quota period (unix), and the last date marked paid.
    for ddl in (
        "ALTER TABLE sponsors ADD COLUMN billing_period INTEGER DEFAULT 1",
        "ALTER TABLE sponsors ADD COLUMN news_period_start INTEGER DEFAULT 0",
        "ALTER TABLE sponsors ADD COLUMN last_paid_on TEXT DEFAULT ''",
    ):
        try:
            db.execute(ddl)
        except sqlite3.OperationalError:
            pass  # column already there
    # for renewal notices + the self-service portal), a unique portal code,
    # the date the listing went active, the renewal date, and which renewal
    # date the reminder was already sent for (so it only goes out once).
    for ddl in (
        "ALTER TABLE sponsors ADD COLUMN email TEXT DEFAULT ''",
        "ALTER TABLE sponsors ADD COLUMN code TEXT DEFAULT ''",
        "ALTER TABLE sponsors ADD COLUMN active_since TEXT DEFAULT ''",
        "ALTER TABLE sponsors ADD COLUMN renews_on TEXT DEFAULT ''",
        "ALTER TABLE sponsors ADD COLUMN notice_sent_for TEXT DEFAULT ''",
    ):
        try:
            db.execute(ddl)
        except sqlite3.OperationalError:
            pass  # column already there
    # Global (Elite) sponsors: shown to everyone; town and reach unused.
    for ddl in (
        "ALTER TABLE sponsors ADD COLUMN is_global INTEGER DEFAULT 0",
    ):
        try:
            db.execute(ddl)
        except sqlite3.OperationalError:
            pass  # column already there
    # Pro multi-town subscriptions: plan + expiry on users, per-user news
    # view preference on settings, town attribution on feeds, and the
    # user's town list (main town + paid extra towns).
    for ddl in (
        "ALTER TABLE users ADD COLUMN pro_plan TEXT DEFAULT 'free'",
        "ALTER TABLE users ADD COLUMN pro_expires_at INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN pro_notice_sent_for INTEGER DEFAULT 0",
        "ALTER TABLE settings ADD COLUMN pro_view TEXT DEFAULT 'unified'",
        "ALTER TABLE feeds ADD COLUMN town_slug TEXT DEFAULT ''",
        "ALTER TABLE user_towns ADD COLUMN sort_order INTEGER DEFAULT 0",
        "ALTER TABLE feeds ADD COLUMN show_all INTEGER DEFAULT 0",
        "ALTER TABLE settings ADD COLUMN pro_town_filter TEXT DEFAULT 'all'",
        "ALTER TABLE places ADD COLUMN town_slug TEXT DEFAULT ''",
        # Email digests (G1): daily for Elite, weekly for Pro.
        "ALTER TABLE settings ADD COLUMN digest_last_sent INTEGER DEFAULT 0",
        "ALTER TABLE settings ADD COLUMN digest_enabled INTEGER DEFAULT 1",
        # Founding badge (G4): Kickstarter founders and friends of the project.
        "ALTER TABLE users ADD COLUMN is_founder INTEGER DEFAULT 0",
    ):
        try:
            db.execute(ddl)
        except sqlite3.OperationalError:
            pass  # column already there
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS user_towns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            town_name TEXT NOT NULL,
            town_slug TEXT NOT NULL DEFAULT '',
            lat REAL,
            lon REAL,
            is_main INTEGER DEFAULT 0,
            sort_order INTEGER DEFAULT 0,
            created_at INTEGER NOT NULL,
            UNIQUE(user_id, town_slug),
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        )
        """
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_user_towns_user "
        "ON user_towns(user_id)"
    )
    # Backfill sort_order: main town first (0), extras in creation order.
    for u in db.execute("SELECT DISTINCT user_id FROM user_towns").fetchall():
        n = 1
        for t in db.execute(
                "SELECT id, is_main FROM user_towns WHERE user_id = ? "
                "AND (sort_order IS NULL OR sort_order = 0) "
                "ORDER BY is_main DESC, created_at",
                (u["user_id"],)).fetchall():
            db.execute("UPDATE user_towns SET sort_order = ? WHERE id = ?",
                       (0 if t["is_main"] else n, t["id"]))
            if not t["is_main"]:
                n += 1
    # Fix slugs for non-English town names: the old ASCII-only slugify
    # collapsed every non-Latin name to '', cross-matching unrelated
    # towns. Recompute from the stored town name (ASCII names are
    # unaffected — the new function slugifies them identically).
    for r in db.execute(
            "SELECT id, town_name FROM sponsors WHERE town_name != ''"):
        db.execute("UPDATE sponsors SET town_slug = ? WHERE id = ?",
                   (town_slug_for(r["town_name"]), r["id"]))
    # Backfill: portal codes, active-since / renews dates (monthly billing).
    for r in db.execute(
            "SELECT id, code, created_at, active_since FROM sponsors"
    ).fetchall():
        if not r["code"]:
            while True:
                code = secrets.token_urlsafe(12)
                if not db.execute("SELECT 1 FROM sponsors WHERE code = ?",
                                  (code,)).fetchone():
                    break
            db.execute("UPDATE sponsors SET code = ? WHERE id = ?",
                       (code, r["id"]))
        if not r["active_since"]:
            since = datetime.fromtimestamp(
                r["created_at"], tz=timezone.utc).date()
            renews = since + timedelta(days=30)
            db.execute(
                "UPDATE sponsors SET active_since = ?, renews_on = ? "
                "WHERE id = ?",
                (since.isoformat(), renews.isoformat(), r["id"]))
        # Billing defaults for pre-existing sponsors: monthly period,
        # news quota counted from the day the listing went active.
        db.execute(
            "UPDATE sponsors SET billing_period = COALESCE(NULLIF("
            "billing_period, 0), 1), news_period_start = COALESCE(NULLIF("
            "news_period_start, 0), ?) WHERE id = ?",
            (r["created_at"], r["id"]))
    # One-time migration: the old single promo per sponsor becomes a
    # sponsor_news row, so the Business tile reads one source.
    for s in db.execute(
            "SELECT * FROM sponsors WHERE promo_title != ''").fetchall():
        if not db.execute("SELECT 1 FROM sponsor_news WHERE sponsor_id = ?",
                          (s["id"],)).fetchone():
            db.execute(
                "INSERT INTO sponsor_news (sponsor_id, title, text, url, "
                "expires, is_active, created_at) VALUES (?,?,?,?,?,1,?)",
                (s["id"], s["promo_title"], s["promo_text"],
                 s["promo_url"], s["promo_expires"],
                 int(time.time())),
            )
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
# Where admin notifications go (new account, feedback). Defaults to the
# admin's username, which is an email address in this deployment.
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", ADMIN_USERNAME)

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")


def valid_email(addr):
    return bool(addr and EMAIL_RE.match(addr.strip()))


# ---------------------------------------------------------------- magic-link login
# Passwordless auth: the user's email IS the account. We email a single-use,
# short-lived login link; clicking it creates the session. No passwords.

MAGIC_LINK_TTL = 900  # login links live for 15 minutes
MAGIC_LINK_HOURLY_CAP = 5  # max links per email per hour (anti-spam)
MAGIC_LINK_RESEND_WAIT = 60  # seconds before the same email gets another link


def send_login_link_email(to_addr, link, is_new):
    """Email the one-click login link. Returns True on success."""
    if is_new:
        subject = "Welcome to TownsLine — here's your login link"
        body = (
            "Welcome to TownsLine!\n\n"
            "Your account is ready — click this link to step inside "
            "(valid for 15 minutes):\n"
            f"{link}\n\n"
            "No password needed, ever. Your email is the key to your own "
            "TownsLine.\n\n"
            "If you didn't ask for this, just ignore this message.\n"
            "— TownsLine"
        )
    else:
        subject = "Your TownsLine login link"
        body = (
            "Here's your one-click login link for TownsLine "
            "(valid for 15 minutes):\n"
            f"{link}\n\n"
            "Just click it and you're in — no password needed.\n\n"
            "If you didn't ask for this, just ignore this message.\n"
            "— TownsLine"
        )
    return send_email(to_addr, subject, body)


def username_from_email(email, db):
    """Derive a unique internal username from an email address."""
    local = email.split("@")[0].lower()
    base = re.sub(r"[^a-z0-9]", "", local)[:20] or "reader"
    name, i = base, 2
    while db.execute("SELECT id FROM users WHERE username = ?",
                     (name,)).fetchone():
        name = f"{base}{i}"
        i += 1
    return name


def request_magic_link(email, town_slug="", ip=""):
    """Create and email a login link. Returns (message, sent).

    Always returns the same friendly message — never reveals whether the
    email belongs to an account, and silently honors rate limits.
    """
    db = get_db()
    now = int(time.time())
    email = (email or "").strip().lower()
    if not valid_email(email):
        return "Please enter a valid email address.", False
    sent_msg = ("Check your inbox — we emailed you a login link "
                "(valid 15 minutes).")
    recent = db.execute(
        "SELECT COUNT(*) AS c FROM magic_links WHERE email = ? AND created_at > ?",
        (email, now - 3600)).fetchone()["c"]
    last = db.execute(
        "SELECT created_at FROM magic_links WHERE email = ? "
        "ORDER BY created_at DESC LIMIT 1",
        (email,)).fetchone()
    if recent >= MAGIC_LINK_HOURLY_CAP or (last and now - last["created_at"] < MAGIC_LINK_RESEND_WAIT):
        return sent_msg, True  # rate-limited: stay silent, stay friendly
    user = db.execute("SELECT * FROM users WHERE email = ? "
                      "ORDER BY id DESC LIMIT 1", (email,)).fetchone()
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    db.execute(
        "INSERT INTO magic_links (email, token_hash, town_slug, created_at, "
        "expires_at, ip) VALUES (?,?,?,?,?,?)",
        (email, token_hash, town_slug or "", now, now + MAGIC_LINK_TTL,
         ip or ""))
    db.commit()
    link = url_for("magic_login", token=token, _external=True)
    if not send_login_link_email(email, link, user is None):
        # Email not configured (local dev): log the link so it isn't lost.
        app.logger.warning("magic link for %s: %s", email, link)
    return sent_msg, True


def apply_town_to_user(uid, town, db):
    """Set the user's town, seed its feeds + places, and start a scan."""
    db.execute(
        "UPDATE settings SET town_name = ?, lat = ?, lon = ?, "
        "state = ?, country_code = ? WHERE user_id = ?",
        (town["name"], town.get("lat"), town.get("lon"),
         town.get("region", ""), town.get("country_code", ""), uid))
    seed_default_feeds(uid, town["name"], state=town.get("region", ""),
                       country_code=town.get("country_code", ""),
                       lat=town.get("lat"), lon=town.get("lon"))
    seed_places_for_town(uid, town["name"])
    db.commit()
    threading.Thread(target=scan_user, args=(uid,), daemon=True).start()


# Outgoing mail for password resets. Configure on the server, e.g.
#   SMTP_HOST=smtp.example.com SMTP_PORT=587 SMTP_USER=... SMTP_PASS=... \
#   SMTP_FROM="TownsLine <noreply@example.com>"
# Until it is configured, reset links stay in the admin panel so the
# administrator can forward them manually.
SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587") or 587)
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASS = os.environ.get("SMTP_PASS", "")
SMTP_FROM = os.environ.get("SMTP_FROM", "")
# Public base URL, used for links inside emails (renewal notices).
SITE_URL = os.environ.get("SITE_URL", "https://townsline.com")


def email_configured():
    return bool(SMTP_HOST and SMTP_FROM)


def send_email(to_addr, subject, body, bcc=None):
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
    if bcc:
        msg["Bcc"] = bcc
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


def notify_admin(subject, body):
    """Email the admin (new account, feedback). Never raises; delivery
    failure is logged by send_email and the user flow continues."""
    try:
        send_email(ADMIN_EMAIL, subject, body)
    except Exception as e:  # noqa: BLE001 - admin notice is non-fatal
        app.logger.warning("admin notify failed: %s", e)


# ---------------------------------------------------------------- magic-link login
# (helpers live above, next to send_email)


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


def pro_required(view):
    """Gate for Pro self-service routes (towns management, feed flags).
    Mirrors pro_enabled(): the admin always, plus anyone who ever held
    Pro/Elite. Mutating routes additionally require an active plan
    where it matters (add_user_town enforces it itself).
    """
    from functools import wraps

    @wraps(view)
    def wrapper(*a, **kw):
        user = current_user()
        if not pro_enabled(user):
            flash("That page is for the administrator only.")
            return redirect(url_for("dashboard"))
        return view(*a, **kw)

    return wrapper


@app.context_processor
def inject_admin():
    is_admin = False
    is_founder = False
    theme = "light"
    uid = session.get("user_id")
    if uid:
        db = get_db()
        row = db.execute("SELECT username, is_founder FROM users WHERE id = ?",
                         (uid,)).fetchone()
        is_admin = bool(row and row["username"] == ADMIN_USERNAME)
        try:
            is_founder = bool(row and row["is_founder"])
        except (IndexError, KeyError):
            is_founder = False
        trow = db.execute("SELECT theme FROM settings WHERE user_id = ?",
                          (uid,)).fetchone()
        if trow and trow["theme"] in ("light", "dark"):
            theme = trow["theme"]
    try:
        css_version = int(os.path.getmtime(
            os.path.join(BASE_DIR, "static", "style.css")))
    except OSError:
        css_version = 0
    return {"is_admin": is_admin, "is_founder": is_founder, "theme": theme,
            "css_version": css_version}


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
_osm_cache = {}  # cache_key -> (timestamp, [places])


def _osm_key(town_slug, town_name):
    """Cache key for OSM discovery. Pro towns use their stable slug;
    anything else falls back to the town name (previous behavior)."""
    if town_slug:
        return "slug:" + town_slug
    return "name:" + (town_name or "").lower().strip()


def _osm_fresh(key):
    """Fresh cached OSM results, or None when never scanned / stale."""
    hit = _osm_cache.get(key)
    if hit and time.time() - hit[0] < OSM_TTL:
        return hit[1]
    return None

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


def discover_osm_places(lat, lon, town, cache_key=None):
    """Live places around (lat, lon) from OpenStreetMap. Cached 24h.

    Two queries: cultural/civic places in 15 km, local shops in 8 km.
    (Schools are deliberately excluded — hundreds of them in any city,
    and they make the query time out without adding visit value.)
    """
    key = cache_key or (town or "").lower().strip()
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


# ---------------------------------------------------------------- town feeds

# Per-category RSS feeds auto-generated for any town (Google News
# searches): Local, Business, Art, Education, Entertainment, News.
TOWN_FEED_CATEGORIES = [
    ("Local", ""),
    ("Business", "business"),
    ("Art", "art"),
    ("Education", "education"),
    ("Entertainment", "entertainment"),
    ("News", "news"),
]


def category_feeds_for_town(town_name, country_code=""):
    """Per-category Google News RSS feeds for a town."""
    name = (town_name or "").strip()
    if not name:
        return []
    cc = (country_code or "US").upper()
    feeds = []
    for label, extra in TOWN_FEED_CATEGORIES:
        q = name + (" " + extra if extra else "")
        url = ("https://news.google.com/rss/search?q="
               + urllib.parse.quote(q)
               + f"&hl=en&gl={cc}&ceid={cc}:en")
        feeds.append({"name": f"{name} — {label}", "url": url})
    return feeds


def find_town_entry(town_name="", lat=None, lon=None):
    """Best towns.json entry for a town: exact slug/name match first,
    otherwise the nearest town by coordinates."""
    if town_name:
        slug = town_slug_for(town_name)
        if slug in TOWNS:
            return TOWNS[slug]
        low = town_name.strip().lower()
        for t in TOWNS.values():
            if t.get("name", "").lower() == low:
                return t
    if lat is not None and lon is not None:
        best, best_d = None, None
        for t in TOWNS.values():
            tlat, tlon = t.get("lat"), t.get("lon")
            if tlat is None or tlon is None:
                continue
            d = haversine_km(lat, lon, tlat, tlon)
            if best_d is None or d < best_d:
                best, best_d = t, d
        return best
    return None


def seed_places_for_town(user_id, town_name):
    """Preload curated directory places for the town into the user's
    Places tab. Returns the number added."""
    db = get_db()
    slug = town_slug_for(town_name)
    have = {norm_url(r["url"]) for r in
            db.execute("SELECT url FROM places WHERE user_id = ?",
                       (user_id,)).fetchall()}
    added = 0
    for p in suggested_places_for_town(town_name):
        if norm_url(p["url"]) not in have:
            db.execute(
                "INSERT INTO places (user_id, name, url, town_slug) "
                "VALUES (?,?,?,?)",
                (user_id, p["name"], p["url"], slug),
            )
            have.add(norm_url(p["url"]))
            added += 1
    db.commit()
    return added


def seed_default_feeds(user_id, town_name, state="", country_code="",
                       lat=None, lon=None):
    """Seed sensible starting feeds for a town, from the towns database:
    the matched town's curated feeds (exact match, else the nearest town
    by coordinates), per-category RSS for the user's own town name, plus
    curated directory matches. A Google News search for the town name is
    the generic fallback when nothing else matched.

    Returns (feeds_added, matched_town_name_or_None)."""
    db = get_db()

    def feed_key(url):
        # Google News searches differ only by query string — keep it, or
        # all per-category feeds would dedupe to one (norm_url drops
        # the query, which is right for ordinary feeds).
        u = (url or "").strip().lower()
        if "news.google.com/rss" in u:
            return u
        return norm_url(url)

    have = {feed_key(r["url"]) for r in
            db.execute("SELECT url FROM feeds WHERE user_id = ?",
                       (user_id,)).fetchall()}
    added = 0

    def add_feed(name, url):
        nonlocal added
        if url and feed_key(url) not in have:
            db.execute(
                "INSERT INTO feeds (user_id, name, url, enabled) "
                "VALUES (?,?,?,1)",
                (user_id, name, url),
            )
            have.add(feed_key(url))
            added += 1

    entry = find_town_entry(town_name, lat, lon)
    entry_cc = (entry.get("country_code") if entry else "") or ""
    if entry:
        for f in entry.get("feeds", []):
            add_feed(f.get("name") or entry.get("name"), f.get("url"))
    # Per-category RSS always reflects the user's own town name, even
    # when the curated feeds came from the nearest database town.
    for f in category_feeds_for_town(
            town_name or (entry.get("name") if entry else ""),
            country_code or entry_cc):
        add_feed(f["name"], f["url"])
    for f in suggested_for_town(town_name, state, country_code):
        add_feed(f.get("name"), f.get("url"))
    if added == 0 and town_name:
        cc = (country_code or entry_cc or "CA").upper()
        gnews = ("https://news.google.com/rss/search?q="
                 + urllib.parse.quote(town_name)
                 + f"&hl=en&gl={cc}&ceid={cc}:en")
        add_feed(f"Google News — {town_name}", gnews)
    db.commit()
    return added, (entry.get("name") if entry else None)


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


def scan_user(user_id, feed_ids=None):
    """Scan a user's enabled feeds. When feed_ids is given, only those
    feeds are scanned (per-town "Scan {Town}")."""
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    try:
        if feed_ids is None:
            feeds = db.execute(
                "SELECT * FROM feeds WHERE user_id = ? AND enabled = 1",
                (user_id,)).fetchall()
        elif not feed_ids:
            feeds = []
        else:
            feeds = db.execute(
                "SELECT * FROM feeds WHERE user_id = ? AND enabled = 1 "
                "AND id IN (%s)" % ",".join("?" * len(feed_ids)),
                (user_id, *feed_ids)).fetchall()
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
                # Plan floor on scan frequency: free 60 min, pro 15, elite 5.
                prow = db.execute(
                    "SELECT pro_plan, pro_expires_at FROM users WHERE id = ?",
                    (u["id"],)).fetchone()
                pplan = (prow["pro_plan"] or "free") if prow else "free"
                pexp = int(prow["pro_expires_at"] or 0) if prow else 0
                pactive = pplan in ("pro", "elite") and pexp > now
                floor = 5 if (pactive and pplan == "elite") else \
                    (15 if pactive else 60)
                interval = max(floor, s["scan_interval_min"] or 240) * 60
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
    # Town prepopulation: arriving from a town page (?town=<slug>) signs
    # the user up with that town's feeds and places already loaded.
    town = None
    if request.method == "GET":
        town = TOWNS.get(request.args.get("town", "").strip())
    message = None
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        town_slug = request.form.get("town_slug", "").strip()
        town = TOWNS.get(town_slug)
        message, _ = request_magic_link(email, town_slug,
                                        request.remote_addr or "")
    return render_template("register.html", town=town, message=message)


@app.route("/auth/<token>")
def magic_login(token):
    """One-click login from an emailed magic link. Single-use, 15 minutes."""
    if current_user():
        return redirect(url_for("dashboard"))
    db = get_db()
    now = int(time.time())
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    row = db.execute(
        "SELECT * FROM magic_links WHERE token_hash = ? AND used_at = 0 "
        "AND expires_at > ?",
        (token_hash, now)).fetchone()
    if not row:
        return render_template(
            "login.html",
            error="That login link is invalid or expired — "
                  "request a fresh one below.",
            town_count=f"{len(TOWNS):,}")
    db.execute("UPDATE magic_links SET used_at = ? WHERE id = ?",
               (now, row["id"]))
    # Match by email OR username: the admin account's username IS its email
    # address, and older accounts may have an empty email field.
    user = db.execute("SELECT * FROM users WHERE email = ? OR username = ? "
                      "ORDER BY id DESC LIMIT 1",
                      (row["email"], row["email"])).fetchone()
    if user and user["is_blocked"]:
        db.commit()
        return render_template(
            "login.html",
            error="This account has been blocked. Contact the administrator.",
            town_count=f"{len(TOWNS):,}")
    is_new = user is None
    if is_new:
        username = username_from_email(row["email"], db)
        cur = db.execute(
            "INSERT INTO users (username, email, pw_hash, created_at) "
            "VALUES (?,?,?,?)",
            (username, row["email"], "", now))
        uid = cur.lastrowid
        db.execute("INSERT INTO settings (user_id) VALUES (?)", (uid,))
    else:
        uid = user["id"]
        username = user["username"]
        if not user["email"]:
            # Backfill: older accounts may have no email on file.
            db.execute("UPDATE users SET email = ? WHERE id = ?",
                       (row["email"], uid))
    town = TOWNS.get(row["town_slug"] or "")
    if town:
        apply_town_to_user(uid, town, db)
    db.commit()
    if is_new:
        notify_admin(
            f"New TownsLine account: {username}",
            f"A new user just registered on TownsLine.\n\n"
            f"Username: {username}\nEmail: {row['email']}\n",
        )
        if town:
            flash(f"Welcome! {town['name']} news, feeds and places "
                  f"are all set up for you.")
        else:
            flash("Welcome to TownsLine! Pick your town in Settings to "
                  "personalize your inbox.")
    session["user_id"] = uid
    return redirect(url_for("dashboard"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user():
        return redirect(url_for("dashboard"))
    message = None
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        message, _ = request_magic_link(email, "",
                                        request.remote_addr or "")
    return render_template("login.html", message=message,
                           town_count=f"{len(TOWNS):,}")


@app.route("/feedback", methods=["GET", "POST"])
@login_required
def feedback_page():
    user = current_user()
    if request.method == "POST":
        message = (request.form.get("message") or "").strip()
        if not message:
            flash("Please write something before sending.")
        else:
            db = get_db()
            db.execute(
                "INSERT INTO feedback (user_id, message, created_at) "
                "VALUES (?,?,?)",
                (user["id"], message[:2000], int(time.time())),
            )
            db.commit()
            notify_admin(
                f"TownsLine feedback from {user['username']}",
                f"Feedback from {user['username']} ({user['email'] or 'no email'}):\n\n"
                f"{message[:2000]}\n",
            )
            flash("Thanks for the feedback!")
            return redirect(url_for("dashboard"))
    return render_template("feedback.html", user=user)


@app.route("/logout")
def logout():
    session.pop("user_id", None)
    return redirect(url_for("login"))


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
    now = int(time.time())
    user_rows = []
    for u in users:
        d = dict(u)
        d["created_str"] = datetime.fromtimestamp(
            u["created_at"], tz=timezone.utc).strftime("%Y-%m-%d")
        # Towns for the expandable detail row: all of the user's towns
        # (main first), from user_towns — the single source of truth.
        towns = [{"label": t.get("town_name") or t["town_slug"],
                  "main": bool(t.get("is_main"))}
                 for t in get_user_towns(u["id"])]
        d["towns"] = towns
        user_rows.append(d)
    feedback_rows = []
    for f in db.execute(
        "SELECT f.message, f.created_at, u.username, u.email "
        "FROM feedback f JOIN users u ON u.id = f.user_id "
        "ORDER BY f.created_at DESC LIMIT 50"
    ).fetchall():
        d = dict(f)
        d["created_str"] = datetime.fromtimestamp(
            f["created_at"], tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
        feedback_rows.append(d)
    sponsor_rows = [dict(r) for r in db.execute(
        "SELECT s.*, (SELECT COUNT(*) FROM sponsor_news n "
        "WHERE n.sponsor_id = s.id AND n.is_active = 1) AS news_count "
        "FROM sponsors s ORDER BY s.is_active DESC, s.name").fetchall()]
    flag_overdue_sponsors(sponsor_rows)
    return render_template("admin.html", users=user_rows,
                           feedback=feedback_rows, sponsors=sponsor_rows,
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


@app.route("/admin/send_login_link", methods=["POST"])
@login_required
@admin_required
def admin_send_login_link():
    """Admin emails a one-click login link to a user (e.g. they're locked
    out or never got theirs). Same magic link as the login page."""
    target_id = int(request.form.get("user_id", 0) or 0)
    db = get_db()
    target = db.execute("SELECT * FROM users WHERE id = ?",
                        (target_id,)).fetchone()
    if not target or not target["email"]:
        flash("User not found or has no email on file.")
    else:
        request_magic_link(target["email"], "", request.remote_addr or "")
        flash(f"Login link sent to {target['email']}.")
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
        for table in ("stories", "feeds", "places",
                      "settings"):
            db.execute(f"DELETE FROM {table} WHERE user_id = ?",
                       (target_id,))
        db.execute("DELETE FROM users WHERE id = ?", (target_id,))
        db.commit()
        flash("Deleted " + target["username"] + " and all their data.")
    return redirect(url_for("admin_panel"))


@app.route("/admin/sponsors")
@login_required
@admin_required
def admin_sponsors():
    """Sponsors sub-page: full list + Add sponsor form."""
    db = get_db()
    sponsors = [dict(r) for r in db.execute(
        "SELECT s.*, (SELECT COUNT(*) FROM sponsor_news n "
        "WHERE n.sponsor_id = s.id AND n.is_active = 1) AS news_count "
        "FROM sponsors s ORDER BY s.is_active DESC, s.name").fetchall()]
    flag_overdue_sponsors(sponsors)
    return render_template("admin_sponsors.html", sponsors=sponsors,
                           user=current_user())


@app.route("/admin/sponsors/<int:sid>")
@login_required
@admin_required
def admin_sponsor_detail(sid):
    """One sponsor: edit fields, portal link, and manage its news."""
    db = get_db()
    row = db.execute("SELECT * FROM sponsors WHERE id = ?",
                     (sid,)).fetchone()
    if not row:
        flash("Sponsor not found.")
        return redirect(url_for("admin_sponsors"))
    news = [dict(r) for r in db.execute(
        "SELECT * FROM sponsor_news WHERE sponsor_id = ? "
        "ORDER BY created_at DESC", (sid,)).fetchall()]
    portal_url = SITE_URL.rstrip("/") + "/s/" + row["code"]
    return render_template("admin_sponsor_detail.html",
                           sponsor=dict(row), news=news,
                           portal_url=portal_url,
                           news_used=news_used_this_period(sid),
                           news_limit=NEWS_MONTHLY_LIMIT,
                           removal_reasons=REMOVAL_REASONS,
                           user=current_user())


def _sponsor_coords(town, slug):
    """lat/lon for a sponsor town: directory first, else geocode."""
    if slug in TOWNS:
        return TOWNS[slug].get("lat"), TOWNS[slug].get("lon")
    try:
        r = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": town, "format": "json", "limit": 1},
            headers={"User-Agent": UA}, timeout=10)
        item = (r.json() or [None])[0]
        if item:
            return float(item["lat"]), float(item["lon"])
    except Exception:
        pass
    return None, None


def _form_float(value):
    """Float from a form field, or None when blank/invalid."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


@app.route("/admin/sponsors/add", methods=["POST"])
@login_required
@admin_required
def admin_sponsor_add():
    """Add a sponsor — live immediately, no deploy. Email is required:
    renewal notices and the self-service portal link go there."""
    name = request.form.get("name", "").strip()
    tagline = request.form.get("tagline", "").strip()
    url = request.form.get("url", "").strip()
    town = request.form.get("town", "").strip()
    email = request.form.get("email", "").strip()
    is_global = request.form.get("is_global") == "on"
    try:
        radius = float(request.form.get("radius_km", "30") or 30)
    except ValueError:
        radius = 30.0
    if not name or not url:
        flash("Name and website are required.")
        return redirect(url_for("admin_sponsors"))
    if not is_global and not town:
        flash("Town is required unless the sponsor is Global.")
        return redirect(url_for("admin_sponsors"))
    if not email or not valid_email(email):
        flash("A valid sponsor email is required.")
        return redirect(url_for("admin_sponsors"))
    try:
        billing_period = int(request.form.get("billing_period", "1") or 1)
    except ValueError:
        billing_period = 1
    if billing_period not in (1, 3, 6, 12):
        billing_period = 1
    if is_global:
        slug, town, lat, lon, radius = "", "", None, None, 0
    else:
        slug = town_slug_for(town)
        flat = _form_float(request.form.get("town_lat"))
        flon = _form_float(request.form.get("town_lon"))
        if flat is not None and flon is not None:
            lat, lon = flat, flon  # picked from autocomplete — exact coords
        else:
            lat, lon = _sponsor_coords(town, slug)
    today = datetime.now(timezone.utc).date()
    code = secrets.token_urlsafe(12)
    db = get_db()
    cur = db.execute(
        "INSERT INTO sponsors (name, tagline, url, town_slug, town_name, "
        "lat, lon, radius_km, email, code, active_since, renews_on, "
        "billing_period, news_period_start, last_paid_on, "
        "is_global, is_active, created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?)",
        (name, tagline, url, slug, town, lat, lon, radius, email, code,
         today.isoformat(),
         add_months(today, billing_period).isoformat(),
         billing_period, int(time.time()), today.isoformat(),
         1 if is_global else 0, int(time.time())),
    )
    db.commit()
    sponsor = dict(db.execute("SELECT * FROM sponsors WHERE id = ?",
                              (cur.lastrowid,)).fetchone())
    emailed = send_sponsor_welcome(sponsor)
    flash(f"Sponsor {name} added — live now."
          + ("" if emailed else " (welcome email could not be sent)"))
    return redirect(url_for("admin_sponsor_detail", sid=cur.lastrowid))


@app.route("/admin/sponsors/<int:sid>/edit", methods=["POST"])
@login_required
@admin_required
def admin_sponsor_edit(sid):
    """Edit a sponsor's fields (name, tagline, URL, town, reach, email,
    active-since and renewal dates)."""
    db = get_db()
    row = db.execute("SELECT * FROM sponsors WHERE id = ?",
                     (sid,)).fetchone()
    if not row:
        flash("Sponsor not found.")
        return redirect(url_for("admin_sponsors"))
    name = request.form.get("name", "").strip()
    tagline = request.form.get("tagline", "").strip()
    url = request.form.get("url", "").strip()
    town = request.form.get("town", "").strip()
    email = request.form.get("email", "").strip()
    active_since = request.form.get("active_since", "").strip()
    renews_on = request.form.get("renews_on", "").strip()
    is_global = request.form.get("is_global") == "on"
    try:
        radius = float(request.form.get("radius_km", "30") or 30)
    except ValueError:
        radius = 30.0
    try:
        billing_period = int(request.form.get("billing_period", "1") or 1)
    except ValueError:
        billing_period = 1
    if billing_period not in (1, 3, 6, 12):
        billing_period = 1
    if not name or not url:
        flash("Name and website are required.")
        return redirect(url_for("admin_sponsor_detail", sid=sid))
    if not is_global and not town:
        flash("Town is required unless the sponsor is Global.")
        return redirect(url_for("admin_sponsor_detail", sid=sid))
    if not email or not valid_email(email):
        flash("A valid sponsor email is required.")
        return redirect(url_for("admin_sponsor_detail", sid=sid))
    if is_global:
        slug, town, lat, lon, radius = "", "", None, None, 0
    else:
        slug = town_slug_for(town)
        flat = _form_float(request.form.get("town_lat"))
        flon = _form_float(request.form.get("town_lon"))
        if flat is not None and flon is not None:
            lat, lon = flat, flon  # picked from autocomplete — exact coords
        elif slug != row["town_slug"]:
            lat, lon = _sponsor_coords(town, slug)
        else:
            lat, lon = row["lat"], row["lon"]
    # What exactly changed — the business gets a notice listing only
    # the fields that actually changed.
    period_labels = {1: "Monthly", 3: "Every 3 months",
                     6: "Every 6 months", 12: "Yearly"}
    changes = []
    for label, old, new in (
        ("Name", row["name"], name),
        ("Description", row["tagline"] or "", tagline),
        ("Website", row["url"], url),
        ("Town", row["town_name"] or "", town),
        ("Global", "Yes" if row["is_global"] else "No",
         "Yes" if is_global else "No"),
        ("Reach (km)", str(row["radius_km"]), str(radius)),
        ("Email", row["email"] or "", email),
        ("Active since", row["active_since"] or "", active_since),
        ("Renews", row["renews_on"] or "", renews_on),
        ("Payment period",
         period_labels.get(row["billing_period"] or 1, "?"),
         period_labels.get(billing_period, "?")),
    ):
        if str(old) != str(new):
            changes.append((label, old or "—", new or "—"))
    db.execute(
        "UPDATE sponsors SET name = ?, tagline = ?, url = ?, "
        "town_slug = ?, town_name = ?, lat = ?, lon = ?, radius_km = ?, "
        "email = ?, active_since = ?, renews_on = ?, billing_period = ?, "
        "is_global = ? WHERE id = ?",
        (name, tagline, url, slug, town, lat, lon, radius, email,
         active_since, renews_on, billing_period,
         1 if is_global else 0, sid),
    )
    db.commit()
    if not changes:
        flash("No changes made.")
        return redirect(url_for("admin_sponsor_detail", sid=sid))
    updated = db.execute("SELECT * FROM sponsors WHERE id = ?",
                         (sid,)).fetchone()
    emailed = (send_sponsor_changed(dict(updated), changes)
               if updated["email"] else False)
    flash(f"Sponsor {name} updated."
          + (" Change notice email sent." if emailed else
             " (no email on file — no notice sent)"))
    return redirect(url_for("admin_sponsor_detail", sid=sid))


@app.route("/admin/sponsors/<int:sid>/mark_paid", methods=["POST"])
@login_required
@admin_required
def admin_sponsor_mark_paid(sid):
    """Record that the business paid for its next period: the renewal
    date moves forward by the billing period and the news allowance
    (10 per period) resets."""
    db = get_db()
    row = db.execute("SELECT * FROM sponsors WHERE id = ?",
                     (sid,)).fetchone()
    if not row:
        flash("Sponsor not found.")
        return redirect(url_for("admin_sponsors"))
    period = row["billing_period"] or 1
    today = datetime.now(timezone.utc).date()
    try:
        base = date.fromisoformat(row["renews_on"] or "")
    except ValueError:
        base = today
    new_renews = add_months(max(base, today), period)
    db.execute(
        "UPDATE sponsors SET renews_on = ?, news_period_start = ?, "
        "last_paid_on = ?, notice_sent_for = '' WHERE id = ?",
        (new_renews.isoformat(), int(time.time()), today.isoformat(), sid),
    )
    db.commit()
    flash(f"Marked paid — {row['name']} renews on {new_renews.isoformat()}, "
          f"news allowance reset (0/{NEWS_MONTHLY_LIMIT}).")
    return redirect(url_for("admin_sponsor_detail", sid=sid))


@app.route("/admin/sponsors/<int:sid>/pause", methods=["GET", "POST"])
@login_required
@admin_required
def admin_sponsor_pause(sid):
    """Pause with a reason: No payment / Rules violation / Other
    (free text). The business gets a paused email naming the reason."""
    db = get_db()
    row = db.execute("SELECT * FROM sponsors WHERE id = ?",
                     (sid,)).fetchone()
    if not row:
        flash("Sponsor not found.")
        return redirect(url_for("admin_sponsors"))
    if not row["is_active"]:
        flash("Already paused.")
        return redirect(url_for("admin_sponsors"))
    if request.method == "POST":
        kind = request.form.get("reason", "")
        other = request.form.get("other", "").strip()
        if kind == "payment":
            reason = "No payment"
        elif kind == "violation":
            reason = "Rules violation"
        elif kind == "other":
            if not other:
                flash("Please enter a reason.")
                return render_template("admin_sponsor_pause.html",
                                       sponsor=dict(row),
                                       user=current_user())
            reason = other
        else:
            flash("Please choose a reason.")
            return render_template("admin_sponsor_pause.html",
                                   sponsor=dict(row),
                                   user=current_user())
        db.execute("UPDATE sponsors SET is_active = 0 WHERE id = ?",
                   (sid,))
        db.commit()
        emailed = (send_sponsor_paused(dict(row), reason)
                   if row["email"] else False)
        flash(f"Paused {row['name']}."
              + (" Notice email sent." if emailed else
                 " (no email on file — no notice sent)"))
        return redirect(url_for("admin_sponsors"))
    return render_template("admin_sponsor_pause.html",
                           sponsor=dict(row), user=current_user())


@app.route("/admin/sponsors/<int:sid>/resume", methods=["POST"])
@login_required
@admin_required
def admin_sponsor_resume(sid):
    """Resume a paused sponsor — listing + news visible again."""
    db = get_db()
    row = db.execute("SELECT * FROM sponsors WHERE id = ?",
                     (sid,)).fetchone()
    if row:
        db.execute("UPDATE sponsors SET is_active = 1 WHERE id = ?",
                   (sid,))
        db.commit()
        flash(f"Resumed {row['name']}.")
    return redirect(url_for("admin_sponsors"))


@app.route("/admin/sponsors/<int:sid>/delete", methods=["GET", "POST"])
@login_required
@admin_required
def admin_sponsor_delete(sid):
    """Two-step delete: first confirm on the page (prevents accidents),
    then a free-text reason — the business gets a deleted-by-request
    email naming the reason."""
    db = get_db()
    row = db.execute("SELECT * FROM sponsors WHERE id = ?",
                     (sid,)).fetchone()
    if not row:
        flash("Sponsor not found.")
        return redirect(url_for("admin_sponsors"))
    if request.method == "POST":
        reason = request.form.get("reason", "").strip()
        if not reason:
            flash("Please enter a reason for the deletion.")
            return render_template("admin_sponsor_delete.html",
                                   sponsor=dict(row), show_reason=True,
                                   user=current_user())
        db.execute("DELETE FROM sponsor_news WHERE sponsor_id = ?", (sid,))
        db.execute("DELETE FROM sponsors WHERE id = ?", (sid,))
        db.commit()
        if row["email"]:
            send_sponsor_deleted(dict(row), reason)
        flash("Deleted sponsor " + row["name"] + ".")
        return redirect(url_for("admin_sponsors"))
    show_reason = request.args.get("reason") == "1"
    return render_template("admin_sponsor_delete.html",
                           sponsor=dict(row), show_reason=show_reason,
                           user=current_user())


@app.route("/admin/sponsors/<int:sid>/regenerate", methods=["POST"])
@login_required
@admin_required
def admin_sponsor_regenerate(sid):
    """Issue a fresh portal code (invalidates the old portal link)."""
    db = get_db()
    row = db.execute("SELECT * FROM sponsors WHERE id = ?",
                     (sid,)).fetchone()
    if row:
        while True:
            code = secrets.token_urlsafe(12)
            if not db.execute("SELECT 1 FROM sponsors WHERE code = ?",
                              (code,)).fetchone():
                break
        db.execute("UPDATE sponsors SET code = ? WHERE id = ?",
                   (code, sid))
        db.commit()
        flash("New portal link issued — the old one no longer works.")
    return redirect(url_for("admin_sponsor_detail", sid=sid))


@app.route("/admin/sponsors/<int:sid>/news/add", methods=["POST"])
@login_required
@admin_required
def admin_sponsor_news_add(sid):
    """Add a news item for a sponsor — appears on the Business tile."""
    db = get_db()
    if not db.execute("SELECT 1 FROM sponsors WHERE id = ?",
                      (sid,)).fetchone():
        flash("Sponsor not found.")
        return redirect(url_for("admin_sponsors"))
    title = request.form.get("title", "").strip()
    text = request.form.get("text", "").strip()
    url = request.form.get("url", "").strip()
    expires = request.form.get("expires", "").strip()
    if not title:
        flash("News headline is required.")
        return redirect(url_for("admin_sponsor_detail", sid=sid))
    if _news_quota_left(sid) <= 0:
        flash(f"News limit reached ({NEWS_MONTHLY_LIMIT} per billing "
              f"period). It resets when the business pays again.")
        return redirect(url_for("admin_sponsor_detail", sid=sid))
    db.execute(
        "INSERT INTO sponsor_news (sponsor_id, title, text, url, expires, "
        "is_active, created_at) VALUES (?,?,?,?,?,1,?)",
        (sid, title, text, url, expires, int(time.time())),
    )
    db.commit()
    sponsor = db.execute("SELECT * FROM sponsors WHERE id = ?",
                         (sid,)).fetchone()
    if sponsor and sponsor["email"]:
        send_sponsor_news_added(
            dict(sponsor),
            {"title": title, "text": text, "url": url, "expires": expires})
    flash("News added — live on the Business tile.")
    return redirect(url_for("admin_sponsor_detail", sid=sid))


@app.route("/admin/sponsors/news/<int:nid>/edit", methods=["POST"])
@login_required
@admin_required
def admin_sponsor_news_edit(nid):
    """Edit one news item (headline, text, link, expiry)."""
    db = get_db()
    row = db.execute("SELECT * FROM sponsor_news WHERE id = ?",
                     (nid,)).fetchone()
    if not row:
        flash("News item not found.")
        return redirect(url_for("admin_sponsors"))
    title = request.form.get("title", "").strip()
    text = request.form.get("text", "").strip()
    url = request.form.get("url", "").strip()
    expires = request.form.get("expires", "").strip()
    if not title:
        flash("News headline is required.")
        return redirect(url_for("admin_sponsor_detail",
                                sid=row["sponsor_id"]))
    db.execute("UPDATE sponsor_news SET title = ?, text = ?, url = ?, "
               "expires = ? WHERE id = ?",
               (title, text, url, expires, nid))
    db.commit()
    flash("News updated.")
    return redirect(url_for("admin_sponsor_detail", sid=row["sponsor_id"]))


@app.route("/admin/sponsors/news/<int:nid>/toggle", methods=["POST"])
@login_required
@admin_required
def admin_sponsor_news_toggle(nid):
    """Hide / show one news item without deleting it."""
    db = get_db()
    row = db.execute("SELECT * FROM sponsor_news WHERE id = ?",
                     (nid,)).fetchone()
    if row:
        db.execute("UPDATE sponsor_news SET is_active = ? WHERE id = ?",
                   (0 if row["is_active"] else 1, nid))
        db.commit()
        flash(("Hidden " if row["is_active"] else "Shown ")
              + row["title"] + ".")
        return redirect(url_for("admin_sponsor_detail",
                                sid=row["sponsor_id"]))
    return redirect(url_for("admin_sponsors"))


@app.route("/admin/sponsors/news/<int:nid>/remove", methods=["POST"])
@login_required
@admin_required
def admin_sponsor_news_remove(nid):
    """Moderated removal: hides the item everywhere, records why, and
    emails the sponsor with the reason (admin BCC'd). The item can be
    restored; the sponsor sees what was removed and why in their portal."""
    db = get_db()
    row = db.execute("SELECT * FROM sponsor_news WHERE id = ?",
                     (nid,)).fetchone()
    if not row:
        return redirect(url_for("admin_sponsors"))
    reason = request.form.get("reason", "Other").strip()
    if reason not in REMOVAL_REASONS:
        reason = "Other"
    note = request.form.get("reason_note", "").strip()
    full_reason = reason + (f" — {note}" if note else "")
    db.execute("UPDATE sponsor_news SET status = 'removed', is_active = 0, "
               "removal_reason = ?, removed_at = ? WHERE id = ?",
               (full_reason, int(time.time()), nid))
    db.commit()
    sponsor = db.execute("SELECT * FROM sponsors WHERE id = ?",
                         (row["sponsor_id"],)).fetchone()
    if sponsor and sponsor["email"]:
        send_sponsor_news_removed(sponsor, row, full_reason)
    flash(f"Removed news: {row['title']} ({full_reason}).")
    return redirect(url_for("admin_sponsor_detail",
                            sid=row["sponsor_id"]))


@app.route("/admin/sponsors/news/<int:nid>/restore", methods=["POST"])
@login_required
@admin_required
def admin_sponsor_news_restore(nid):
    """Undo a moderated removal."""
    db = get_db()
    row = db.execute("SELECT * FROM sponsor_news WHERE id = ?",
                     (nid,)).fetchone()
    if row:
        db.execute("UPDATE sponsor_news SET status = 'active', is_active = 1, "
                   "removal_reason = '', removed_at = 0 WHERE id = ?", (nid,))
        db.commit()
        flash(f"Restored news: {row['title']}.")
        return redirect(url_for("admin_sponsor_detail",
                                sid=row["sponsor_id"]))
    return redirect(url_for("admin_sponsors"))


# ---------------------------------------------------------------- Pro multi-town subscriptions (admin-only)


@app.route("/admin/pro")
@login_required
@admin_required
def admin_pro():
    """Admin: every user with their Pro plan, status, expiry and towns."""
    db = get_db()
    rows = db.execute(
        "SELECT u.*, "
        "(SELECT COUNT(*) FROM user_towns t WHERE t.user_id = u.id "
        "AND t.is_main = 0) AS extras, "
        "(SELECT town_name FROM user_towns t WHERE t.user_id = u.id "
        "AND t.is_main = 1) AS main_town "
        "FROM users u ORDER BY u.created_at DESC").fetchall()
    users = []
    for u in rows:
        d = dict(u)
        status, plan, expires_at = pro_status(d)
        d["pro_status"] = status
        d["pro_plan"] = plan
        d["pro_expires_at"] = expires_at
        d["expiry_str"] = pro_expiry_str(expires_at)
        d["extra_limit"] = pro_extra_limit(plan)
        d["max_towns"] = 1 + pro_extra_limit(plan)
        users.append(d)
    return render_template("admin_pro.html", users=users,
                           user=current_user(),
                           admin_username=ADMIN_USERNAME)


@app.route("/admin/pro/<int:uid>")
@login_required
@admin_required
def admin_pro_detail(uid):
    """Admin: one user's subscription card — plan, status, expiry,
    towns used — plus grant/extend/revoke and town management."""
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE id = ?",
                      (uid,)).fetchone()
    if not user:
        flash("User not found.")
        return redirect(url_for("admin_pro"))
    d = dict(user)
    status, plan, expires_at = pro_status(d)
    towns = get_user_towns(uid)
    main = next((t for t in towns if t["is_main"]), None)
    main_slug = main["town_slug"] if main else ""
    # Story counts per town via feed attribution (legacy '' = main).
    counts = {}
    for r in db.execute(
            "SELECT COALESCE(f.town_slug, '') AS ts, COUNT(*) AS c "
            "FROM stories s LEFT JOIN feeds f ON f.id = s.feed_id "
            "WHERE s.user_id = ? GROUP BY ts", (uid,)):
        key = r["ts"] if r["ts"] else main_slug
        counts[key] = counts.get(key, 0) + r["c"]
    for t in towns:
        t["story_count"] = counts.get(t["town_slug"], 0)
        t["greyed"] = bool(not t["is_main"] and status != "active")
    extras = len([t for t in towns if not t["is_main"]])
    # Item-level attribution editor: every feed/place with its current
    # town tag ('' = untagged/main) so mis-tagged legacy items can be
    # fixed precisely after a main-town switch.
    item_feeds = [
        dict(r) for r in db.execute(
            "SELECT id, name, url, COALESCE(town_slug, '') AS town_slug "
            "FROM feeds WHERE user_id = ? ORDER BY name", (uid,))]
    item_places = [
        dict(r) for r in db.execute(
            "SELECT id, name, url, COALESCE(town_slug, '') AS town_slug "
            "FROM places WHERE user_id = ? ORDER BY name", (uid,))]
    return render_template(
        "admin_pro_detail.html", user=current_user(), target=d,
        status=status, plan=plan, expires_at=expires_at,
        expiry_str=pro_expiry_str(expires_at), towns=towns,
        extras=extras, max_towns=1 + pro_extra_limit(plan),
        admin_username=ADMIN_USERNAME,
        item_feeds=item_feeds, item_places=item_places)


@app.route("/admin/pro/<int:uid>/grant", methods=["POST"])
@login_required
@admin_required
def admin_pro_grant(uid):
    """Grant a plan with an expiry date (manual billing, no Stripe)."""
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE id = ?",
                      (uid,)).fetchone()
    if not user:
        flash("User not found.")
        return redirect(url_for("admin_pro"))
    plan = request.form.get("plan", "free")
    if plan not in ("free", "pro", "elite"):
        flash("Unknown plan.")
        return redirect(url_for("admin_pro_detail", uid=uid))
    expires_at = 0
    if plan in ("pro", "elite"):
        date_s = request.form.get("expires", "").strip()
        try:
            dt = datetime.strptime(date_s, "%Y-%m-%d").replace(
                tzinfo=timezone.utc) + timedelta(hours=23, minutes=59,
                                                seconds=59)
            expires_at = int(dt.timestamp())
        except ValueError:
            flash("Expiry date must be YYYY-MM-DD.")
            return redirect(url_for("admin_pro_detail", uid=uid))
        if expires_at <= int(time.time()):
            flash("Expiry must be in the future.")
            return redirect(url_for("admin_pro_detail", uid=uid))
    db.execute("UPDATE users SET pro_plan = ?, pro_expires_at = ?, "
               "pro_notice_sent_for = 0 WHERE id = ?",
               (plan, expires_at, uid))
    db.commit()
    flash(f"Granted {plan.capitalize()}"
          + (f" until {date_s}." if expires_at else "."))
    return redirect(url_for("admin_pro_detail", uid=uid))


@app.route("/admin/pro/<int:uid>/extend", methods=["POST"])
@login_required
@admin_required
def admin_pro_extend(uid):
    """Renew/extend: push the expiry forward N calendar months from
    max(today, current expiry)."""
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE id = ?",
                      (uid,)).fetchone()
    if not user:
        flash("User not found.")
        return redirect(url_for("admin_pro"))
    _, plan, expires_at = pro_status(dict(user))
    if plan not in ("pro", "elite"):
        flash("Grant a Pro or Elite plan first.")
        return redirect(url_for("admin_pro_detail", uid=uid))
    try:
        months = int(request.form.get("months", "1"))
    except ValueError:
        months = 1
    if months not in (1, 3, 6, 12):
        months = 1
    base = max(datetime.now(timezone.utc).date(),
               datetime.fromtimestamp(expires_at,
                                      tz=timezone.utc).date())
    new_date = add_months(base, months)
    new_expires = int(datetime(new_date.year, new_date.month, new_date.day,
                               23, 59, 59,
                               tzinfo=timezone.utc).timestamp())
    db.execute("UPDATE users SET pro_expires_at = ?, "
               "pro_notice_sent_for = 0 WHERE id = ?", (new_expires, uid))
    db.commit()
    flash(f"Extended {plan.capitalize()} to {new_date.isoformat()}.")
    return redirect(url_for("admin_pro_detail", uid=uid))


@app.route("/admin/pro/<int:uid>/revoke", methods=["POST"])
@login_required
@admin_required
def admin_pro_revoke(uid):
    """Revoke a subscription back to free. Towns stay saved."""
    db = get_db()
    if not db.execute("SELECT 1 FROM users WHERE id = ?",
                      (uid,)).fetchone():
        flash("User not found.")
        return redirect(url_for("admin_pro"))
    db.execute("UPDATE users SET pro_plan = 'free', pro_expires_at = 0, "
               "pro_notice_sent_for = 0 WHERE id = ?", (uid,))
    db.commit()
    flash("Subscription revoked — towns stay saved, extra towns greyed.")
    return redirect(url_for("admin_pro_detail", uid=uid))


@app.route("/admin/pro/<int:uid>/founder", methods=["POST"])
@login_required
@admin_required
def admin_pro_founder(uid):
    """Toggle the founding badge (Elite perk: Kickstarter founders)."""
    db = get_db()
    row = db.execute("SELECT is_founder FROM users WHERE id = ?",
                     (uid,)).fetchone()
    if not row:
        flash("User not found.")
        return redirect(url_for("admin_pro"))
    try:
        cur = 1 if row["is_founder"] else 0
    except (IndexError, KeyError):
        cur = 0
    db.execute("UPDATE users SET is_founder = ? WHERE id = ?",
               (1 - cur, uid))
    db.commit()
    flash("Founder badge " + ("granted." if cur == 0 else "removed."))
    return redirect(url_for("admin_pro_detail", uid=uid))


def _opt_float(v):
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


@app.route("/admin/pro/<int:uid>/towns/add", methods=["POST"])
@login_required
@admin_required
def admin_pro_town_add(uid):
    """Add an extra town to a user's list (slot limits enforced)."""
    if not get_db().execute("SELECT 1 FROM users WHERE id = ?",
                            (uid,)).fetchone():
        flash("User not found.")
        return redirect(url_for("admin_pro"))
    ok, msg = add_user_town(uid, request.form.get("town_name", ""),
                            lat=_opt_float(request.form.get("lat")),
                            lon=_opt_float(request.form.get("lon")))
    if ok:
        # Same "instant scan" the signup flow does: a new town with
        # "No stories" looks broken, so scan its feeds right away.
        threading.Thread(target=scan_user, args=(uid,), daemon=True).start()
        msg += " Scanning its feeds now — stories will appear in a moment."
    flash(msg)
    return redirect(url_for("admin_pro_detail", uid=uid))


@app.route("/admin/pro/<int:uid>/towns/<int:tid>/remove", methods=["POST"])
@login_required
@admin_required
def admin_pro_town_remove(uid, tid):
    """Remove an extra town from a user's list (never the main town)."""
    ok, msg = remove_user_town(uid, tid)
    flash(msg)
    return redirect(url_for("admin_pro_detail", uid=uid))


@app.route("/admin/pro/<int:uid>/reassign", methods=["POST"])
@login_required
@admin_required
def admin_pro_reassign_items(uid):
    """Item-level attribution fix: change any feed/place's town tag.
    Accepts '' (untagged = follows the main town) or one of the user's
    town slugs. Invalid values are ignored."""
    db = get_db()
    if not db.execute("SELECT 1 FROM users WHERE id = ?",
                      (uid,)).fetchone():
        flash("User not found.")
        return redirect(url_for("admin_pro"))
    slugs = {t["town_slug"] for t in get_user_towns(uid)}
    changed = 0
    for table, prefix in (("feeds", "feed_"), ("places", "place_")):
        ids = [r["id"] for r in db.execute(
            f"SELECT id FROM {table} WHERE user_id = ?", (uid,))]
        for i in ids:
            key = f"{prefix}{i}"
            if key not in request.form:
                continue
            v = (request.form.get(key) or "").strip()
            if v != "" and v not in slugs:
                continue
            changed += db.execute(
                f"UPDATE {table} SET town_slug = ? "
                "WHERE id = ? AND user_id = ?",
                (v, i, uid)).rowcount
    db.commit()
    flash(f"Updated {changed} item(s).")
    return redirect(url_for("admin_pro_detail", uid=uid))


@app.route("/settings/towns/add", methods=["POST"])
@login_required
@pro_required
def settings_town_add():
    """Add an extra town from the Settings towns card (Nominatim picker).
    Same rules as the admin route: active plan required, slot limit
    enforced, feeds seeded + instant scan on success."""
    user = current_user()
    ok, msg = add_user_town(
        user["id"], request.form.get("town_name", ""),
        lat=_opt_float(request.form.get("lat")),
        lon=_opt_float(request.form.get("lon")))
    if ok:
        threading.Thread(target=scan_user, args=(user["id"],),
                         daemon=True).start()
        msg += " Scanning its feeds now — stories will appear in a moment."
    flash(msg)
    return redirect(url_for("settings_page") + "#towns")


@app.route("/settings/towns/reorder", methods=["POST"])
@login_required
@pro_required
def settings_towns_reorder():
    """Persist drag-and-drop town order from the Settings towns card.
    JSON body: {"order": [town_id, ...]} — full list, main town first."""
    data = request.get_json(force=True, silent=True) or {}
    ok, msg = reorder_user_towns(current_user()["id"],
                                data.get("order") or [])
    return jsonify({"ok": ok, "message": msg}), (200 if ok else 400)


@app.route("/settings/towns/make_main", methods=["POST"])
@login_required
@pro_required
def settings_town_make_main():
    """Make an extra town the main town (the "Make main" switch on the
    Settings towns card)."""
    user = current_user()
    try:
        tid = int(request.form.get("tid", 0))
    except (TypeError, ValueError):
        tid = 0
    ok, msg = make_main_town(user["id"], tid)
    flash(msg)
    return redirect(url_for("settings_page") + "#towns")


@app.route("/settings/towns/<int:tid>/remove", methods=["POST"])
@login_required
@pro_required
def settings_town_remove(tid):
    """Remove an extra town (never the main town)."""
    user = current_user()
    ok, msg = remove_user_town(user["id"], tid)
    flash(msg)
    return redirect(url_for("settings_page") + "#towns")


@app.route("/settings/feeds/<int:fid>/show_all", methods=["POST"])
@login_required
@pro_required
def feed_show_all(fid):
    """Toggle 'show with all': a flagged feed's stories appear in every
    town's news line, not just its home town's."""
    user = current_user()
    db = get_db()
    feed = db.execute("SELECT * FROM feeds WHERE id = ? AND user_id = ?",
                      (fid, user["id"])).fetchone()
    if not feed:
        flash("Feed not found.")
    else:
        db.execute("UPDATE feeds SET show_all = ? WHERE id = ?",
                   (0 if feed["show_all"] else 1, fid))
        db.commit()
        flash(f"{'Pinned' if not feed['show_all'] else 'Unpinned'} "
              f"{feed['name']} {'to' if not feed['show_all'] else 'from'} "
              f"all towns.")
    return redirect(url_for("settings_page") + "#feeds")


# ---------------------------------------------------------------- sponsor portal

@app.route("/s/<code>")
def sponsor_portal(code):
    """A sponsor's own page: manage their news with their unique link.
    No login — the unguessable code in the URL is the credential."""
    sponsor = get_sponsor_by_code(code)
    if not sponsor:
        return render_template("404.html"), 404
    db = get_db()
    news = [dict(r) for r in db.execute(
        "SELECT * FROM sponsor_news WHERE sponsor_id = ? "
        "ORDER BY created_at DESC", (sponsor["id"],)).fetchall()]
    return render_template("sponsor_portal.html", sponsor=sponsor,
                           news=news,
                           news_used=news_used_this_period(sponsor["id"]),
                           news_limit=NEWS_MONTHLY_LIMIT,
                           user=current_user())


@app.route("/s/<code>/news/add", methods=["POST"])
def sponsor_portal_news_add(code):
    """A sponsor adds one of their own news items."""
    sponsor = get_sponsor_by_code(code)
    if not sponsor:
        return render_template("404.html"), 404
    title = request.form.get("title", "").strip()
    text = request.form.get("text", "").strip()
    url = request.form.get("url", "").strip()
    expires = request.form.get("expires", "").strip()
    if not title:
        flash("News headline is required.")
        return redirect(url_for("sponsor_portal", code=code))
    if _news_quota_left(sponsor["id"]) <= 0:
        flash(f"News limit reached ({NEWS_MONTHLY_LIMIT} per billing "
              f"period). It resets when your next payment goes through — "
              f"or contact us if you need more.")
        return redirect(url_for("sponsor_portal", code=code))
    db = get_db()
    db.execute(
        "INSERT INTO sponsor_news (sponsor_id, title, text, url, expires, "
        "is_active, created_at) VALUES (?,?,?,?,?,1,?)",
        (sponsor["id"], title, text, url, expires, int(time.time())),
    )
    db.commit()
    if sponsor.get("email"):
        send_sponsor_news_added(
            sponsor,
            {"title": title, "text": text, "url": url, "expires": expires})
    flash("News added — live on the Business tile.")
    return redirect(url_for("sponsor_portal", code=code))


@app.route("/s/<code>/news/<int:nid>/edit", methods=["POST"])
def sponsor_portal_news_edit(code, nid):
    """A sponsor edits one of their own news items."""
    sponsor = get_sponsor_by_code(code)
    db = get_db()
    row = (db.execute("SELECT * FROM sponsor_news WHERE id = ? AND "
                      "sponsor_id = ?", (nid, sponsor["id"])).fetchone()
           if sponsor else None)
    if not row:
        return render_template("404.html"), 404
    title = request.form.get("title", "").strip()
    text = request.form.get("text", "").strip()
    url = request.form.get("url", "").strip()
    expires = request.form.get("expires", "").strip()
    if not title:
        flash("News headline is required.")
        return redirect(url_for("sponsor_portal", code=code))
    db.execute("UPDATE sponsor_news SET title = ?, text = ?, url = ?, "
               "expires = ? WHERE id = ?", (title, text, url, expires, nid))
    db.commit()
    flash("News updated.")
    return redirect(url_for("sponsor_portal", code=code))


@app.route("/s/<code>/news/<int:nid>/delete", methods=["POST"])
def sponsor_portal_news_delete(code, nid):
    """A sponsor deletes one of their own news items."""
    sponsor = get_sponsor_by_code(code)
    db = get_db()
    row = (db.execute("SELECT * FROM sponsor_news WHERE id = ? AND "
                      "sponsor_id = ?", (nid, sponsor["id"])).fetchone()
           if sponsor else None)
    if not row:
        return render_template("404.html"), 404
    db.execute("DELETE FROM sponsor_news WHERE id = ?", (nid,))
    db.commit()
    flash("News deleted.")
    return redirect(url_for("sponsor_portal", code=code))


# ---------------------------------------------------------------- dashboard

@app.route("/")
@login_required
def dashboard():
    user = current_user()
    db = get_db()
    settings = get_settings(user["id"])
    active_cat = request.args.get("category", "")
    unread_only = request.args.get("unread") == "1"

    # ---- Pro: town filter ------------------------------------------------
    # ?town=all|<slug> switches the news line between towns. Persisted per
    # user so a refresh keeps the view. Ignored entirely for non-Pro users.
    penabled = pro_enabled(user)
    pro_towns, pro_status_s, pro_plan = [], "lapsed", "free"
    town_filter = "all"
    if penabled:
        pro_towns = get_user_towns(user["id"])
        pro_status_s, pro_plan, _ = pro_status(user)
        pro_active = pro_status_s == "active"
        slugs = {t["town_slug"] for t in pro_towns}
        main_slug_all = next(
            (t["town_slug"] for t in pro_towns if t["is_main"]), "")
        qtown = request.args.get("town", "")
        if qtown:
            # Lapsed: extra towns are greyed and unreachable — fall back
            # to the main town rather than showing an empty line.
            main_slug = next(
                (t["town_slug"] for t in pro_towns if t["is_main"]), "")
            if qtown != "all" and (qtown not in slugs or
                                   (not pro_active and qtown != main_slug)):
                qtown = main_slug or "all"
            town_filter = qtown if qtown in slugs or qtown == "all" else "all"
            if (settings.get("pro_town_filter") or "all") != town_filter:
                db.execute("UPDATE settings SET pro_town_filter = ? "
                           "WHERE user_id = ?", (town_filter, user["id"]))
                db.commit()
                settings["pro_town_filter"] = town_filter
        else:
            saved = (settings.get("pro_town_filter") or "all")
            main_slug = next(
                (t["town_slug"] for t in pro_towns if t["is_main"]), "")
            if saved != "all" and (saved not in slugs or
                                   (not pro_active and saved != main_slug)):
                saved = main_slug or "all"
            town_filter = saved
    # ----------------------------------------------------------------------

    def town_scope():
        """Feed-scope for the visible news line. Returns
        (join_sql, clause_sql, params) — empty when the full line
        applies (active plan on "All towns", or non-Pro users)."""
        if not (penabled and (town_filter != "all" or not pro_active)):
            return "", "", []
        # Per-town line: this town's feeds plus feeds pinned "show with
        # all" (active plans only). Legacy untagged feeds belong to the
        # main town. Lapsed: main-town news only, even on "All towns" —
        # no news from extra towns until the subscription is renewed.
        trow = next((t for t in pro_towns if t["town_slug"] == town_filter),
                    None)
        if town_filter == "all" or (trow and trow["is_main"]):
            fslugs = [main_slug_all, ""]
        else:
            fslugs = [town_filter]
        clause = ("COALESCE(f.town_slug, '') IN (%s)" %
                  ",".join("?" * len(fslugs)))
        if pro_active and town_filter != "all":
            clause = "(%s OR COALESCE(f.show_all, 0) = 1)" % clause
        return "LEFT JOIN feeds f ON f.id = s.feed_id ", clause, fslugs

    scope_join, scope_clause, scope_params = town_scope()
    scope_and = (" AND " + scope_clause) if scope_clause else ""

    cats = [r["category"] for r in db.execute(
        "SELECT DISTINCT category FROM stories s " + scope_join +
        "WHERE s.user_id = ?" + scope_and + " ORDER BY category",
        [user["id"]] + scope_params).fetchall()]
    # Business is always a tile — sponsor news lives there even when the
    # user has no Business-category stories yet (otherwise the tile and
    # its Sponsored cards are unreachable: no chip to click).
    if "Business" not in cats:
        cats.append("Business")
    # Priority: News first, then alphabetical, Sport and Business last.
    cats = sorted(cats, key=lambda c: (0, "") if c == "News"
                  else ((2, c) if c in ("Sport", "Business") else (1, c)))

    unread_counts = {r["category"]: r["c"] for r in db.execute(
        "SELECT s.category, COUNT(*) c FROM stories s " + scope_join +
        "WHERE s.user_id = ? AND s.is_read = 0" + scope_and +
        " GROUP BY s.category",
        [user["id"]] + scope_params).fetchall()}
    unread_total = sum(unread_counts.values())
    unread_saved = db.execute(
        "SELECT COUNT(*) c FROM stories s " + scope_join +
        "WHERE s.user_id = ? AND s.is_read = 0 AND s.is_saved = 1" +
        scope_and,
        [user["id"]] + scope_params).fetchone()["c"]
    # Unread count for the category tile being viewed (None on "All").
    if active_cat == "Saved":
        cat_unread = unread_saved
    elif active_cat:
        cat_unread = unread_counts.get(active_cat, 0)
    else:
        cat_unread = None

    def chip_url(category=None, unread=False):
        args = {}
        if category:
            args["category"] = category
        if unread:
            args["unread"] = 1
        if penabled and town_filter != "all":
            args["town"] = town_filter
        return url_for("dashboard", **args)

    def page_url(p):
        args = {}
        if active_cat:
            args["category"] = active_cat
        if unread_only:
            args["unread"] = 1
        if penabled and town_filter != "all":
            args["town"] = town_filter
        if p > 1:
            args["page"] = p
        return url_for("dashboard", **args)

    def town_url(slug):
        return url_for("dashboard", town=slug)

    chips = [
        {"label": "All", "key": "__all__", "url": chip_url(unread=unread_only),
         "count": unread_total,
         "active": not active_cat and not unread_only},
        {"label": "★ Saved", "key": "Saved",
         "url": chip_url(category="Saved", unread=unread_only),
         "count": unread_saved, "active": active_cat == "Saved"},
    ]
    for c in cats:
        if c == "Saved":
            continue
        chips.append({
            "label": c,
            "key": c,
            "url": chip_url(category=c, unread=unread_only),
            "count": unread_counts.get(c, 0),
            "active": active_cat == c,
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
    if scope_clause:
        where += " AND " + scope_clause
        params.extend(scope_params)
    base = ("SELECT s.*, f.name AS feed_name, "
            "COALESCE(f.town_slug, '') AS ftown FROM stories s "
            "LEFT JOIN feeds f ON f.id = s.feed_id " + where)

    # Stories per page (0 = All: everything on one page, no pagination).
    page_size = settings.get("story_limit")
    if page_size is None:
        page_size = 200
    page, total_pages = 1, 1
    if page_size > 0:
        total = db.execute(
            "SELECT COUNT(*) c FROM stories s LEFT JOIN feeds f "
            "ON f.id = s.feed_id " + where, params).fetchone()["c"]
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

    stories = [dict(r) for r in stories]
    for st in stories:
        st["is_sponsor"] = False

    # ---- Pro: town chips + tiles -------------------------------------
    pro_show_chips = False
    pro_tiles = []
    dash_title = settings.get("town_name") or "Your town"
    if penabled:
        by_slug = {t["town_slug"]: t for t in pro_towns}
        main = next((t for t in pro_towns if t["is_main"]), None)
        if town_filter == "all":
            # Unified line: every story carries its town's chip.
            pro_show_chips = True
            for st in stories:
                ft = st.get("ftown") or ""
                t = by_slug.get(ft) or main
                st["town_name"] = t["town_name"] if t else ""
                st["town_slug"] = t["town_slug"] if t else ""
            dash_title = "All towns"
        else:
            t = by_slug.get(town_filter)
            if t:
                dash_title = t["town_name"]
        pro_tiles = [{
            "slug": "all", "name": "All towns", "is_main": False,
            "greyed": False, "active": town_filter == "all",
            "url": town_url("all"),
        }]
        for t in pro_towns:
            greyed = bool(not t["is_main"] and not pro_active)
            pro_tiles.append({
                "slug": t["town_slug"], "name": t["town_name"],
                "is_main": bool(t["is_main"]), "greyed": greyed,
                "active": town_filter == t["town_slug"],
                "url": None if greyed else town_url(t["town_slug"]),
            })
    # ----------------------------------------------------------------------
    # Sponsor news reads as news in the Business tile: merge the
    # sponsor rows into the story list (page 1 only, so they never
    # repeat across pages), newest first.
    if active_cat == "Business" and page == 1:
        stories += sponsor_news_stories_for_user(settings)
        # NULL-safe sort: a legacy story row can have no published date
        # (None or text) — comparing it with ints crashed the whole
        # Business tile with a 500. Such rows sort as oldest.
        def _sort_ts(v):
            try:
                return int(v or 0)
            except (TypeError, ValueError):
                return 0
        stories.sort(key=lambda r: _sort_ts(r["published"]), reverse=True)

    return render_template(
        "dashboard.html", user=user, settings=settings, chips=chips,
        active_cat=active_cat, unread=unread_total, unread_only=unread_only,
        cat_unread=cat_unread,
        stories=stories, rel_time=rel_time, page=page, total_pages=total_pages,
        page_url=page_url,
        unread_on_url=chip_url(category=active_cat or None, unread=True),
        unread_off_url=chip_url(category=active_cat or None, unread=False),
        pro_enabled=penabled, pro_tiles=pro_tiles,
        pro_show_chips=pro_show_chips, pro_town_filter=town_filter,
        dash_title=dash_title,
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
    # ?category=Ukraine marks only that category read; ?category=Saved
    # marks only saved stories read. No param = everything.
    category = request.args.get("category", "")
    if category == "Saved":
        db.execute("UPDATE stories SET is_read = 1 WHERE user_id = ? "
                   "AND is_saved = 1", (session["user_id"],))
    elif category:
        db.execute("UPDATE stories SET is_read = 1 WHERE user_id = ? "
                   "AND category = ?", (session["user_id"], category))
    else:
        db.execute("UPDATE stories SET is_read = 1 WHERE user_id = ?",
                   (session["user_id"],))
    db.commit()
    if category:
        return redirect(url_for("dashboard", category=category))
    return redirect(url_for("dashboard"))


@app.route("/stories/clear", methods=["POST"])
@login_required
def clear_stories():
    """Delete every story of the current user ("clean news line").
    NOTE: UI hidden for now (2026-10-11) — endpoint kept for easy return."""
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
    ("remove filter tile").
    NOTE: UI hidden for now (2026-10-11) — endpoint kept for easy return."""
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
    user = current_user()
    db = get_db()
    row = db.execute(
        "SELECT is_saved FROM stories WHERE id = ? AND user_id = ?",
        (sid, user["id"])).fetchone()
    if row and not row["is_saved"]:
        # Saved-stories cap by plan (closest to promise): free 100,
        # pro 1,000, elite unlimited. Existing saves are never deleted.
        status, plan, _ = pro_status(user)
        limit = (None if (status == "active" and plan == "elite")
                 else (1000 if status == "active" else 100))
        if limit is not None:
            n = db.execute(
                "SELECT COUNT(*) c FROM stories "
                "WHERE user_id = ? AND is_saved = 1",
                (user["id"],)).fetchone()["c"]
            if n >= limit:
                flash(f"Saved stories limit reached ({limit}) — "
                      f"remove some to save more.")
                return redirect(request.referrer or url_for("dashboard"))
    db.execute(
        "UPDATE stories SET is_saved = 1 - is_saved WHERE id = ? AND user_id = ?",
        (sid, user["id"]),
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
    # Feeds now live under Settings; keep the old URL working.
    return redirect(url_for("settings_page") + "#feeds")


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
    state = settings.get("state", "")
    country_code = settings.get("country_code", "")
    if pro_enabled(user):
        # Pro: suggestions follow the selected town tile, not the main
        # town (the tile click persists pro_town_filter, shared with
        # the dashboard and feeds card).
        filt = (settings.get("pro_town_filter") or "all")
        # The tile also passes its slug explicitly (?town=) — it wins
        # over the stored filter so suggestions can't desync from the
        # tile the user actually sees (e.g. after validation resets).
        slug_param = (request.args.get("town") or "").strip()
        if slug_param:
            towns = get_user_towns(user["id"])
            main_slug = next((t["town_slug"] for t in towns
                              if t["is_main"]), "")
            status_now, _, _ = pro_status(user)
            if slug_param in ({t["town_slug"] for t in towns} | {"all"}) \
               and (status_now == "active"
                    or slug_param in ("all", main_slug)):
                filt = slug_param
        if filt != "all":
            trow = next((t for t in get_user_towns(user["id"])
                         if t["town_slug"] == filt), None)
            if trow:
                town = trow["town_name"]
                entry = find_town_entry(trow["town_name"], trow.get("lat"),
                                        trow.get("lon"))
                state = (entry.get("region") if entry else "") or ""
                country_code = (entry.get("country_code") if entry
                                else "") or ""
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

    for f in suggested_for_town(town, state, country_code):
        add_candidate(f["name"], f["url"], "directory")
    for f in discover_web_feeds(town):
        add_candidate(f["name"], f["url"], "web")

    # Self-healing: if this town has no feeds at all, the curated
    # directory feeds are offered unconditionally. A user who lost
    # their feeds (old account, manual delete) shouldn't depend on a
    # live network check from the server to get them back; if one is
    # actually dead, the per-feed error display handles it.
    slug = town_slug_for(town) if town else ""
    town_has_feeds = bool(slug and db.execute(
        "SELECT 1 FROM feeds WHERE user_id = ? AND town_slug = ? LIMIT 1",
        (user["id"], slug)).fetchone())

    working = []
    seen_names = set()
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = pool.map(is_working_feed, [c["url"] for c in candidates])
    for c, ok in zip(candidates, results):
        if (ok or (c["origin"] == "directory" and not town_has_feeds)) \
                and c["name"] not in seen_names:
            seen_names.add(c["name"])
            working.append(c)
    return jsonify({
        "town": town,
        "suggestions": working,
    })


@app.route("/api/feeds/restore-defaults")
@login_required
def api_restore_defaults():
    """The town's curated directory feeds that the user hasn't added —
    the explicit 'get them back' action after removing defaults.
    No liveness gate: these are curated, and per-feed errors show if
    one is actually dead. The user picks which ones to re-add."""
    user = current_user()
    db = get_db()
    settings = get_settings(user["id"])
    town = settings.get("town_name", "")
    state = settings.get("state", "")
    country_code = settings.get("country_code", "")
    if pro_enabled(user):
        filt = (settings.get("pro_town_filter") or "all")
        slug_param = (request.args.get("town") or "").strip()
        if slug_param:
            towns = get_user_towns(user["id"])
            main_slug = next((t["town_slug"] for t in towns
                              if t["is_main"]), "")
            status_now, _, _ = pro_status(user)
            if slug_param in ({t["town_slug"] for t in towns} | {"all"}) \
               and (status_now == "active"
                    or slug_param in ("all", main_slug)):
                filt = slug_param
        if filt != "all":
            trow = next((t for t in get_user_towns(user["id"])
                         if t["town_slug"] == filt), None)
            if trow:
                town = trow["town_name"]
                entry = find_town_entry(trow["town_name"], trow.get("lat"),
                                        trow.get("lon"))
                state = (entry.get("region") if entry else "") or ""
                country_code = (entry.get("country_code") if entry
                                else "") or ""
    have = {norm_url(r["url"]) for r in
            db.execute("SELECT url FROM feeds WHERE user_id = ?",
                       (user["id"],)).fetchall()}
    out, seen = [], set()
    for f in suggested_for_town(town, state, country_code):
        norm = norm_url(f["url"])
        if norm and norm not in seen and norm not in have:
            seen.add(norm)
            out.append({"name": f["name"], "url": f["url"].strip()})
    return jsonify({"town": town, "defaults": out})


@app.route("/api/feeds/validate")
@login_required
def api_feeds_validate():
    """Check a feed URL *before* adding: fetches it, parses it, reports
    the feed's title and item count — or why it's not usable. Also
    reports if the URL is already in the user's list (and where)."""
    url = (request.args.get("url") or "").strip()
    if not url:
        return jsonify({"ok": False, "error": "Enter a URL first."})
    if not re.match(r"^https?://", url, re.IGNORECASE):
        return jsonify({"ok": False,
                        "error": "The URL must start with http:// or https://."})
    db = get_db()
    uid = session["user_id"]
    norm = norm_url(url)
    existing = None
    for r in db.execute("SELECT name, url, town_slug FROM feeds "
                        "WHERE user_id = ?", (uid,)):
        if norm_url(r["url"]) == norm:
            town_names = {t["town_slug"]: t["town_name"]
                          for t in get_user_towns(uid)}
            existing = {"name": r["name"],
                        "town": town_names.get(r["town_slug"] or "", "")}
            break
    try:
        parsed = fetch_feed(url)
    except Exception as e:
        return jsonify({"ok": False, "existing": existing,
                        "error": f"Could not fetch the URL ({str(e)[:120]})."})
    if getattr(parsed, "bozo", False) and not parsed.entries:
        return jsonify({"ok": False, "existing": existing,
                        "error": "The URL doesn't look like a feed — "
                                 "nothing parseable found."})
    title = (parsed.feed.get("title") or "").strip()
    return jsonify({"ok": True, "title": title,
                    "items": len(parsed.entries), "existing": existing})


@app.route("/feeds/add", methods=["POST"])
@login_required
def feed_add():
    name = request.form.get("name", "").strip()
    url = request.form.get("url", "").strip()
    added = False
    duplicate = False
    dup_info = None
    if url:
        if not name:
            name = url
        db = get_db()
        have = {}
        for r in db.execute("SELECT id, name, url, town_slug, enabled "
                            "FROM feeds WHERE user_id = ?",
                            (session["user_id"],)).fetchall():
            have[norm_url(r["url"])] = dict(r)
        dup = have.get(norm_url(url))
        if dup:
            duplicate = True
            town_names = {t["town_slug"]: t["town_name"]
                          for t in get_user_towns(session["user_id"])}
            dup_info = {
                "name": dup["name"],
                "town": town_names.get(dup["town_slug"] or "", ""),
                "enabled": bool(dup["enabled"]),
            }
        else:
            # Pro: a feed added while a town tile is selected is tagged
            # with that town (validated against the user's own towns).
            tslug = (request.form.get("town_slug") or "").strip()
            if tslug and pro_enabled(current_user()):
                slugs = {t["town_slug"]
                         for t in get_user_towns(session["user_id"])}
                if tslug not in slugs:
                    tslug = ""
            else:
                tslug = ""
            db.execute(
                "INSERT INTO feeds (user_id, name, url, enabled, town_slug) "
                "VALUES (?,?,?,1,?)",
                (session["user_id"], name, url, tslug or None),
            )
            db.commit()
            added = True
            threading.Thread(target=scan_user, args=(session["user_id"],),
                             daemon=True).start()
    wants_json = (request.headers.get("X-Requested-With") == "fetch"
                  or "application/json" in request.headers.get("Accept", ""))
    if wants_json:
        return jsonify({"ok": added, "duplicate": duplicate,
                        "existing": dup_info if duplicate else None,
                        "name": name, "url": url})
    if duplicate:
        where = f" ({dup_info['town']})" if dup_info["town"] else ""
        flash(f"That feed is already in your list as "
              f"“{dup_info['name']}”{where}.")
    return redirect(url_for("settings_page") + "#feeds")


@app.route("/feeds/<int:fid>/toggle", methods=["POST"])
@login_required
def feed_toggle(fid):
    db = get_db()
    db.execute(
        "UPDATE feeds SET enabled = 1 - enabled "
        "WHERE id = ? AND user_id = ?", (fid, session["user_id"]))
    db.commit()
    return redirect(url_for("settings_page") + "#feeds")


@app.route("/feeds/<int:fid>/delete", methods=["POST"])
@login_required
def feed_delete(fid):
    db = get_db()
    db.execute("DELETE FROM feeds WHERE id = ? AND user_id = ?",
               (fid, session["user_id"]))
    db.commit()
    return redirect(url_for("settings_page") + "#feeds")


@app.route("/feeds/<int:fid>/edit", methods=["GET", "POST"])
@login_required
def feed_edit(fid):
    """Edit a feed: rename it, fix the URL (validated before saving),
    re-tag its town, enable/disable."""
    user = current_user()
    db = get_db()
    feed = db.execute("SELECT * FROM feeds WHERE id = ? AND user_id = ?",
                      (fid, user["id"])).fetchone()
    if not feed:
        flash("Feed not found.")
        return redirect(url_for("settings_page") + "#feeds")
    f = dict(feed)
    towns = get_user_towns(user["id"]) if pro_enabled(user) else []
    if request.method == "POST":
        url = (request.form.get("url") or "").strip()
        name = (request.form.get("name") or "").strip() or url
        if not url:
            flash("The URL can't be empty.")
            return redirect(url_for("feed_edit", fid=fid))
        if not re.match(r"^https?://", url, re.IGNORECASE):
            flash("The URL must start with http:// or https://.")
            return redirect(url_for("feed_edit", fid=fid))
        # Duplicate check, excluding this feed.
        norm = norm_url(url)
        for r in db.execute("SELECT id, name, url FROM feeds "
                            "WHERE user_id = ? AND id != ?",
                            (user["id"], fid)):
            if norm_url(r["url"]) == norm:
                flash(f"That URL is already in your list as “{r['name']}”.")
                return redirect(url_for("feed_edit", fid=fid))
        # The URL must actually serve a feed before we save it.
        try:
            parsed = fetch_feed(url)
            valid = not (getattr(parsed, "bozo", False)
                         and not parsed.entries)
        except Exception:
            valid = False
        if not valid:
            flash("That URL doesn't look like a working feed — not saved. "
                  "Use the Check button to see the error.")
            return redirect(url_for("feed_edit", fid=fid))
        tslug = (request.form.get("town_slug") or "").strip()
        if pro_enabled(user) and tslug:
            if tslug not in {t["town_slug"] for t in towns}:
                tslug = f["town_slug"] or ""
        else:
            tslug = f["town_slug"] or ""
        enabled = 1 if request.form.get("enabled") else 0
        db.execute("UPDATE feeds SET name = ?, url = ?, town_slug = ?, "
                   "enabled = ? WHERE id = ?",
                   (name, url, tslug or None, enabled, fid))
        db.commit()
        flash(f"Saved “{name}”.")
        threading.Thread(target=scan_user, args=(user["id"],),
                         daemon=True).start()
        return redirect(url_for("settings_page") + "#feeds")
    return render_template("feed_edit.html", user=user, feed=f, towns=towns,
                           pro_enabled=pro_enabled(user))


@app.route("/feeds/scan_now", methods=["POST"])
@login_required
def scan_now():
    session["scan_requested_at"] = int(time.time())
    threading.Thread(target=scan_user, args=(session["user_id"],),
                     daemon=True).start()
    flash("Scanning your feeds — new stories will appear in a moment.", "scan")
    return redirect(request.referrer or url_for("settings_page") + "#feeds")


def pro_town_scan_feeds(user_id, slug):
    """Feed ids to scan for one town: its tagged feeds plus feeds flagged
    'show with all'. The main town also gets untagged legacy feeds.
    Returns (town_dict_or_None, [feed ids])."""
    db = get_db()
    towns = get_user_towns(user_id)
    t = next((x for x in towns if x["town_slug"] == slug), None)
    if not t:
        return None, []
    if t["is_main"]:
        rows = db.execute(
            "SELECT id FROM feeds WHERE user_id = ? AND enabled = 1 AND "
            "(COALESCE(town_slug, '') IN (?, '') OR "
            "COALESCE(show_all, 0) = 1)",
            (user_id, slug)).fetchall()
    else:
        rows = db.execute(
            "SELECT id FROM feeds WHERE user_id = ? AND enabled = 1 AND "
            "(town_slug = ? OR COALESCE(show_all, 0) = 1)",
            (user_id, slug)).fetchall()
    return t, [r["id"] for r in rows]


@app.route("/feeds/scan_town", methods=["POST"])
@login_required
@pro_required
def scan_town():
    """Scan only one town's feeds (the "Scan {Town}" button on the
    Settings feeds card). Lapsed plans are limited to the main town,
    like the tiles."""
    user = current_user()
    slug = (request.form.get("town") or "").strip()
    status, _, _ = pro_status(user)
    if status != "active":
        main_slug = next((t["town_slug"] for t in get_user_towns(user["id"])
                          if t["is_main"]), "")
        if slug != main_slug:
            flash("That town is greyed until the subscription is renewed.")
            return redirect(url_for("settings_page") + "#feeds")
    t, ids = pro_town_scan_feeds(user["id"], slug)
    if not t:
        flash("Town not found.")
        return redirect(url_for("settings_page") + "#feeds")
    session["scan_requested_at"] = int(time.time())
    threading.Thread(target=scan_user, args=(user["id"], ids),
                     daemon=True).start()
    flash(f"Scanning {t['town_name']} feeds — new stories will appear "
          f"in a moment.", "scan")
    return redirect(url_for("settings_page") + "#feeds")


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
    sponsors = get_sponsors_for_user(settings)
    penabled = pro_enabled(user)
    pro_towns, pro_place_groups = [], []
    pro_place_tiles, pro_place_filter = [], "all"
    if penabled:
        # Group places under town headers in the user's town order;
        # untagged (legacy / non-Pro) places join the main town.
        pro_towns = get_user_towns(user["id"])
        pro_status_s, _, _ = pro_status(user)
        pro_active = pro_status_s == "active"
        slugs = {t["town_slug"] for t in pro_towns}
        main_slug = next((t["town_slug"] for t in pro_towns if t["is_main"]),
                         "")
        # The places view shares the dashboard/feeds "current town"
        # concept: picking a tile here switches the news line too.
        qtown = (request.args.get("town") or "").strip()
        if qtown:
            if qtown != "all" and (qtown not in slugs or
                                  (not pro_active and qtown != main_slug)):
                qtown = main_slug or "all"
            pro_place_filter = qtown if qtown in slugs or qtown == "all" \
                else "all"
            if (settings.get("pro_town_filter") or "all") != pro_place_filter:
                db.execute("UPDATE settings SET pro_town_filter = ? "
                           "WHERE user_id = ?", (pro_place_filter, user["id"]))
                db.commit()
                settings["pro_town_filter"] = pro_place_filter
        else:
            saved = (settings.get("pro_town_filter") or "all")
            if saved != "all" and (saved not in slugs or
                                  (not pro_active and saved != main_slug)):
                saved = main_slug or "all"
            pro_place_filter = saved
        groups = {t["town_slug"]: {"town_name": t["town_name"],
                                   "town_slug": t["town_slug"],
                                   "is_main": bool(t["is_main"]),
                                   "places": []} for t in pro_towns}
        for p in places:
            d = dict(p)
            g = groups.get(d.get("town_slug") or "") or groups.get(main_slug)
            if g is None:
                continue
            g["places"].append(d)
        pro_place_groups = [groups[t["town_slug"]] for t in pro_towns]
        for g in pro_place_groups:
            g["places"].sort(key=lambda d: d["name"].lower())
        if pro_place_filter != "all":
            pro_place_groups = [g for g in pro_place_groups
                                if g["town_slug"] == pro_place_filter]
        # Town tiles row (same component as dashboard/feeds).
        pro_place_tiles = [{
            "slug": "all", "name": "All towns", "is_main": False,
            "greyed": False, "active": pro_place_filter == "all",
            "url": url_for("places_page", town="all"),
        }]
        for t in pro_towns:
            greyed = bool(not t["is_main"] and not pro_active)
            pro_place_tiles.append({
                "slug": t["town_slug"], "name": t["town_name"],
                "is_main": bool(t["is_main"]), "greyed": greyed,
                "active": pro_place_filter == t["town_slug"],
                "url": (None if greyed else
                        url_for("places_page", town=t["town_slug"])),
            })
    # Suggestions follow the selected town tile (main town when "All
    # towns" is picked); non-Pro users get their single town.
    suggest_town, suggest_slug = settings.get("town_name") or "your town", ""
    if penabled and pro_towns:
        st = next((t for t in pro_towns
                   if t["town_slug"] == pro_place_filter), None)
        if st is None:
            st = next((t for t in pro_towns if t["is_main"]), pro_towns[0])
        suggest_town, suggest_slug = st["town_name"], st["town_slug"]
    return render_template("places.html", user=user, settings=settings,
                           places=places, sponsors=sponsors,
                           pro_enabled=penabled, pro_towns=pro_towns,
                           pro_place_groups=pro_place_groups,
                           pro_place_tiles=pro_place_tiles,
                           pro_place_filter=pro_place_filter,
                           suggest_town=suggest_town,
                           suggest_slug=suggest_slug)


@app.route("/api/places/suggestions")
@login_required
def api_places_suggestions():
    """Suggested places for a town: curated directory + OpenStreetMap
    discovery around its coordinates. Excludes already-saved places.

    ?town=<slug> picks the town (Pro users: one of their towns, falls
    back to the main town). OSM discovery runs on demand only: the
    response carries scanned=false until ?scan=1 (or ?refresh=1) has
    populated the cache — afterwards results come from cache, no
    rescan needed.
    """
    user = current_user()
    db = get_db()
    settings = get_settings(user["id"])
    town_slug, town_name, lat, lon = "", "", None, None
    if pro_enabled(user):
        towns = get_user_towns(user["id"])
        want = (request.args.get("town") or "").strip()
        target = next((t for t in towns if t["town_slug"] == want), None)
        if target is None:
            target = next((t for t in towns if t["is_main"]), None)
        if target:
            town_slug = target["town_slug"]
            town_name = target["town_name"]
            lat, lon = target["lat"], target["lon"]
    if not town_name:
        town_name = settings.get("town_name", "")
        lat, lon = settings.get("lat"), settings.get("lon")
    key = _osm_key(town_slug, town_name)
    do_scan = request.args.get("scan") == "1" or \
        request.args.get("refresh") == "1"
    if request.args.get("refresh") == "1":
        # Rethink: drop the cached OSM discovery so this call scans fresh.
        _osm_cache.pop(key, None)
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

    for p in suggested_places_for_town(town_name):
        add(p["name"], p["url"], origin="directory")
    if do_scan:
        osm = discover_osm_places(lat, lon, town_name, cache_key=key)
        scanned = True
    else:
        osm = _osm_fresh(key)
        scanned = osm is not None
        osm = osm or []
    for p in osm:
        add(p["name"], p["url"], kind=p["kind"], origin=p["origin"])
    return jsonify({"town": town_name, "town_slug": town_slug,
                    "suggestions": suggestions, "scanned": scanned})


@app.route("/places/add", methods=["POST"])
@login_required
def place_add():
    name = request.form.get("name", "").strip()
    url = request.form.get("url", "").strip()
    added = False
    duplicate = False
    # Pro users file a place under a chosen town (defaults to main).
    town_slug = (request.form.get("town_slug") or "").strip()
    if url:
        if not name:
            name = url
        db = get_db()
        user = current_user()
        if not town_slug and pro_enabled(user):
            towns = get_user_towns(user["id"])
            main = next((t for t in towns if t["is_main"]), None)
            town_slug = main["town_slug"] if main else ""
        if pro_enabled(user) and town_slug:
            slugs = {t["town_slug"] for t in get_user_towns(user["id"])}
            if town_slug not in slugs:
                town_slug = ""
        have = {norm_url(r["url"]) for r in
                db.execute("SELECT url FROM places WHERE user_id = ?",
                           (session["user_id"],)).fetchall()}
        if norm_url(url) in have:
            duplicate = True
        else:
            db.execute(
                "INSERT INTO places (user_id, name, url, town_slug) "
                "VALUES (?,?,?,?)",
                (session["user_id"], name, url, town_slug),
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
        # Plan floor: free 60 min, pro 15, elite 5 (closest to promise).
        interval = max(min_scan_interval(user), interval)
        notify = 1 if request.form.get("notify_enabled") else 0
        digest = 1 if request.form.get("digest_enabled") else 0
        raw_limit = request.form.get("story_limit", 200)
        try:
            story_limit = int(raw_limit)
        except (TypeError, ValueError):
            story_limit = 200
        if story_limit not in (0, 5, 10, 15, 25, 50, 100, 200):
            story_limit = 200  # 0 = All: everything on one page
        db.execute(
            "UPDATE settings SET scan_interval_min = ?, notify_enabled = ?, "
            "digest_enabled = ?, story_limit = ? WHERE user_id = ?",
            (interval, notify, digest, story_limit, user["id"]),
        )
        db.commit()
        return redirect(url_for("settings_page"))
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
    penabled = pro_enabled(user)
    pro_towns, pro_feed_groups, pro_sub = [], [], None
    pro_feed_tiles, pro_feed_filter, pro_feed_filter_name = [], "all", "All towns"
    if penabled:
        pro_towns = get_user_towns(user["id"])
        for t in pro_towns:
            t["n_feeds"], t["n_stories"], t["n_places"] = \
                town_item_counts(user["id"], t["town_slug"])
        status, plan, expires_at = pro_status(user)
        pro_active = status == "active"
        extras = [t for t in pro_towns if not t["is_main"]]
        max_extras = pro_extra_limit(plan) if plan in ("pro", "elite") else 5
        pro_sub = {
            "plan": plan, "status": status, "expires_at": expires_at,
            "expiry_str": pro_expiry_str(expires_at),
            "extras": len(extras), "max_extras": max_extras,
            "max_towns": 1 + max_extras,
        }
        main_slug = next((t["town_slug"] for t in pro_towns if t["is_main"]),
                         "")
        slugs = {t["town_slug"] for t in pro_towns}

        def _valid_filter(v):
            v = (v or "all").strip()
            if v != "all" and (v not in slugs or
                              (not pro_active and v != main_slug)):
                return main_slug or "all"
            return v

        # The feeds card shares the dashboard's "current town" concept:
        # picking a tile here switches the news line too.
        fq = (request.args.get("feed_town") or "").strip()
        if fq:
            fq = _valid_filter(fq)
            if (settings.get("pro_town_filter") or "all") != fq:
                db.execute("UPDATE settings SET pro_town_filter = ? "
                           "WHERE user_id = ?", (fq, user["id"]))
                db.commit()
                settings["pro_town_filter"] = fq
        pro_feed_filter = _valid_filter(settings.get("pro_town_filter"))
        # Feeds grouped under town headers in town order; untagged
        # (legacy) feeds belong to the main town.
        groups = {t["town_slug"]: {"town_name": t["town_name"],
                                   "town_slug": t["town_slug"],
                                   "is_main": bool(t["is_main"]),
                                   "feeds": []} for t in pro_towns}
        for d in feed_rows:
            g = groups.get(d.get("town_slug") or "") or groups.get(main_slug)
            if g is None:
                continue
            g["feeds"].append(d)
        all_groups = [groups[t["town_slug"]] for t in pro_towns]
        for g in all_groups:
            g["feeds"].sort(key=lambda d: d["name"].lower())
        pro_feed_groups = (all_groups if pro_feed_filter == "all"
                           else [g for g in all_groups
                                 if g["town_slug"] == pro_feed_filter])
        # Town tiles row (same component as the dashboard).
        pro_feed_tiles = [{
            "slug": "all", "name": "All towns", "is_main": False,
            "greyed": False, "active": pro_feed_filter == "all",
            "url": url_for("settings_page", feed_town="all") + "#feeds",
        }]
        for t in pro_towns:
            greyed = bool(not t["is_main"] and not pro_active)
            pro_feed_tiles.append({
                "slug": t["town_slug"], "name": t["town_name"],
                "is_main": bool(t["is_main"]), "greyed": greyed,
                "active": pro_feed_filter == t["town_slug"],
                "url": (None if greyed else
                        url_for("settings_page",
                                feed_town=t["town_slug"]) + "#feeds"),
            })
        ft = next((t for t in pro_towns
                   if t["town_slug"] == pro_feed_filter), None)
        pro_feed_filter_name = ft["town_name"] if ft else "All towns"
    return render_template("settings.html", user=user, settings=settings,
                           intervals=[iv for iv in INTERVALS
                                      if iv[0] >= min_scan_interval(user)],
                           feeds=feed_rows,
                           rel_time=rel_time, pro_enabled=penabled,
                           pro_towns=pro_towns,
                           pro_feed_groups=pro_feed_groups, pro_sub=pro_sub,
                           pro_feed_tiles=pro_feed_tiles,
                           pro_feed_filter=pro_feed_filter,
                           pro_feed_filter_name=pro_feed_filter_name)


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
    email = request.form.get("email", "").strip().lower()
    if not valid_email(email):
        flash("That does not look like a valid email address.")
    else:
        db = get_db()
        taken = db.execute(
            "SELECT id FROM users WHERE (email = ? OR username = ?) "
            "AND id != ?",
            (email, email, user["id"])).fetchone()
        if taken:
            flash("That email is already in use by another account.")
        else:
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
        added, matched = seed_default_feeds(
            user["id"], town, state=state, country_code=country_code,
            lat=float(lat or 0) or None, lon=float(lon or 0) or None)
        threading.Thread(target=scan_user, args=(user["id"],),
                         daemon=True).start()
        if matched and matched.lower() != town.lower():
            flash(f"Town set to {town} — we started you with feeds "
                  f"from nearby {matched}.")
        else:
            flash(f"Town set to {town} — here are feeds we found for it.")
    return redirect(url_for("settings_page"))


@app.route("/settings/locate", methods=["POST"])
@login_required
def locate():
    """'Scan near me': the nearby town becomes the user's main town —
    user_towns, settings and freshly seeded feeds (tagged with the new
    town's slug so stories attribute correctly), then an immediate scan.
    Reuses the main row in place, so plan town limits are never
    exceeded and the old town's feeds keep their attribution."""
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
    slug = town_slug_for(town)
    towns = get_user_towns(user["id"])
    existing = next((t for t in towns if t["town_slug"] == slug), None)
    if existing:
        if not existing["is_main"]:
            # Already in the list — just make it the main town (pins
            # legacy feeds to the old main, syncs settings).
            make_main_town(user["id"], existing["id"])
        db.execute("UPDATE settings SET state = ?, country_code = ? "
                   "WHERE user_id = ?",
                   (state, country_code, user["id"]))
        db.commit()
    else:
        # Repurpose the main row: pin legacy untagged feeds/places to
        # the outgoing main first (same as make-main), then the nearby
        # town takes over as main.
        main = next((t for t in towns if t["is_main"]), None)
        if main and main["town_slug"]:
            db.execute("UPDATE feeds SET town_slug = ? WHERE user_id = ? "
                       "AND COALESCE(town_slug, '') = ''",
                       (main["town_slug"], user["id"]))
            db.execute("UPDATE places SET town_slug = ? WHERE user_id = ? "
                       "AND COALESCE(town_slug, '') = ''",
                       (main["town_slug"], user["id"]))
        if main:
            db.execute("UPDATE user_towns SET town_name = ?, town_slug = ?, "
                       "lat = ?, lon = ? WHERE id = ?",
                       (town, slug, float(lat), float(lon), main["id"]))
        else:
            db.execute("INSERT INTO user_towns (user_id, town_name, "
                       "town_slug, lat, lon, is_main, sort_order, "
                       "created_at) VALUES (?,?,?,?,?,1,0,?)",
                       (user["id"], town, slug, float(lat), float(lon),
                        int(time.time())))
        db.execute(
            "UPDATE settings SET town_name = ?, lat = ?, lon = ?, "
            "state = ?, country_code = ? WHERE user_id = ?",
            (town, float(lat), float(lon), state, country_code,
             user["id"]),
        )
        db.commit()
    # Seed the town's default feeds, tagging the fresh rows so the news
    # line attributes their stories to the nearby town.
    before = {r["id"] for r in db.execute(
        "SELECT id FROM feeds WHERE user_id = ?", (user["id"],))}
    seed_default_feeds(user["id"], town, state=state,
                       country_code=country_code,
                       lat=float(lat), lon=float(lon))
    for r in db.execute("SELECT id FROM feeds WHERE user_id = ?",
                        (user["id"],)):
        if r["id"] not in before:
            db.execute("UPDATE feeds SET town_slug = ? WHERE id = ?",
                       (slug, r["id"]))
    db.commit()
    threading.Thread(target=scan_user, args=(user["id"],),
                     daemon=True).start()
    return jsonify({"ok": True, "town": town})


# ---------------------------------------------------------------- main

@app.context_processor
def inject_helpers():
    return {"rel_time": rel_time, "site_url": SITE_URL,
            "canonical_url": SITE_URL + request.path,
            "donate_url": DONATE_URL}


# ---------------------------------------------------------------- SEO
SITE_URL = os.environ.get("SITE_URL", "https://townsline.com").rstrip("/")
DONATE_URL = os.environ.get("DONATE_URL", "").strip()


@app.route("/robots.txt")
def robots_txt():
    body = (f"User-agent: *\nAllow: /\nDisallow: /admin\n"
            f"Sitemap: {SITE_URL}/sitemap.xml\n")
    return Response(body, mimetype="text/plain")


@app.route("/sitemap.xml")
def sitemap_xml():
    pages = ["/login", "/register", "/about", "/towns"]
    urls = "".join(
        f"<url><loc>{SITE_URL}{p}</loc><changefreq>weekly</changefreq>"
        f"<priority>0.8</priority></url>" for p in pages)
    for slug in sorted(TOWNS):
        urls += (f"<url><loc>{SITE_URL}/t/{slug}</loc>"
                 f"<changefreq>weekly</changefreq><priority>0.7</priority></url>")
    xml = ('<?xml version="1.0" encoding="UTF-8"?>'
           '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
           f'{urls}</urlset>')
    return Response(xml, mimetype="application/xml")


@app.route("/towns")
def towns_page():
    """Public directory of every town on TownsLine, grouped by
    continent -> country -> region."""
    tree = {}
    for slug, t in TOWNS.items():
        cont = CONTINENT_BY_CC.get((t.get("country_code") or "").lower(),
                                   "Other")
        tree.setdefault(cont, {}).setdefault(
            t.get("country") or "Other", {}).setdefault(
            t.get("region") or "Other", []).append(t)
    continents = []
    order = CONTINENT_ORDER + sorted(c for c in tree
                                     if c not in CONTINENT_ORDER)
    for cont in order:
        if cont not in tree:
            continue
        countries = []
        for country in sorted(tree[cont]):
            regions = []
            for region in sorted(tree[cont][country]):
                towns = sorted(tree[cont][country][region],
                               key=lambda x: x["name"])
                regions.append({"name": region, "towns": towns,
                                "count": len(towns)})
            countries.append({"name": country, "regions": regions,
                              "count": sum(r["count"] for r in regions)})
        continents.append({"name": cont, "countries": countries,
                           "count": sum(c["count"] for c in countries)})
    return render_template("towns.html", continents=continents,
                           total=sum(c["count"] for c in continents))


@app.route("/t/<slug>")
def town_page(slug):
    town = TOWNS.get(slug)
    if not town:
        return render_template("404.html"), 404
    sponsors = get_sponsors_for_town(slug)
    category_feeds = category_feeds_for_town(town.get("name"),
                                             town.get("country_code"))
    return render_template("town.html", town=town, sponsors=sponsors,
                           category_feeds=category_feeds)


@app.route("/about")
def about_page():
    return render_template("about.html")


def start_background():
    """DB + scanner thread. Runs at import so it also works under gunicorn
    (use a single worker: gunicorn --workers 1)."""
    init_db()
    t = threading.Thread(target=scanner_loop, daemon=True)
    t.start()
    t2 = threading.Thread(target=renewal_loop, daemon=True)
    t2.start()


start_background()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"TownsLine Web on http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, debug=False)
