# TownsLine Pro — Design Document

**Status:** design (for overnight implementation)
**Author:** Muse (subagent design pass)
**Date:** 2026-10-15
**Clickable prototype:** `~/workspace/your_files/townsline-pro-prototype/index.html`

Pro is the paid multi-town tier of TownsLine Web. Free = one town (the user's
existing main town). **Pro = main + up to 5 extra towns (6 total).**
**Elite = main + up to 10 extra towns (11 total).**

**Phase 1 scope (this build): ADMIN-ONLY.** The entire Pro surface — routes,
templates, nav, emails, daemon — is visible only to the admin user
(`admin_required`, `is_admin` conditionals). Regular users see **zero changes**
to any existing page. Stripe is NOT implemented in this phase; subscriptions
are granted manually by the admin (same pattern as sponsor management).

---

## 1. Data model

### 1.1 `users` — new columns

| Column | Type | Default | Meaning |
|---|---|---|---|
| `pro_plan` | TEXT | `'free'` | `'free'` \| `'pro'` \| `'elite'` |
| `pro_expires_at` | INTEGER | `0` | Unix timestamp (UTC). `0` = never granted |
| `pro_notice_sent_for` | INTEGER | `0` | Expiry ts the 3-day reminder was sent for (idempotency, mirrors `sponsors.notice_sent_for`) |

Pro is **active** iff `pro_plan != 'free'` **and** `pro_expires_at > now()`.
Everything else is **lapsed** (covers: never had Pro, expired Pro, expired
Elite). Lapse is **computed, not stored** — no cron needed for enforcement;
the grey-out happens the instant `pro_expires_at` passes, because every query
checks the computed predicate.

### 1.2 `user_towns` — new table

```sql
CREATE TABLE user_towns (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    town_name  TEXT NOT NULL DEFAULT '',
    town_slug  TEXT NOT NULL DEFAULT '',
    lat        REAL,
    lon        REAL,
    is_main    INTEGER NOT NULL DEFAULT 0,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL DEFAULT 0,
    UNIQUE (user_id, town_slug)
);
CREATE INDEX idx_user_towns_user ON user_towns(user_id);
```

- `is_main = 1` — the user's home town, backfilled from the `settings` table
  (see §1.4). The main town can never be removed, moved, or greyed out.
- `sort_order` — the user's custom town order (main = 0, extras 1..n).
  Drives the dashboard tiles, Settings rows, Places groups and feeds groups.
- Extra towns: count limit = `5` for Pro, `10` for Elite. Enforced at insert
  (UI + server-side check).
- In Phase 1 only the admin user gets extra towns; the schema is multi-user
  ready.

### 1.3 `feeds` — new columns

| Column | Type | Default | Meaning |
|---|---|---|---|
| `town_slug` | TEXT | `''` | Which town this feed belongs to. `''` = legacy / main town |
| `show_all` | INTEGER | `0` | `1` = "show with all": this feed's stories appear in **every** town's news line |

Stories map to towns via the feed join: `stories.feed_id → feeds.town_slug`.
Set at feed-seeding time for each town (mirror of the existing town-aware
signup seeding: curated feeds + directory places + auto category feeds per
town).

### 1.4 `settings` — new column

| Column | Type | Default | Meaning |
|---|---|---|---|
| `pro_town_filter` | TEXT | `'all'` | Persisted dashboard town filter: `'all'` or a town slug |

### 1.5 `places` — new column

| Column | Type | Default | Meaning |
|---|---|---|---|
| `town_slug` | TEXT | `''` | Which town this place belongs to. `''` = legacy / main town |

Tagged at seed time (`seed_places_for_town`) and on manual add (Pro users
pick the town; defaults to main).

### 1.6 Migration notes (existing DBs)

SQLite `ALTER TABLE … ADD COLUMN` is supported; follow the existing pattern in
`init_db()` (cf. the `sponsors.notice_sent_for` migration ~line 855):

```sql
ALTER TABLE users ADD COLUMN pro_plan TEXT DEFAULT 'free';
ALTER TABLE users ADD COLUMN pro_expires_at INTEGER DEFAULT 0;
ALTER TABLE users ADD COLUMN pro_notice_sent_for INTEGER DEFAULT 0;
ALTER TABLE feeds ADD COLUMN town_slug TEXT DEFAULT '';
ALTER TABLE user_towns ADD COLUMN sort_order INTEGER DEFAULT 0;
ALTER TABLE feeds ADD COLUMN show_all INTEGER DEFAULT 0;
ALTER TABLE settings ADD COLUMN pro_town_filter TEXT DEFAULT 'all';
ALTER TABLE places ADD COLUMN town_slug TEXT DEFAULT '';
```

