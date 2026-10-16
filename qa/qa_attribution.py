"""TownsLine Pro QA: town-attribution integrity (admin + regular user).

Isolated temp DB. Seeds an admin (Pro, 3 towns) and a free reader, then
verifies that every feed, story and place resolves to the right town —
in the DB and through the real routes (dashboard / settings / places).
"""
import os, sys, tempfile, time
tmp = tempfile.mkdtemp()
sys.path.insert(0, os.path.expanduser("~/workspace/townline-web"))
import app as A

A.DB_PATH = os.path.join(tmp, "qa.db")
A.init_db()
A.ADMIN_USERNAME = "qa_admin"
A.app.config["TESTING"] = True
c = A.app.test_client()

passed, failed = 0, 0
def check(name, cond, extra=""):
    global passed, failed
    if cond:
        passed += 1
        print("PASS " + name)
    else:
        failed += 1
        print("FAIL " + name + (" — " + str(extra) if extra else ""))

NOW = int(time.time())
ADMIN, READER = None, None
FEEDS, PLACES = {}, {}

with A.app.app_context():
    db = A.get_db()
    def mkuser(name, email, pro_plan="free", expires=0):
        db.execute("INSERT INTO users (username, email, pw_hash, created_at,"
                   " pro_plan, pro_expires_at) VALUES (?,?,?,?,?,?)",
                   (name, email, "x", NOW, pro_plan, expires))
        uid = db.execute("SELECT id FROM users WHERE username=?",
                         (name,)).fetchone()["id"]
        db.execute("INSERT INTO settings (user_id, town_name, lat, lon, theme)"
                   " VALUES (?,?,?,?,?)", (uid, "Airdrie", 51.29, -114.01, "light"))
        db.commit()
        return uid
    ADMIN = mkuser("qa_admin", "a@t.com", "pro", NOW + 30 * 86400)
    READER = mkuser("qa_reader", "r@t.com")

    def town(uid, slug, name, lat, lon, pos, main):
        db.execute("INSERT INTO user_towns (user_id, town_slug, town_name, lat,"
                   " lon, sort_order, is_main, created_at)"
                   " VALUES (?,?,?,?,?,?,?,?)",
                   (uid, slug, name, lat, lon, pos, 1 if main else 0, NOW))
    town(ADMIN, "airdrie", "Airdrie", 51.29, -114.01, 0, True)
    town(ADMIN, "banff", "Banff", 51.17, -115.57, 1, False)
    town(ADMIN, "calgary", "Calgary", 51.05, -114.07, 2, False)

    def feed(uid, name, url, tslug, show_all=0):
        db.execute("INSERT INTO feeds (user_id, name, url, town_slug, show_all)"
                   " VALUES (?,?,?,?,?)", (uid, name, url, tslug, show_all))
        return db.execute("SELECT id FROM feeds WHERE user_id=? AND url=?",
                          (uid, url)).fetchone()["id"]
    FEEDS["airdrie"] = feed(ADMIN, "QA Airdrie News", "https://qa-a.test/rss", "airdrie")
    FEEDS["banff"] = feed(ADMIN, "QA Banff News", "https://qa-b.test/rss", "banff")
    FEEDS["calgary"] = feed(ADMIN, "QA Calgary News", "https://qa-c.test/rss", "calgary")
    FEEDS["legacy"] = feed(ADMIN, "QA Legacy Feed", "https://qa-l.test/rss", "")
    FEEDS["shared"] = feed(ADMIN, "QA Shared Feed", "https://qa-s.test/rss", "calgary", 1)
    FEEDS["reader"] = feed(READER, "QA Reader Feed", "https://qa-r.test/rss", "")

    def place(uid, name, url, tslug):
        db.execute("INSERT INTO places (user_id, name, url, town_slug)"
                   " VALUES (?,?,?,?)", (uid, name, url, tslug))
        return db.execute("SELECT id FROM places WHERE user_id=? AND url=?",
                          (uid, url)).fetchone()["id"]
    PLACES["airdrie"] = place(ADMIN, "QA Airdrie Place", "https://qa-ap.test", "airdrie")
    PLACES["banff"] = place(ADMIN, "QA Banff Place", "https://qa-bp.test", "banff")
    PLACES["calgary"] = place(ADMIN, "QA Calgary Place", "https://qa-cp.test", "calgary")
    PLACES["legacy"] = place(ADMIN, "QA Legacy Place", "https://qa-lp.test", "")
    PLACES["reader"] = place(READER, "QA Reader Place", "https://qa-rp.test", "")

    def story(uid, fid, title):
        db.execute("INSERT INTO stories (user_id, feed_id, title, url, published,"
                   " created_at) VALUES (?,?,?,?,?,?)",
                   (uid, fid, title, "https://qa.test/" + title.replace(" ", ""),
                    NOW, NOW))
    story(ADMIN, FEEDS["airdrie"], "QA Story Airdrie")
    story(ADMIN, FEEDS["banff"], "QA Story Banff")
    story(ADMIN, FEEDS["calgary"], "QA Story Calgary")
    story(ADMIN, FEEDS["legacy"], "QA Story Legacy")
    story(ADMIN, FEEDS["shared"], "QA Story Shared")
    story(READER, FEEDS["reader"], "QA Story Reader")
    db.commit()

