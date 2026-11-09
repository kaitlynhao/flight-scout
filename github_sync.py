"""Save and load the tracked-trips list (watchlist.json) straight on GitHub, using GitHub's web API.

Used by the online version of the app (Streamlit Cloud), which can't run git like your laptop does.
Turned on by a GITHUB_TOKEN secret: a GitHub token allowed to read and write this repo's files.
"""
import base64
import json
import os
import time

import requests

API = "https://api.github.com/repos/{repo}/contents/watchlist.json"
DEFAULT_REPO = "kaitlynhao/flight-scout"
_cache = {"data": None, "at": 0.0}
CACHE_SECONDS = 30  # the app redraws on every click, so don't ask GitHub every single time


def enabled():
    return bool(os.getenv("GITHUB_TOKEN", "").strip())


def _url():
    return API.format(repo=os.getenv("GITHUB_REPO", DEFAULT_REPO).strip())


def _headers():
    return {"Authorization": f"Bearer {os.getenv('GITHUB_TOKEN', '').strip()}",
            "Accept": "application/vnd.github+json"}


def _get():
    """Returns (watchlist, sha). sha is GitHub's version id, needed to save over the file."""
    r = requests.get(_url(), headers=_headers(), timeout=15)
    if r.status_code == 404:
        return {"trips": []}, None
    r.raise_for_status()
    body = r.json()
    return json.loads(base64.b64decode(body["content"])), body["sha"]


def load():
    """Latest tracked trips from GitHub (remembered for 30 seconds)."""
    if _cache["data"] is not None and time.time() - _cache["at"] < CACHE_SECONDS:
        return _cache["data"]
    data, _ = _get()
    _cache.update(data=data, at=time.time())
    return data


def save(data, change):
    """Upload the tracked-trips list. Returns (worked, message)."""
    text = json.dumps(data, indent=2) + "\n"
    try:
        for _ in range(2):  # if someone else saved in between, get the new version id and try once more
            _, sha = _get()
            payload = {"message": f"Tracked trips: {change}",
                       "content": base64.b64encode(text.encode()).decode(),
                       "committer": {"name": "Kaitlyn Hao", "email": "kaitlynhao@gmail.com"}}
            if sha:
                payload["sha"] = sha
            r = requests.put(_url(), headers=_headers(), json=payload, timeout=20)
            if r.status_code in (200, 201):
                _cache.update(data=data, at=time.time())
                return True, "Synced to GitHub."
            if r.status_code not in (409, 422):
                break
        return False, "Couldn't save to GitHub right now. Try again in a minute."
    except Exception:
        return False, "Couldn't reach GitHub. Try again in a minute."