Guard each with `PRAGMA table_info` checks (existing convention).

**Backfill:**
1. For every user with a `settings.town_name`, insert one `user_towns` row
   with `is_main = 1`, `sort_order = 0`, `town_slug = town_slug_for(town_name)`,
   `lat/lon` from the settings/geocode cache where available.
2. Existing feeds keep `town_slug = ''`, which the queries treat as the main
   town — so the current single-town behavior is preserved exactly for
   everyone, with no data rewrite.
3. `sort_order` backfill: main = 0, existing extras numbered 1..n by
   `created_at`.

---

## 2. UX flows (per state) — INTEGRATED approach

There are no separate Pro pages. The live dashboard, Settings and Places
become Pro-aware behind one gate, `pro_enabled(user)` (see §6). Non-Pro
users see byte-for-byte today's UI.

### 2.1 Dashboard — town tiles row

Directly above the news line, a slim tile row (rendered only when
`pro_enabled`):

`[All towns] [📌 Airdrie] [Calgary] [Lviv]`

- **All towns**: the unified line — every story in one `<ul class="stories">`,
  each row carrying a town chip (`.town-chip`) next to the category pill.
- **A town tile**: that town's stories only, plus any feed flagged
  "show with all" (see §2.4).
- Clicking a tile switches via `?town=<slug>` / `?town=all`; the choice
  persists per user (`settings.pro_town_filter`, default `all`).
- The main town tile is pinned with 📌 and always first; extras follow in
  the user's custom order (`user_towns.sort_order`) — the same order used
  in Settings, Places and feeds.
- The dashboard H1 reflects the filter ("All towns" / town name).
- Category chips, unread counts, pagination and sponsor merging compose
  with the town filter unchanged.

### 2.2 Lapsed (expired plan) — grey-out on the real dashboard

- Extra tiles render **greyed and unclickable** (no link, `cursor:not-allowed`).
- "All towns" and any per-town view show **main-town news only**; no story
  from an extra town's feed appears (feeds flagged "show with all" from
  extra towns are also excluded while lapsed).
- Extra towns stay saved in Settings (greyed rows); renewing restores them
  instantly — no re-seeding, feeds never stopped existing.
- Adding towns while lapsed is **rejected**: "Subscription lapsed — renew to
  add extra towns." (Yuriy's decision, 2026-10-16.)

### 2.3 Settings — "Your towns" card (pro_enabled only)

Replaces the single "Your town" card:

- Main town pinned at top with 📌 + "Main" badge — cannot be moved or removed.
- Extra towns as slim rows with **↑ / ↓ reorder arrows** and **Remove**
  (confirm). Order persists in `user_towns.sort_order` and drives the
  dashboard tiles, Places groups and feeds groups — single source of truth.
- "Change main town" keeps today's Nominatim picker (posts to
  `/settings/location`); the pinned `user_towns` row is reconciled so there
  is exactly one main town.
