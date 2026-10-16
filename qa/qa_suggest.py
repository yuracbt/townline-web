"""QA: per-town place suggestions — selected-town follow, on-demand scan, cache."""
import os, sys, tempfile, time
tmp = tempfile.mkdtemp()
sys.path.insert(0, os.path.expanduser("~/workspace/townline-web"))
import app as A

A.DB_PATH = os.path.join(tmp, "q.db")
A.init_db()
A.ADMIN_USERNAME = "qa_admin"
A.app.config["TESTING"] = True
c = A.app.test_client()
passed, failed = 0, 0

def check(name, cond, extra=""):
    global passed, failed
    if cond: passed += 1; print("PASS " + name)
    else: failed += 1; print("FAIL " + name + (" — " + str(extra) if extra else ""))

NOW = int(time.time())
with A.app.app_context():
    db = A.get_db()
    def mkuser(name, email, pro="free", exp=0, town="Airdrie"):
        db.execute("INSERT INTO users (username,email,pw_hash,created_at,pro_plan,pro_expires_at)"
                   " VALUES (?,?,?,?,?,?)", (name, email, "x", NOW, pro, exp))
        uid = db.execute("SELECT id FROM users WHERE username=?", (name,)).fetchone()["id"]
        db.execute("INSERT INTO settings (user_id,town_name,lat,lon) VALUES (?,?,?,?)",
                   (uid, town, 51.29, -114.01))
        db.commit(); return uid
    admin = mkuser("qa_admin", "a@t.c", "pro", NOW + 99999)
    reader = mkuser("qa_reader", "r@t.c")
    def town(uid, slug, name, lat, lon, pos, main):
        db.execute("INSERT INTO user_towns (user_id,town_slug,town_name,lat,lon,sort_order,is_main,created_at)"
                   " VALUES (?,?,?,?,?,?,?,?)", (uid, slug, name, lat, lon, pos, 1 if main else 0, NOW))
    town(admin, "airdrie", "Airdrie", 51.29, -114.01, 0, True)
    town(admin, "fredericton", "Fredericton", 45.96, -66.64, 1, False)
    db.commit()

def login(uid):
    with c.session_transaction() as s: s["user_id"] = uid

# fake OSM: never hit the network in tests
A._osm_cache.clear()
A.discover_osm_places = lambda lat, lon, town, cache_key=None: (
    A._osm_cache.__setitem__(cache_key, (time.time(), [{"name": "OSM Fake Hall", "kind": "City hall",
        "url": "https://osm.test/hall", "origin": "nearby"}])) or A._osm_cache[cache_key][1])

login(admin)

# 1. town param resolves to the selected town, not scanned yet
r = c.get("/api/places/suggestions?town=fredericton").get_json()
check("api: town=fredericton resolves", r["town"] == "Fredericton" and r["town_slug"] == "fredericton", r)
check("api: scanned=false before first scan", r["scanned"] is False, r)

# 2. invalid slug falls back to main
r = c.get("/api/places/suggestions?town=moscow").get_json()
check("api: invalid slug falls back to main", r["town"] == "Airdrie" and r["town_slug"] == "airdrie", r)

# 3. on-demand scan populates cache
r = c.get("/api/places/suggestions?town=fredericton&scan=1").get_json()
check("api: scan=1 returns scanned=true", r["scanned"] is True)
check("api: scan=1 includes OSM results",
      any(s["name"] == "OSM Fake Hall" for s in r["suggestions"]), r["suggestions"])

# 4. second call served from cache — no rescan needed
calls = []
orig = A.discover_osm_places
A.discover_osm_places = lambda *a, **k: (calls.append(1), orig(*a, **k))[1]
r = c.get("/api/places/suggestions?town=fredericton").get_json()
check("api: cached town served without rescan", r["scanned"] is True and not calls)
A.discover_osm_places = orig

# 5. refresh drops cache and rescans
r = c.get("/api/places/suggestions?town=fredericton&refresh=1").get_json()
check("api: refresh=1 rescans", r["scanned"] is True and any(s["name"] == "OSM Fake Hall" for s in r["suggestions"]))

# 6. non-Pro: town param ignored, own town used
login(reader)
r = c.get("/api/places/suggestions?town=fredericton").get_json()
check("api: reader town param ignored", r["town"] == "Airdrie" and r["town_slug"] == "", r)

# 7. template: header + add-place default follow the selected town
login(admin)
h = c.get("/places?town=fredericton").get_data(as_text=True)
check("places: header shows selected town",
      "Suggested places for <span id=\"place-town\">Fredericton</span>" in h)
check("places: add-place defaults to selected town",
      '<option value="fredericton" selected>' in h)
h = c.get("/places?town=all").get_data(as_text=True)
check("places: all-towns defaults add-place to main",
      '<option value="airdrie" selected>' in h)

# 8. place_add validates town_slug
login(admin)
c.post("/places/add", data={"name": "Evil Place", "url": "https://evil.test/x", "town_slug": "moscow"})
with A.app.app_context():
    v = A.get_db().execute("SELECT town_slug FROM places WHERE url='https://evil.test/x'").fetchone()["town_slug"]
check("place_add: forged slug rejected", (v or "") == "", v)
c.post("/places/add", data={"name": "Fred Place", "url": "https://fred.test/x", "town_slug": "fredericton"})
with A.app.app_context():
    v = A.get_db().execute("SELECT town_slug FROM places WHERE url='https://fred.test/x'").fetchone()["town_slug"]
check("place_add: valid slug stored", v == "fredericton", v)

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
