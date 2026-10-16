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
    created_at INTEGER NOT NULL DEFAULT 0,
    UNIQUE (user_id, town_slug)
);
CREATE INDEX idx_user_towns_user ON user_towns(user_id);
```

- `is_main = 1` — the user's home town, backfilled from the `settings` table
  (see §1.4). The main town can never be removed and is never greyed out.
- Extra towns: count limit = `5` for Pro, `10` for Elite. Enforced at insert
  (UI + server-side check).
- In Phase 1 only the admin user gets extra towns; the schema is multi-user
  ready.

### 1.3 `feeds` — new column

| Column | Type | Default | Meaning |
|---|---|---|---|
| `town_slug` | TEXT | `''` | Which town this feed belongs to. `''` = legacy / unassigned |

Stories map to towns via the feed join: `stories.feed_id → feeds.town_slug`.
Set at feed-seeding time for each town (mirror of the existing town-aware
signup seeding: curated feeds + directory places + auto category feeds per
town).

### 1.4 Migration notes (existing DBs)

SQLite `ALTER TABLE … ADD COLUMN` is supported; follow the existing pattern in
`init_db()` (cf. the `sponsors.notice_sent_for` migration ~line 855):

```sql
ALTER TABLE users ADD COLUMN pro_plan TEXT DEFAULT 'free';
ALTER TABLE users ADD COLUMN pro_expires_at INTEGER DEFAULT 0;
ALTER TABLE users ADD COLUMN pro_notice_sent_for INTEGER DEFAULT 0;
ALTER TABLE feeds ADD COLUMN town_slug TEXT DEFAULT '';
```

Guard each with `PRAGMA table_info` checks (existing convention).

**Backfill:**
1. For every user with a `settings.town_name`, insert one `user_towns` row
   with `is_main = 1`, `town_slug = town_slug_for(town_name)`, `lat/lon` from
   the settings/geocode cache where available.
2. For every user's existing feeds (`''` town_slug), set
   `feeds.town_slug = <their main town slug>` — so the current single-town
   behavior is preserved exactly for everyone.

---

## 2. UX flows (per state)

All flows below describe the **target** UX. In Phase 1 it renders only for the
admin user; the prototype demonstrates it with dummy data.

### 2.1 Never had Pro (admin on Free plan)

- **My towns** card: only the main town listed, marked `★ Main`.
- **Add a town** → prompts to upgrade: "Pro unlocks up to 5 extra towns"
  with a link to the Subscription page. No town can be added.
- News line: unchanged — main town only (identical to today's dashboard).
- Subscription page: Free plan card highlighted; Pro/Elite cards show
  **Upgrade** buttons (disabled in Phase 1 with note "manual grant by admin"
  — or admin self-grant flow, see §2.5).

### 2.2 Active Pro

- **My towns**: main `★ Main` + extras (e.g. 3/6 used). Each extra town row:
  name, added date, `Remove` link, story count.
- **Add a town**: town search (reuse the existing `/towns` search), adds feed
  set + instant scan for that town. Blocked with a clear message at 5 extras.
- News line: town selector + **view toggle**: `All together` (unified, §2.6)
  | `By town` (grouped, §2.7). Preference persisted per user (settings or a
  `users.news_view` column — implementer's choice; settings table is fine).
- Subscription page: Pro card highlighted, status badge **Active**,
  `Renews 2027-10-15`, towns used `3/6`, **Renew** button (extends expiry),
  **Upgrade to Elite** button.

### 2.3 Active Elite

Same as Pro, with limit 11 (`8/11` style counter) and an **Elite** badge.
Upgrade button hidden (top tier); Renew still available.

### 2.4 Lapsed (expired or downgraded)

- **My towns**: main town normal; every extra town row is **greyed out**,
  non-clickable, with hint `Renew to unlock`. Towns stay saved (nothing is
  deleted). User may *remove* a greyed town but not add new ones.
- News line: **main town only**, exactly like Free. No stories from extra
  towns appear; the town selector shows extras greyed.
- A slim banner on the news page: `Your Pro expired on 2026-10-12 — renew to
  unlock Lviv, Dublin.` → links to Subscription.
- Subscription page: status badge **Expired**, expiry date shown,
  **Renew** button prominent. Renew → instant restore: all extra towns live
  again, news back in the line, no re-seeding needed (feeds kept scanning —
  see open question §7.4).

### 2.5 Upgrade Free → Pro → Elite (Phase 1: admin-granted)

Since Stripe is not built yet, "upgrade"/"renew" in Phase 1 = the admin
granting via the Admin panel (see §2.6). The user-facing buttons on the
Subscription page may POST to an endpoint that, in Phase 1, flashes
"Subscriptions open soon — contact us" **except when the admin is viewing**,
where the grant form is shown. (Simpler alternative: buttons visible only to
admin; regular users never see this page at all in Phase 1.)

### 2.6 Admin granting

New Admin panel section **Pro subscriptions** (`/admin/pro`, `admin_required`):
- Table of users: username, email, plan badge, expiry date, towns used
  (`user_towns` count / limit), status (active/lapsed).
- Row action **Grant / edit**: form with plan select (`free/pro/elite`),
  expiry date picker (default: +1 year), submit → sets `pro_plan`,
  `pro_expires_at`, resets `pro_notice_sent_for = 0` (so a fresh reminder
  cycle starts). Also **Revoke** (set plan `free`, keep towns saved but
  greyed).
- Confirmation email to the user on grant (admin BCC), plain text:

  > Subject: Your TownsLine Pro is active
  >
  > Hi {username},
  >
  > Your TownsLine Pro subscription is now active until {date}.
  > You can follow up to 5 extra towns alongside {main town} — add them
  > in Settings → My towns.
  >
  > — TownsLine

### 2.7 News line views (admin user)

- **Unified** (`All together`): one `<ul class="stories">`, all towns'
  stories interleaved by publish time, each row carrying a town chip
  (`.town-chip`) next to the category pill. The existing story row markup is
  reused; only the chip is added.
- **Per-town** (`By town`): same data, grouped under slim town headers
  (`Airdrie — 4 new`, …), each group its own `<ul class="stories">`.
- Toggle: a segmented control in the dashhead. One click switches; the choice
  persists. When lapsed, the toggle still works but only the main town has
  stories.

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

## 6. Admin-only gating & the zero-changes guarantee

- Every new route lives under `/admin/pro*` and carries `@admin_required`
  (non-admins are redirected to the dashboard with the existing flash —
  same as `/admin/sponsors*`).
- The admin's Pro news UI (town selector, unified/per-town toggle, My towns
  card) renders **only when `is_admin`** (the existing context processor).
  Template conditionals wrap the new blocks; the `{% else %}` branch is the
  current markup, byte-identical.
- Nav: a **Pro** link appears in the Admin panel page only — not in the
  topbar for regular users.
- **Guarantee:** no existing route handler changes behavior for non-admin
  users; no existing template changes its output for non-admin users; no
  migration touches existing column values (backfill only *adds* rows /
  fills the new `feeds.town_slug` with the user's current town).
- Verification for the implementation agent: `diff` the rendered dashboard
  HTML for a non-admin user before/after the change — must be identical
  (modulo CSRF-ish tokens/nonces).

---

## 7. Open questions for Yuriy

**Decided 2026-10-16:** adding towns while lapsed is **rejected** —
`add_user_town()` returns "Subscription lapsed — renew to add extra towns."
(Free users: "Extra towns need a Pro subscription.") Renew first, then add.

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