- "Add an extra town": the same Nominatim picker UX (new instance posting
  to `/settings/towns/add`) — type, pick the correct town name, feeds seed
  and scan instantly (Yuriy's decisions, 2026-10-16).
- Removing a town never deletes stories: its feeds revert to untagged
  (main-town) attribution.

### 2.4 Settings — Feeds grouped per town + "show with all"

- Feeds render grouped under town headers in town order (untagged/legacy
  feeds group under the main town).
- Each feed row gets a **"show with all"** checkbox (`feeds.show_all`).
  A flagged feed's stories appear in **every** town's news line ("pinned
  for all towns"); unflagged feeds never leak across towns.

### 2.5 Settings — "Subscription" card (pro_enabled only)

Plan pill + status (Active/Lapsed), expiry date, towns used
(`3 of 6` on Pro, `8 of 11` on Elite), and the renew note
("To renew, reply to the renewal email." — Stripe comes later, §5).

### 2.6 Places

- `places.town_slug` tags seeded and user-added places (add form gets a town
  dropdown for Pro users; suggestions default to the main town).
- The "Your places" list groups under town headers in town order.
- Sponsors / featured businesses section unchanged.

### 2.7 Admin granting (unchanged)

`/admin/pro` (`@admin_required`): user table with plan/status/expiry/towns,
grant form (plan + expiry date), extend, revoke. This stays the
subscription back-office; the day-to-day town management moved to Settings.

---

## 3. Subscription lifecycle

```
grant (admin) ──► active ──► 3-day reminder email ──► expiry ──► lapsed (greyed)
                      ▲                                              │
                      └────────────── renew (admin) ◄────────────────┘
```

1. **Grant** — admin sets `pro_plan` + `pro_expires_at`, resets
   `pro_notice_sent_for = 0`. Confirmation email to user (BCC admin).
2. **Active** — dashboard town selector, unified/per-town views, add-town
   all work; counter enforces 5/10 extra-town limits.
3. **3-day reminder** — daemon (hourly, same thread as `renewal_loop`):
   for each user with `pro_plan != 'free'` and
   `date(pro_expires_at) = today + 3 days` and
   `pro_notice_sent_for != pro_expires_at` → send email, then
   `UPDATE users SET pro_notice_sent_for = pro_expires_at`.
   **Idempotency:** the notice is keyed to the exact expiry timestamp, so a
   restart, a repeat run, or a *renewal that moves the expiry* can never
   double-send. (Exact mirror of `send_renewal_notices` for sponsors.)
4. **Expiry** — no cron action needed. `is_pro_active()` returns false the
   moment `pro_expires_at <= now()`. Dashboard queries filter extra towns out;
   town list renders them greyed. Instant, atomic, restart-proof.
5. **Lapsed** — main-town-only news; extras saved but locked (greyed +
   "Renew to unlock").
6. **Renew** — admin sets a new `pro_expires_at` (and plan if upgrading);
   reset `pro_notice_sent_for = 0`. Instant restore: extras unlock, their
   (kept-scanning) stories reappear immediately.

Edge cases:
- Renew *before* expiry: new expiry = old expiry + period (no lost days).
- Downgrade Elite → Pro with 8 extras saved: keep all rows; only the first
  6 (main + 5 oldest) are active, the rest greyed with "over plan limit".
  (Simplest rule that never deletes user data.)

---

## 4. Renewal email copy (plain text, admin BCC — same as sponsor mail)

**Subject:** `Your TownsLine Pro renews in 3 days`

```
Hi {username},

Your TownsLine Pro subscription renews on {YYYY-MM-DD} — 3 days from now.

You follow {N} towns: {Main} ★, {Town2}, {Town3}.
Renew to keep them all in one inbox — if it lapses, your extra towns are
kept but locked until you renew.

Renew here:
{https://townsline.com/subscription}

To cancel, just reply to this email.

— TownsLine
```

Elite variant: same body with "Pro" → "Elite". Lapsed users get no reminder
(nothing to renew *before*); the in-app banner (§2.4) covers the lapsed state.
`send_email(to, subject, body, bcc=ADMIN_EMAIL)` — the BCC rule from the
sponsor flow applies (never BCC mail carrying live secrets; this mail has
none).

---

## 5. Future Stripe integration plan (NOT implemented in Phase 1)

- **Products/prices** (Stripe Dashboard, CAD — prices TBD, see §7.1):
  `TownsLine Pro` — recurring monthly + yearly; `TownsLine Elite` —
  recurring monthly + yearly. Price IDs stored in server env/config, never
  hard-coded per user.
- **Checkout:** `/subscription/checkout?plan=pro&interval=yearly`
  (login + `admin_required` in Phase 1; later any user) →
  `stripe.checkout.Session.create(customer_email=user.email,
  client_reference_id=user.id, metadata={plan, interval}, mode='subscription',
  success_url=…/subscription?ok=1, cancel_url=…/subscription)`.
- **Webhook** `POST /stripe/webhook` (raw body, signature verified with
  `STRIPE_WEBHOOK_SECRET`):
  - `checkout.session.completed` → `pro_plan = metadata.plan`,
    `pro_expires_at = now + interval` (`metadata.interval`: month/year),
    `pro_notice_sent_for = 0`. Send grant confirmation email (§2.6).
  - `customer.subscription.updated` → adjust `pro_expires_at` from
    `current_period_end`; plan change pro↔elite updates `pro_plan`.
  - `customer.subscription.deleted` / `invoice.payment_failed` (final) →
    **do nothing to the columns**: let `pro_expires_at` pass. The lapse
    behavior (§2.4, §3.4) is identical to manual expiry — grey-out,
    kept towns, instant restore on re-subscribe. No dunning logic in v1.
  - `invoice.upcoming` (3 days) could later replace the daemon reminder;
    keep the daemon in v1 — it also covers manually granted subs.
- **Idempotency:** Stripe event IDs logged in a `stripe_events` table
  (or reuse the `pro_notice_sent_for` pattern: only apply a webhook if its
  `current_period_end > pro_expires_at`); retries are harmless.
- **Portal:** Stripe Customer Portal link on the Subscription page for card
  updates/cancellation (phase 2).

---

## 6. Gating: `pro_enabled(user)` & the zero-changes guarantee

One gate controls **every** Pro UI element — dashboard tiles, towns card,
grouped feeds, subscription card, places grouping:

```python
def pro_enabled(user):
    if not user: return False
    if user["username"] == ADMIN_USERNAME: return True   # admin preview
    return (user["pro_plan"] or "free") in ("pro", "elite")
```

- **Today (admin-only preview):** only the admin sees Pro UI. Everyone else
  gets byte-for-byte today's product.
- **At public launch nothing needs to change:** any user who ever held
  Pro/Elite gets the Pro UI automatically. Active subscribers get the full
  experience; lapsed ones get the greyed-out towns view (§2.2). Users who
  never subscribed see today's single-town product.
- New self-service routes (`/settings/towns/*`, `/settings/feeds/*/show_all`)
  carry `@pro_required` (mirrors the gate; non-Pro users are redirected).
- **Guarantee:** no existing route handler changes behavior for non-Pro
  users; no existing template changes its output for non-Pro users (every
  Pro block is wrapped in `{% if pro_enabled %}`, `{% else %}` keeps the
  current markup); migrations only add columns / backfill the new ones.
- Verification: render dashboard/Settings/Places for a non-Pro user
  before/after — output must be identical.

---

## 7. Open questions for Yuriy

**Decided 2026-10-16:**
- Adding towns while lapsed is **rejected** — `add_user_town()` returns
  "Subscription lapsed — renew to add extra towns." (Free users: "Extra
  towns need a Pro subscription.") Renew first, then add.
- Adding a town triggers an **instant scan** of its feeds (same as signup).
- Add-town uses the **Nominatim picker** (same UX as Settings).
- **Integrated, not separate:** Yuriy rejected the `/pro/*` preview pages.
  Pro lives in the real dashboard (town tiles), Settings (towns card,
  grouped feeds, subscription card) and Places — all behind `pro_enabled`.

1. **Public prices** — Kickstarter pack lists Pro $4.99/mo or $49/yr,
   Elite $9.99/mo or $99/yr. Are those the public prices too, or
   Kickstarter-only? (Prototype shows "TBD".)
2. **Renewal periods** — monthly + yearly for both tiers, or yearly-only to
   keep it simple at launch?
3. **Lapsed scanning** — should extra-town feeds keep scanning while lapsed
   (instant restore on renew, but wasted scan time), or pause scanning and
   re-scan on renew (cheaper, but a gap in history)?
4. **Reminder cadence** — 3-days-before only, or also a "renewed / expired"
   email on the day? (Sponsors get 3-day + auto-pause events; Pro could
   mirror that.)
5. **Grace period** — instant grey-out at expiry, or a few grace days? (Doc
   assumes instant, per the brief.)
6. **Downgrade overflow** — Elite→Pro with 8 extras: doc proposes keeping
   all, activating main + 5 oldest. OK, or should the user pick which 5?
7. **Android app** — does the multi-town model need to sync to the TownLine
   Android app later, or is Pro web-only for now?

---

## 8. Implementation checklist (for the build agent)

- [ ] Migration: 3 `users` columns, `user_towns` table, `feeds.town_slug`
      (guarded, existing `init_db()` convention)
- [ ] Backfill: main-town `user_towns` rows; `feeds.town_slug` = main slug
- [ ] `is_pro_active(user)`, `pro_town_limit(user)` helpers; town queries
      respect the computed predicate everywhere
- [ ] Seed-town flow sets `feeds.town_slug` for new towns (admin path)
- [ ] `/admin/pro` table + grant/edit/revoke form; confirmation email
- [ ] My towns card (Settings, `is_admin` only), add/remove town,
      limit enforcement server-side
- [ ] Dashboard: town selector, unified/per-town toggle (persisted),
      lapsed grey-out + banner
- [ ] `/subscription` page (admin sees full UI in Phase 1)
- [ ] Daemon: 3-day reminder in `renewal_loop` style, idempotent via
      `pro_notice_sent_for`; admin BCC
- [ ] Renew-before-expiry stacks (no lost days); downgrade-overflow rule
- [ ] Non-admin rendered-HTML diff check → identical to before
