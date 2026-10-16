import os, sqlite3, sys, tempfile, time
tmp = tempfile.mkdtemp()
os.environ["TOWNLINE_DB"] = os.path.join(tmp, "t.db")
sys.path.insert(0, os.path.expanduser("~/workspace/townline-web"))
import app as A
A.DB_PATH = os.path.join(tmp, "t.db")
A.init_db()
from werkzeug.security import generate_password_hash as gph

A.app.config["TESTING"] = True
c = A.app.test_client()
passed = failed = 0

def check(name, cond):
    global passed, failed
    if cond:
        passed += 1
    else:
        failed += 1
    print(("PASS " if cond else "FAIL ") + name)

with A.app.app_context():
    db = A.get_db()

    def mkuser(name, email):
        db.execute(
            "INSERT INTO users (username, email, pw_hash, created_at)"
            " VALUES (?,?,?,?)",
            (name, email, gph("x"), 12345))
        uid = db.execute(
            "SELECT id FROM users WHERE username=?", (name,)).fetchone()["id"]
        db.execute(
            "INSERT INTO settings (user_id, town_name, lat, lon, theme)"
            " VALUES (?,?,?,?,?)",
            (uid, "Airdrie", 51.29, -114.01, "light"))
        db.commit()
        return uid

    admin = mkuser("qaadmin", "a@x.com")
    user = mkuser("qau", "u@x.com")
    db.execute("UPDATE users SET pro_plan='pro', pro_expires_at=? WHERE id=?",
               (1794723035, user))
    now = 1792131069
    db.execute("INSERT INTO user_towns (user_id, town_slug, town_name, lat,"
               " lon, sort_order, is_main, created_at) VALUES (?,?,?,?,?,?,?,?)",
               (user, "airdrie", "Airdrie", 51.29, -114.01, 0, 1, now))
    db.execute("INSERT INTO user_towns (user_id, town_slug, town_name, lat,"
               " lon, sort_order, is_main, created_at) VALUES (?,?,?,?,?,?,?,?)",
               (user, "banff", "Banff", 51.17, -115.57, 1, 0, now))
    # corrupted state: Airdrie items wrongly tagged 'banff'
    db.execute("INSERT INTO places (user_id, name, url, town_slug) VALUES (?,?,?,?)",
               (user, "Airdrie Chamber", "https://x.ca", "banff"))
    db.execute("INSERT INTO places (user_id, name, url, town_slug) VALUES (?,?,?,?)",
               (user, "Banff Real Place", "https://y.ca", "banff"))
    db.execute("INSERT INTO feeds (user_id, name, url, town_slug)"
               " VALUES (?,?,?,?)",
               (user, "Airdrie Feed", "https://a.ca/rss", "banff"))
    db.execute("INSERT INTO feeds (user_id, name, url, town_slug)"
               " VALUES (?,?,?,?)",
               (user, "Banff Feed", "https://b.ca/rss", "banff"))
    db.commit()
    pids = [r["id"] for r in db.execute(
        "SELECT id FROM places WHERE user_id=? ORDER BY id", (user,))]
    fids = [r["id"] for r in db.execute(
        "SELECT id FROM feeds WHERE user_id=? ORDER BY id", (user,))]
    A.ADMIN_USERNAME = "qaadmin"

with c.session_transaction() as s:
    s["user_id"] = admin

r = c.get(f"/admin/pro/{user}")
t = r.get_data(as_text=True)
check("editor card shown", "Feed &amp; place attribution" in t)
check("place rows listed", "Airdrie Chamber" in t and "Banff Real Place" in t)
check("feed rows listed", "Airdrie Feed" in t and "Banff Feed" in t)
check("banff preselected for corrupted rows",
      t.count('value="banff" selected') >= 4)

form = {f"place_{pids[0]}": "airdrie", f"place_{pids[1]}": "banff",
        f"feed_{fids[0]}": "airdrie", f"feed_{fids[1]}": "banff"}
r = c.post(f"/admin/pro/{user}/reassign", data=form, follow_redirects=True)
check("reassign flash", "Updated" in r.get_data(as_text=True))
with A.app.app_context():
    db = A.get_db()
    tags = {r["name"]: r["town_slug"] for r in db.execute(
        "SELECT name, town_slug FROM places WHERE user_id=?", (user,))}
    check("place moved to airdrie", tags["Airdrie Chamber"] == "airdrie")
    check("banff place untouched", tags["Banff Real Place"] == "banff")
    ftags = {r["name"]: r["town_slug"] for r in db.execute(
        "SELECT name, town_slug FROM feeds WHERE user_id=?", (user,))}
    check("feed moved to airdrie", ftags["Airdrie Feed"] == "airdrie")
    check("banff feed untouched", ftags["Banff Feed"] == "banff")

r = c.post(f"/admin/pro/{user}/reassign",
           data={f"place_{pids[0]}": "moscow"}, follow_redirects=True)
with A.app.app_context():
    db = A.get_db()
    v = db.execute("SELECT town_slug FROM places WHERE id=?",
                   (pids[0],)).fetchone()["town_slug"]
    check("invalid slug ignored", v == "airdrie")

r = c.post(f"/admin/pro/{user}/reassign",
           data={f"place_{pids[0]}": ""}, follow_redirects=True)
with A.app.app_context():
    db = A.get_db()
    v = db.execute("SELECT town_slug FROM places WHERE id=?",
                   (pids[0],)).fetchone()["town_slug"]
    check("untag to main allowed", (v or "") == "")

with c.session_transaction() as s:
    s["user_id"] = user
r = c.get(f"/admin/pro/{user}")
check("non-admin blocked from detail", r.status_code in (302, 403))
r = c.post(f"/admin/pro/{user}/reassign", data={})
check("non-admin blocked from reassign", r.status_code in (302, 403))

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
