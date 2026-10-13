# TownsLine web portal — research brief

> **Status update 2026-10-07 ~12:20 MDT:** the portal was already built by the main agent as a
> self-hosted **Python/Flask + SQLite** app at `~/workspace/townline-web/` (auth, per-user feeds,
> Nominatim location search, ported categorizer, background scanner, 16/16 checks passed — see
> `~/workspace/townline-web/README.md`). The stack analysis below is now background for the
> **deploy decision** (free Python hosts), not a build recommendation.

Prepared 2026-10-07 ~12:15 MDT for Yuriy's request: "Can you do the web version for this? ...
different users (by authentification) see the different content and can make their own settings ...
news hub portal. Add ability to manually set the location by place name/Postal Code/ZIP/etc."

## Why this is attractive for him specifically

- The web route **sidesteps the $25 Play fee** he refused today for TownsLine. Web + direct APK = full
  distribution with zero gatekeeper fees. This revives the commercialization thread without the blocker.
- The Android app's hard-won assets are portable: `feeds_directory.json` (11 verified Alberta towns),
  `places_directory.json` (Airdrie events), and the category "thinking" rules can be ported to JS/TS as-is.

## Decisive technical constraint

A browser **cannot fetch RSS feeds directly**. Google News RSS (and most news hosts) send no
`Access-Control-Allow-Origin` header, so `fetch()` from a web page is blocked — verified in a real
browser by an independent project. Consequences:
- A pure static page (GitHub Pages alone) can't scan feeds client-side.
- There must be a **server-side fetch layer** (tiny API or edge function) that pulls RSS and hands JSON
  to the frontend. Third-party CORS proxies (rss2json etc.) are flaky — not a foundation to build on.
- Bonus: server-side fetching also centralizes the scan, so per-user background scans become possible
  without each phone running its own JobScheduler.

## Verified free stack (all $0, no credit card)

**Frontend:** Cloudflare Pages or GitHub Pages — both free for static sites. GitHub Pages is familiar
territory (he already publishes there); Cloudflare Pages pairs naturally with the API below.
Start on the free `*.pages.dev` / `github.io` subdomain; a custom domain (~$15/yr) is optional later.

**Auth + per-user data:** Supabase free tier — 500 MB Postgres, 50,000 monthly active users, 1 GB file
storage, 5 GB/month egress, 500k edge-function invocations/month, unlimited API requests, email/password
plus Google/GitHub social login, no credit card required. Per-user feeds, settings, saved links, and
scan history map cleanly onto Postgres tables with row-level security.
- Caveat: free projects **pause after 7 days of inactivity** (10–30s cold start on wake). Fixable with a
  cheap keep-alive ping, or acceptable for a personal/friends MVP. Flag it honestly.

**Server-side feed fetching:** two free options —
- Supabase Edge Functions (500k invocations/month free): fetch feeds on demand when a user opens the
  portal or hits "Scan", cache results in Postgres, return JSON. No cron needed for the MVP.
- Cloudflare Workers free (100k requests/day, 10ms CPU/invocation, 5 cron triggers/account, static
  assets hosting): can host the whole frontend + a `/api/scan` endpoint + scheduled scans. Precedent:
  an open-source project runs exactly this pattern — a Cloudflare Worker serving a key-less news API
  over ~85 RSS feeds with open CORS.
- ⚠️ One project's note: Google News RSS may be **blocked from Cloudflare datacenter IPs**. Not
  independently confirmed — verify during the build; fallback is DiscoverAirdrie-style direct publisher
  feeds, which the app already prioritizes.

**Manual location entry (place name / postal code / ZIP):** OpenStreetMap Nominatim — free, no key,
resolves town names AND postal codes (`T4B 2B4` → Airdrie). Hard policy limits that shape the UI:
- **Max 1 request/second for the whole application** (not per user), identifying User-Agent required,
  results must be cached server-side, and **no search-as-you-type autocomplete**.
- UI consequence: "type town or postal code → press Search → pick from results", resolved once and
  stored per user. Never autocomplete. (Alternative for postal codes only: zippopotam.us, free, no key —
  unverified, optional.)

## Recommended MVP shape

1. Static frontend (Cloudflare Pages) — login, town/location picker, feed list, categories, saved links.
2. Supabase: Auth (offer Google sign-in — one click, no passwords) + Postgres (users' feeds, settings,
   town, scan cache).
3. One Edge Function / Worker endpoint: takes the user's feed list, fetches RSS server-side, applies the
   ported category rules, caches in Postgres, returns JSON.
4. Location: stored per user; manual entry resolved once via cached Nominatim.

## Open decisions for Yuriy (do not assume)

- Auth method: Google sign-in vs email/password (Google = zero password friction, but ties to Google).
- Whose accounts: new Cloudflare + Supabase accounts under his email, or under the agent's guidance —
  he creates them, agent wires them (credentials never in the repo).
- Privacy: collecting emails/login data means a simple privacy policy page is owed eventually —
  to-do, not an MVP blocker.
- Scope: MVP = his own feeds + settings first; multi-user polish (admin, abuse limits) after.

## Suggested next step for the main agent

Reply with the stack recommendation (Cloudflare Pages + Supabase free, $0) and the two/three decisions
above — don't start building until he picks auth method and confirms the free-account setup. Offer to
scaffold the repo (`townline-web`) with the ported feed directory once he says go.