def q(sql, args=()):
    with A.app.app_context():
        return [dict(r) for r in A.get_db().execute(sql, args).fetchall()]

def login(uid):
    with c.session_transaction() as s:
        s["user_id"] = uid

def titles(html):
    return [t for t in ("QA Story Airdrie", "QA Story Banff", "QA Story Calgary",
                        "QA Story Legacy", "QA Story Shared") if t in html]

# ---- 1. DB attribution integrity -------------------------------------
feeds = q("SELECT * FROM feeds WHERE user_id=?", (ADMIN,))
slugs = {t["town_slug"] for t in q("SELECT town_slug FROM user_towns WHERE user_id=?", (ADMIN,))}
check("feeds: every town_slug valid or empty",
      all((not f["town_slug"]) or f["town_slug"] in slugs for f in feeds))
places = q("SELECT * FROM places WHERE user_id=?", (ADMIN,))
check("places: every town_slug valid or empty",
      all((not p["town_slug"]) or p["town_slug"] in slugs for p in places))
stories = q("SELECT s.*, f.user_id AS fuser, COALESCE(f.town_slug,'') AS fslug "
            "FROM stories s LEFT JOIN feeds f ON f.id=s.feed_id WHERE s.user_id=?", (ADMIN,))
check("stories: every feed exists, same owner, no cross-user leak",
      all(s["fuser"] == ADMIN for s in stories) and len(stories) == 5)
eff = {s["title"]: (s["fslug"] or "airdrie") for s in stories}
check("stories: effective town mapping correct",
      eff == {"QA Story Airdrie": "airdrie", "QA Story Banff": "banff",
              "QA Story Calgary": "calgary", "QA Story Legacy": "airdrie",
              "QA Story Shared": "calgary"}, eff)

# ---- 2. Dashboard tiles + filtering (admin) ---------------------------
login(ADMIN)
h = c.get("/").get_data(as_text=True)
check("dashboard: tiles row present", 'class="towntiles"' in h)
check("dashboard: All towns + pinned main + extras in order",
      h.index("All towns") < h.index("📌 Airdrie") < h.index("Banff") < h.index("Calgary"))
check("dashboard: ?town=banff shows Banff + shared only",
      sorted(titles(c.get("/?town=banff").get_data(as_text=True))) == ["QA Story Banff", "QA Story Shared"])
check("dashboard: ?town=airdrie shows Airdrie + legacy + shared",
      sorted(titles(c.get("/?town=airdrie").get_data(as_text=True))) == ["QA Story Airdrie", "QA Story Legacy", "QA Story Shared"])
check("dashboard: ?town=all shows everything",
      sorted(titles(c.get("/?town=all").get_data(as_text=True))) == ["QA Story Airdrie", "QA Story Banff", "QA Story Calgary", "QA Story Legacy", "QA Story Shared"])
c.get("/?town=banff")
check("dashboard: filter persists (banff sticks)",
      sorted(titles(c.get("/").get_data(as_text=True))) == ["QA Story Banff", "QA Story Shared"])
c.get("/?town=all")  # reset

# ---- 3. Settings feeds grouping --------------------------------------
h = c.get("/settings").get_data(as_text=True)
ia, ib, ic = h.index("📌 Airdrie</h3>"), h.index(">Banff</h3>"), h.index(">Calgary</h3>")
check("settings: feed groups in town order", ia < ib < ic)
seg_a, seg_b, seg_c = h[ia:ib], h[ib:ic], h[ic:ic + 4000]
check("settings: Airdrie group has Airdrie + Legacy feeds",
      "QA Airdrie News" in seg_a and "QA Legacy Feed" in seg_a and "QA Banff News" not in seg_a)
check("settings: Banff group has only Banff feed",
      "QA Banff News" in seg_b and "QA Calgary News" not in seg_b)
check("settings: Calgary group has Calgary + Shared feeds",
      "QA Calgary News" in seg_c and "QA Shared Feed" in seg_c)

# ---- 4. Places filtering ----------------------------------------------
def place_names(html):
    return [n for n in ("QA Airdrie Place", "QA Banff Place", "QA Calgary Place",
                        "QA Legacy Place") if n in html]
check("places: ?town=banff shows only Banff place",
      place_names(c.get("/places?town=banff").get_data(as_text=True)) == ["QA Banff Place"])
