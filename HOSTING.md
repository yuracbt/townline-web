# Hosting TownLine Web for free

How to put the TownLine Web news hub on the internet for **$0**, so different
users from different locations can register and test it with their own towns,
feeds, and settings.

There are two good free paths. **Start with Option A** — it takes five minutes
and is perfect for testing. Move to Option B when you want a fixed address that
works while your computer is off.

---

## Option A — Cloudflare Quick Tunnel (fastest, recommended for testing)

Your computer runs the app; Cloudflare gives it a public `https://` address.
No account, no credit card, no router changes, no open ports. Anyone with the
link can register and use it from anywhere in the world.

### How it works

```
tester's browser → https://random-words.trycloudflare.com → Cloudflare
    → cloudflared (on your computer, dials OUT — nothing to open) → localhost:5000
```

### Step 1 — Install cloudflared (once)

| Your computer | Command |
|---|---|
| Linux (Debian/Ubuntu) | Download the `.deb` from https://github.com/cloudflare/cloudflared/releases and `sudo dpkg -i cloudflared-linux-amd64.deb` |
| Windows | `winget install --id Cloudflare.cloudflared` |
| macOS | `brew install cloudflare/cloudflare/cloudflared` |

Verify: `cloudflared --version`.

### Step 2 — Start TownLine Web

```bash
cd ~/workspace/townline-web
.venv/bin/python app.py
```

Check it loads at http://localhost:5000 in your own browser first.

### Step 3 — Open the tunnel (new terminal window)

```bash
cloudflared tunnel --url http://localhost:5000
```

After a few seconds it prints something like:

```
https://bright-panda-42.trycloudflare.com
```

**That is your public link.** Share it with your testers.

### Step 4 — What your testers do

1. Open the link on their phone or computer (works from any country).
2. **Register** their own account (username + password — remind them it's a
   test server, not to reuse a real password).
3. Go to **Settings → location search** and set their own town:
   - a place name: `Kyiv`, `Calgary`, `Cochrane`
   - a postal code: `T4B 0B5`
   - a US ZIP: `90210`
   
   Pick their town from the results — matching feeds are seeded automatically,
   or they can add any RSS/Atom feed URL on the Feeds page.
4. Their content is fully separate from everyone else's: own feeds, own
   stories, own read/saved marks, own scan interval.

### Good to know

- **Keep both terminal windows open.** Closing the tunnel (or Ctrl-C) takes the
  site offline; closing the app window stops the app.
- **The URL changes every time you restart the tunnel.** Send the new link when
  you restart. (A permanent address needs a Cloudflare account + your own
  domain — free too, but more setup; see the Cloudflare Tunnel docs.)
- Your computer must stay on and connected while people test.
- First page load through a fresh tunnel can take ~30 seconds — normal.
- The tunnel itself is encrypted end-to-end by Cloudflare, and the public URL
  is `https://`.

---

## Option B — Render (fixed address, computer can be off)

