#!/usr/bin/env python3
"""Push townline-web files to GitHub via the Contents API (create or update).

Usage: gh_push.py <file> [<file> ...]   (paths relative to the repo root)
Uses the user-connected `custom.github` credential via authd surrogates.
"""
import base64
import json
import os
import sys
import urllib.request
import urllib.error

sys.path.insert(0, "/opt/hatch/skills/skill-creator/bin")
from dynamic_credentials import add_surrogate_to_request, read_json_response, read_response_body

CRED = "custom.github"
ALLOWED = ("api.github.com",)
API = "https://api.github.com"
REPO = "yuracbt/townline-web"
BRANCH = "main"
LOCAL = "/home/hatch/workspace/townline-web"


def call(method, url, payload=None):
    body = json.dumps(payload).encode() if payload is not None else None
    r = urllib.request.Request(url, data=body, method=method)
    r.add_header("User-Agent", "muse-github-skill/1.0")
    r.add_header("Accept", "application/vnd.github+json")
    if body is not None:
        r.add_header("Content-Type", "application/json")
    add_surrogate_to_request(r, CRED, allowed_hosts=ALLOWED)
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            return resp.status, read_json_response(resp)
    except urllib.error.HTTPError as e:
        try:
            data = json.loads(read_response_body(e).decode("utf-8", "replace"))
        except Exception:
            data = {"message": f"HTTP {e.code}"}
        return e.code, data


def push(path):
    with open(os.path.join(LOCAL, path), "rb") as f:
        content = base64.b64encode(f.read()).decode()
    st, cur = call("GET", f"{API}/repos/{REPO}/contents/{path}?ref={BRANCH}")
    payload = {
        "message": f"Web: {path}",
        "content": content,
        "branch": BRANCH,
    }
    if path.endswith(".sh"):
        payload["mode"] = "100755"
    if st == 404:
        pass  # new file
    elif st != 200:
        print(f"SKIP {path}: HTTP {st}: {cur.get('message')}")
        return False
    else:
        if cur.get("content", "").replace("\n", "") == content:
            print(f"UNCHANGED {path}")
            return True
        payload["sha"] = cur["sha"]
    st, res = call("PUT", f"{API}/repos/{REPO}/contents/{path}", payload)
    if st in (200, 201):
        print(f"PUSHED {path}")
        return True
    print(f"FAIL {path}: HTTP {st}: {res.get('message')}")
    return False


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: gh_push.py <file> [<file> ...]")
        sys.exit(2)
    ok = all(push(p) for p in sys.argv[1:])
    sys.exit(0 if ok else 1)