check("places: ?town=all groups all places",
      place_names(c.get("/places?town=all").get_data(as_text=True)) ==
      ["QA Airdrie Place", "QA Banff Place", "QA Calgary Place", "QA Legacy Place"])
check("places: legacy place follows main (Airdrie)",
      "QA Legacy Place" in c.get("/places?town=airdrie").get_data(as_text=True))

# ---- 5. make-main backfill ---------------------------------------------
banff_id = q("SELECT id FROM user_towns WHERE user_id=? AND town_slug='banff'", (ADMIN,))[0]["id"]
c.post("/settings/towns/make_main", data={"tid": banff_id}, follow_redirects=True)
tags = {r["name"]: (r["town_slug"] or "") for r in
        q("SELECT name, town_slug FROM feeds WHERE user_id=?", (ADMIN,))}
check("make-main: untagged feed pinned to OLD main (airdrie)",
      tags["QA Legacy Feed"] == "airdrie", tags)
check("make-main: tagged feeds untouched",
      tags["QA Airdrie News"] == "airdrie" and tags["QA Banff News"] == "banff"
      and tags["QA Calgary News"] == "calgary")
ptags = {r["name"]: (r["town_slug"] or "") for r in
         q("SELECT name, town_slug FROM places WHERE user_id=?", (ADMIN,))}
check("make-main: untagged place pinned to OLD main (airdrie)",
      ptags["QA Legacy Place"] == "airdrie", ptags)
main = q("SELECT town_slug FROM user_towns WHERE user_id=? AND is_main=1", (ADMIN,))[0]["town_slug"]
check("make-main: Banff is now main", main == "banff")
h = c.get("/?town=airdrie").get_data(as_text=True)
check("dashboard: legacy story still under Airdrie after flip",
      "QA Story Legacy" in h and "QA Story Airdrie" in h)

# ---- 6. Attribution editor ----------------------------------------------
c.post(f"/admin/pro/{ADMIN}/reassign",
       data={f"place_{PLACES['banff']}": "calgary"}, follow_redirects=True)
v = q("SELECT town_slug FROM places WHERE id=?", (PLACES["banff"],))[0]["town_slug"]
check("admin editor: place retagged banff->calgary", v == "calgary")
check("places: moved place now under Calgary",
      "QA Banff Place" in c.get("/places?town=calgary").get_data(as_text=True))
check("places: no longer under Banff",
      "QA Banff Place" not in c.get("/places?town=banff").get_data(as_text=True))

# ---- 7. Lapsed subscription ----------------------------------------------
with A.app.app_context():
    db = A.get_db()
    db.execute("UPDATE users SET pro_expires_at=? WHERE id=?", (NOW - 10, ADMIN))
    db.commit()
h = c.get("/").get_data(as_text=True)
check("lapsed: extra towns greyed, not links",
      'class="towntile greyed"' in h and ">Airdrie</span>" in h and ">Calgary</span>" in h
      and 'href="/?town=airdrie"' not in h and 'href="/?town=calgary"' not in h)
check("lapsed: main town reachable (banff is main now)",
      sorted(titles(c.get("/?town=banff").get_data(as_text=True))) == ["QA Story Banff"])
check("lapsed: ?town=airdrie falls back to main",
      sorted(titles(c.get("/?town=airdrie").get_data(as_text=True))) == ["QA Story Banff"])
check("lapsed: ?town=all shows main-town news only (no extra towns, no shared)",
      sorted(titles(c.get("/?town=all").get_data(as_text=True))) == ["QA Story Banff"])
# renew
with A.app.app_context():
    db = A.get_db()
    db.execute("UPDATE users SET pro_expires_at=? WHERE id=?", (NOW + 30 * 86400, ADMIN))
    db.commit()
h = c.get("/?town=all").get_data(as_text=True)
check("renewed: full news line restored", len(titles(h)) == 5)

# ---- 8. Regular user: no Pro anywhere ------------------------------------
login(READER)
h = c.get("/").get_data(as_text=True)
check("reader: no town tiles", 'class="towntiles"' not in h and "All towns" not in h)
check("reader: sees own story", "QA Story Reader" in h)
check("reader: no admin story leak", "QA Story Airdrie" not in h)
h = c.get("/settings").get_data(as_text=True)
check("reader: no Pro towns card", 'id="add-town-btn"' not in h and 'class="towntile' not in h)
h = c.get("/places").get_data(as_text=True)
check("reader: no tiles on places, own place shown",
      'class="towntiles"' not in h and "QA Reader Place" in h)
r = c.get(f"/admin/pro/{ADMIN}")
check("reader: admin area blocked", r.status_code in (302, 403))
r = c.post(f"/admin/pro/{ADMIN}/reassign", data={})
check("reader: admin editor POST blocked", r.status_code in (302, 403))

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