[Render](https://render.com) has a genuinely permanent free tier: 750 hours a
month, no credit card required. You get a fixed address like
`https://townline-web.onrender.com` that works even with your computer off.

### Honest limitations of the free tier (read before you choose)

- **Sleeps after 15 minutes with no visitors.** The next visit then takes
  about 60 seconds to wake up (cold start). Fine for testing, annoying for
  daily use.
- **While asleep, the background scanner doesn't run.** New stories are picked
  up on the next visit/wake.
- **The database is wiped on every restart/redeploy.** Render's free
  filesystem is ephemeral, and TownLine Web stores everything in a local
  SQLite file. For a test round this is acceptable (testers just re-register),
  but don't treat it as permanent storage. A permanent database needs a paid
  disk or an external free database (e.g. Supabase/Neon Postgres + code
  changes — out of scope for testing).
- Set a real `SECRET_KEY` (below) — never ship the default on a public URL.

### Step 1 — Put the code on GitHub

```bash
cd ~/workspace/townline-web
git init
git add -A
git commit -m "TownLine Web"
# create an empty repo on github.com, then:
git remote add origin https://github.com/YOURNAME/townline-web.git
git push -u origin main
```

### Step 2 — Create the web service

1. Sign up at https://render.com (email or GitHub login, no card).
2. **New → Web Service →** connect your `townline-web` repo.
3. Settings:
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `gunicorn --workers 1 --bind 0.0.0.0:$PORT app:app`
     
     (`--workers 1` matters: the feed scanner runs inside the app, and more
     workers would scan everything twice.)
   - **Instance Type:** Free
4. Under **Environment**, add:
   - `SECRET_KEY` → a long random string. Generate one with:
     `python3 -c "import secrets; print(secrets.token_hex(32))"`
   - (Optional) `PYTHON_VERSION` → `3.12.0`
5. **Deploy.** First build takes a few minutes. Your URL will be
   `https://townline-web.onrender.com` (or whatever name you chose).

### Step 3 — Test it

Same tester flow as Option A: register, set location by name/postal/ZIP, add
feeds. If the page takes ~60s to load, it's just waking up — refresh once.

### If something breaks

- **"No open ports detected"** — the Start Command must bind `0.0.0.0:$PORT`
  exactly as above.
- **Build fails on Python version** — set `PYTHON_VERSION` to `3.12.0`.
- **App is up but feeds never scan** — check the Logs tab; the scanner logs
  each cycle. Remember it sleeps with the service.

---

## Option C — Oracle Cloud Always Free (always-on, most setup)

If you outgrow both options and want a server that never sleeps: Oracle Cloud's
Always Free tier gives you a real virtual machine, forever free. As of 2026 the
ARM allowance is **2 OCPUs / 12 GB RAM** (halved from the old 4/24 — still far
more than this app needs), plus 200 GB storage and 10 TB/month bandwidth.

### Staying on Always Free — the checklist

1. **Never upgrade to Pay-As-You-Go.** A new account is locked to the Free
   Tier: Oracle physically blocks you from creating anything beyond the free
   limits until you manually upgrade. This one rule is 95% of the safety.
2. **Stay inside the Always Free caps** (current as of mid-2026):
   - Ampere A1 (`VM.Standard.A1.Flex`): **2 OCPU + 12 GB RAM total**
     (one 2/12 VM, or two 1/6 VMs)
   - AMD E2 micro: 2 instances (tiny — not needed for this app)
   - Block storage: **200 GB total** including the boot volume (default 47 GB
     is fine — don't enlarge it past what you need)
   - Outbound bandwidth: 10 TB/month (this app uses a trickle)
   - When creating anything, the console labels it **"Always Free eligible"**
     — only ever pick those.
3. **Watch the three dashboards** (all under ☰ → Billing & Cost Management):
   - **Cost Analysis** — actual spend; should sit at $0.
   - **Budgets** — the $0 budget alert from the guide above.
   - **Limits, Quotas and Usage** (under Governance & Administration) —
     shows consumption vs. quota per resource.
4. **Don't create what you don't need.** No load balancers, no extra block
   volumes, no databases — this app needs exactly one VM and its boot volume.

- Needs a credit card **for identity verification only** (a small temporary
  hold, released; not charged). Stay on the Always Free tier — don't upgrade to
  pay-as-you-go.
- **Pick your home region carefully** (Canada Southeast / Toronto is closest to
  Airdrie) — it can never be changed.
- Set a **$0 billing budget alert** (Billing → Budgets) so you'd hear about it
  if anything ever stopped being free.
- "Out of host capacity" errors are common on the free ARM shape — just retry,
  or try again in a few hours.
- Once the VM exists, one script does the rest: it installs Python, Caddy
  (automatic HTTPS), and TownLine Web as a service. See
  [`deploy/oracle-setup.sh`](deploy/oracle-setup.sh):
  `./oracle-setup.sh your-domain.com` on a fresh Ubuntu 24.04 VM, then point
  your domain's A record at the VM and open the ingress ports 80/443 in the
  VCN security list.

---

## Why not the other free hosts?

- **PythonAnywhere (free):** blocks outbound internet to non-whitelisted
  domains — the feed scanner and the Nominatim location search would fail.
- **Railway / Fly.io:** no longer permanently free (trial credits only).
- **Vercel/Netlify:** serverless only — no background scanner thread, no
  persistent process. Wrong shape for this app.

---

## Suggested test plan (works on any option)

| # | Tester | Location to set | What to check |
|---|---|---|---|
| 1 | You | `Airdrie` or `T4B 0B5` | Local feeds seeded; categories look right |
| 2 | Someone in another city | `Calgary`, `Edmonton`, or a postal code | Different feeds, different stories |
| 3 | Someone abroad | `Kyiv`, `90210`, `London` | Location search works worldwide; isolation holds |
| 4 | Everyone | — | Register/login, wrong password rejected, saved stories, mark-all-read, scan interval change |

Isolation check: tester 2 must see **zero** of tester 1's stories on their
dashboard — that confirms per-user separation is working.
